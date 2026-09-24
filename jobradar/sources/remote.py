"""Remote job boards with public APIs/feeds: RemoteOK, Remotive, Himalayas, We Work Remotely."""
from __future__ import annotations

import datetime as dt
import email.utils
import xml.etree.ElementTree as ET

from ..http import pause, session
from ..models import Job
from .ats import _date, strip_html


def remoteok(s) -> list[Job]:
    r = s.get("https://remoteok.com/api", timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json():
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is the legal notice
        out.append(Job(
            id=f"rok-{j.get('id')}", source="remoteok", title=j.get("position", ""),
            company=j.get("company", ""), location=j.get("location") or "Remote",
            url=j.get("url", ""), apply_type="company", apply_url=j.get("apply_url") or j.get("url", ""),
            posted_at=_date(j.get("date")), is_remote=True,
            description=strip_html(j.get("description", ""))[:6000]))
    return out


def remotive(s) -> list[Job]:
    out = []
    for cat in ("software-dev", "data"):
        r = s.get("https://remotive.com/api/remote-jobs", params={"category": cat, "limit": 100}, timeout=30)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            out.append(Job(
                id=f"rmt-{j.get('id')}", source="remotive", title=j.get("title", ""),
                company=j.get("company_name", ""),
                location=f"Remote ({j.get('candidate_required_location') or 'Anywhere'})",
                url=j.get("url", ""), apply_type="company", apply_url=j.get("url", ""),
                posted_at=_date(j.get("publication_date")), is_remote=True,
                description=strip_html(j.get("description", ""))[:6000]))
        pause(1, 2)
    return out


def himalayas(s, pages: int = 5) -> list[Job]:
    out = []
    for p in range(pages):
        r = s.get("https://himalayas.app/jobs/api", params={"limit": 20, "offset": p * 20}, timeout=30)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        for j in jobs:
            restr = j.get("locationRestrictions") or []
            out.append(Job(
                id=f"him-{j.get('guid') or j.get('applicationLink')}", source="himalayas",
                title=j.get("title", ""), company=j.get("companyName", ""),
                location="Remote (" + (", ".join(restr) if restr else "Anywhere") + ")",
                url=j.get("applicationLink") or j.get("guid", ""), apply_type="company",
                apply_url=j.get("applicationLink", ""), posted_at=_date(j.get("pubDate")),
                is_remote=True, description=strip_html(j.get("description", ""))[:6000],
                seniority=", ".join(j.get("seniority") or [])))
        if len(jobs) < 20:
            break
        pause(1, 2)
    return out


def weworkremotely(s) -> list[Job]:
    r = s.get("https://weworkremotely.com/categories/remote-programming-jobs.rss", timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        raw = (it.findtext("title") or "")
        company, _, title = raw.partition(":")
        if not title:
            company, title = "", raw
        link = it.findtext("link") or ""
        pub = it.findtext("pubDate")
        posted = email.utils.parsedate_to_datetime(pub).date().isoformat() if pub else None
        region = it.findtext("region") or "Anywhere"
        out.append(Job(
            id=f"wwr-{link.rstrip('/').rsplit('/', 1)[-1]}", source="weworkremotely",
            title=title.strip(), company=company.strip(), location=f"Remote ({region})", url=link,
            apply_type="company", apply_url=link, posted_at=posted, is_remote=True,
            description=strip_html(it.findtext("description") or "")[:6000]))
    return out


def all_remote() -> tuple[list[Job], list[dict]]:
    s = session()
    jobs, health = [], []
    for name, fn in (("remoteok", remoteok), ("remotive", remotive),
                     ("himalayas", himalayas), ("weworkremotely", weworkremotely)):
        t0 = dt.datetime.now()
        try:
            got = fn(s)
            jobs += got
            health.append({"source": name, "ok": True, "count": len(got)})
        except Exception as e:  # noqa: BLE001
            health.append({"source": name, "ok": False, "count": 0, "error": str(e)[:200]})
        health[-1]["secs"] = round((dt.datetime.now() - t0).total_seconds(), 1)
    return jobs, health
