"""LinkedIn public (logged-out) job search.

No LinkedIn account or cookie is ever used, so nothing here can restrict your profile.
Search:  /jobs-guest/jobs/api/seeMoreJobPostings/search  (HTML fragments of job cards)
Detail:  /jobs/view/<id>  public page; an offsite job carries <code id="applyUrl">, an Easy Apply
         job does not. That is how jobs are split into "Easy Apply" vs "Company site".
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from ..http import pause, session
from ..models import Job
from .ats import strip_html

BASE = "https://www.linkedin.com"


def parse_cards(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("div.base-card, li div.job-search-card"):
        urn = card.get("data-entity-urn", "")
        m = re.search(r"(\d{6,})", urn)
        link = card.select_one("a.base-card__full-link")
        if not m and link:
            m = re.search(r"-(\d{6,})\?", link.get("href", "")) or re.search(r"(\d{6,})", link.get("href", ""))
        if not m:
            continue
        jid = m.group(1)
        title = card.select_one("h3.base-search-card__title")
        comp = card.select_one("h4.base-search-card__subtitle")
        loc = card.select_one("span.job-search-card__location")
        t = card.select_one("time")
        comp_a = comp.select_one("a") if comp else None
        out.append(Job(
            id=f"li-{jid}", source="linkedin",
            title=title.get_text(strip=True) if title else "",
            company=comp.get_text(strip=True) if comp else "",
            location=loc.get_text(strip=True) if loc else "",
            url=f"{BASE}/jobs/view/{jid}", apply_type="linkedin_unknown",
            posted_at=t.get("datetime") if t else None,
            company_url=(comp_a.get("href", "").split("?")[0] if comp_a else "")))
    return out


def search(terms: list[str], location: str, *, remote: bool = False, pages: int = 2,
           hours: int = 26, exp_levels: str = "1,2") -> list[Job]:
    s = session()
    found: dict[str, Job] = {}
    for term in terms:
        for page in range(pages):
            params = {"keywords": term, "location": location, "f_TPR": f"r{hours * 3600}",
                      "start": page * 25}
            if exp_levels:
                params["f_E"] = exp_levels
            if remote:
                params["f_WT"] = "2"
            r = s.get(f"{BASE}/jobs-guest/jobs/api/seeMoreJobPostings/search", params=params, timeout=30)
            if r.status_code == 429:
                raise RuntimeError("LinkedIn rate-limited this runner (HTTP 429); will retry next run")
            r.raise_for_status()
            cards = parse_cards(r.text)
            for j in cards:
                j.is_remote = remote or "remote" in j.location.lower()
                found.setdefault(j.id, j)
            pause(1.5, 3.5)
            if len(cards) < 25:
                break
    return list(found.values())


def parse_detail(html: str, job: Job) -> bool:
    """Fill description, seniority, and apply type from the public job page."""
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one("div.show-more-less-html__markup, div.description__text")
    if desc is None:
        return False
    job.description = strip_html(str(desc))[:6000]
    for li in soup.select("li.description__job-criteria-item"):
        h = li.select_one("h3")
        v = li.select_one("span")
        if h and v and "seniority" in h.get_text(strip=True).lower():
            job.seniority = v.get_text(strip=True)
    code = soup.find("code", id="applyUrl")
    if code:
        m = re.search(r'(?<=\?url=)[^"&]+', code.decode_contents())
        job.apply_url = unquote(m.group(0)) if m else ""
        job.apply_type = "company"
    else:
        job.apply_type = "easy_apply"
    return True


def enrich(jobs: list[Job], limit: int) -> tuple[int, int]:
    """Open each job page (capped) to detect Easy Apply and read the description."""
    s = session()
    ok = fail = 0
    for job in jobs[:limit]:
        try:
            r = s.get(job.url, timeout=20)
            if r.status_code == 429:
                break  # stop politely; remaining jobs stay "linkedin_unknown"
            if r.ok and "linkedin.com/signup" not in r.url and parse_detail(r.text, job):
                ok += 1
            else:
                fail += 1
        except Exception:  # noqa: BLE001
            fail += 1
        pause(1.2, 2.8)
    return ok, fail
