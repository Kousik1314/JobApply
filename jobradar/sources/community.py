"""Community sources: Hacker News "Who is hiring?" and Reddit hiring posts."""
from __future__ import annotations

import os
import re

from ..http import session
from ..models import Job
from .ats import _date, strip_html

HN = "https://hn.algolia.com/api/v1"
ROLEY = re.compile(r"engineer|developer|scientist|machine learning|\bml\b|\bai\b|data", re.I)
PLACEY = re.compile(r"remote|india|bangalore|bengaluru|delhi|noida|gurgaon|gurugram|pune|mumbai|kolkata|anywhere|worldwide|global", re.I)


def hackernews() -> list[Job]:
    s = session()
    r = s.get(f"{HN}/search_by_date", params={"tags": "story,author_whoishiring",
                                               "query": "who is hiring", "hitsPerPage": 5}, timeout=30)
    r.raise_for_status()
    story = next((h for h in r.json().get("hits", [])
                  if h.get("title", "").lower().startswith("ask hn: who is hiring")), None)
    if not story:
        return []
    r = s.get(f"{HN}/items/{story['objectID']}", timeout=60)
    r.raise_for_status()
    out = []
    for c in r.json().get("children", []):
        text = strip_html(c.get("text") or "")
        if not text or not ROLEY.search(text) or not PLACEY.search(text):
            continue
        first = text.split("\n", 1)[0]
        bits = [b.strip() for b in first.split("|")]
        company = bits[0][:80] if bits else "HN post"
        title = next((b for b in bits[1:] if ROLEY.search(b)), first[:120])
        location = next((b for b in bits[1:] if PLACEY.search(b)), "See post")
        url = f"https://news.ycombinator.com/item?id={c['id']}"
        out.append(Job(id=f"hn-{c['id']}", source="hackernews", title=title[:140], company=company,
                       location=location[:120], url=url, apply_type="community",
                       posted_at=_date(c.get("created_at")), is_remote=bool(re.search("remote", first, re.I)),
                       description=text[:6000]))
    return out


def reddit(subs: list[str]) -> list[Job]:
    cid, secret = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        raise RuntimeError("skipped: add REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET secrets to enable")
    s = session()
    s.headers["User-Agent"] = "jobradar/1.0 (personal job search)"
    tok = s.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret),
                 data={"grant_type": "client_credentials"}, timeout=30)
    tok.raise_for_status()
    s.headers["Authorization"] = f"bearer {tok.json()['access_token']}"
    out = []
    for sub in subs:
        r = s.get(f"https://oauth.reddit.com/r/{sub}/new", params={"limit": 100}, timeout=30)
        r.raise_for_status()
        for p in r.json().get("data", {}).get("children", []):
            d = p.get("data", {})
            title = d.get("title", "")
            flair = (d.get("link_flair_text") or "").lower()
            if not (re.search(r"\bhiring\b", title, re.I) or "hiring" in flair):
                continue
            if re.search(r"for hire|\[for hire\]|looking for (a )?job", title, re.I):
                continue
            body = d.get("selftext", "")
            out.append(Job(id=f"rd-{d.get('id')}", source="reddit", title=title[:140],
                           company=f"r/{sub}", location="See post",
                           url=f"https://www.reddit.com{d.get('permalink', '')}", apply_type="community",
                           posted_at=_date(d.get("created_utc")),
                           is_remote=bool(re.search("remote", title + body, re.I)), description=body[:6000]))
    return out
