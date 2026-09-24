"""Company career sites via their public ATS job-board APIs.

Paste a careers URL in config.yaml; `detect()` works out which ATS it is.
All of these are public, documented (or widely used) read-only endpoints.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from urllib.parse import urlparse

from ..http import pause, session
from ..models import Job

TODAY = dt.date.today


def detect(url: str) -> dict | None:
    u = urlparse(url.strip())
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    if "greenhouse.io" in host and parts:
        return {"ats": "greenhouse", "token": parts[-1] if host.startswith("boards-api") else parts[0]}
    if "lever.co" in host and parts:
        return {"ats": "lever", "token": parts[0], "eu": ".eu." in host}
    if "ashbyhq.com" in host and parts:
        return {"ats": "ashby", "token": parts[0]}
    if "smartrecruiters.com" in host and parts:
        return {"ats": "smartrecruiters", "token": parts[0]}
    if "myworkdayjobs.com" in host:
        site = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        if site:
            return {"ats": "workday", "host": host, "tenant": host.split(".")[0], "site": site[0]}
    return None


def strip_html(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def _date(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        ts = v / 1000 if v > 1e11 else v
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
    return str(v)[:10]


# --------------------------------------------------------------------------- APIs

def greenhouse(token: str, s) -> list[Job]:
    r = s.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        company = (j.get("company_name") or token).strip()
        out.append(Job(
            id=f"gh-{token}-{j['id']}", source="greenhouse", title=j.get("title", ""),
            company=company, location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""), apply_type="company", apply_url=j.get("absolute_url", ""),
            posted_at=_date(j.get("first_published") or j.get("updated_at")),
            description=strip_html(j.get("content", ""))[:6000]))
    return out


def lever(token: str, s, eu: bool = False) -> list[Job]:
    base = "https://api.eu.lever.co" if eu else "https://api.lever.co"
    r = s.get(f"{base}/v0/postings/{token}?mode=json", timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        loc = cat.get("location") or ", ".join(cat.get("allLocations") or [])
        out.append(Job(
            id=f"lv-{token}-{j['id']}", source="lever", title=j.get("text", ""), company=token,
            location=loc, url=j.get("hostedUrl", ""), apply_type="company",
            apply_url=j.get("applyUrl") or j.get("hostedUrl", ""), posted_at=_date(j.get("createdAt")),
            is_remote=(j.get("workplaceType") == "remote"),
            description=(j.get("descriptionPlain") or "")[:6000]))
    return out


def ashby(token: str, s) -> list[Job]:
    r = s.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
        out.append(Job(
            id=f"ab-{token}-{j['id']}", source="ashby", title=j.get("title", ""), company=token,
            location=", ".join(l for l in locs if l), url=j.get("jobUrl", ""), apply_type="company",
            apply_url=j.get("applyUrl") or j.get("jobUrl", ""), posted_at=_date(j.get("publishedAt")),
            is_remote=bool(j.get("isRemote")), description=(j.get("descriptionPlain") or "")[:6000]))
    return out


def smartrecruiters(token: str, s) -> list[Job]:
    out, offset = [], 0
    while offset < 500:
        r = s.get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
                  params={"limit": 100, "offset": offset}, timeout=30)
        r.raise_for_status()
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            url = f"https://jobs.smartrecruiters.com/{token}/{j['id']}"
            out.append(Job(
                id=f"sr-{token}-{j['id']}", source="smartrecruiters", title=j.get("name", ""),
                company=(j.get("company") or {}).get("name") or token,
                location=", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x),
                url=url, apply_type="company", apply_url=url, posted_at=_date(j.get("releasedDate")),
                is_remote=bool(loc.get("remote"))))
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


def smartrecruiters_detail(job: Job, s) -> None:
    token, pid = job.id.split("-", 2)[1:]
    r = s.get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{pid}", timeout=30)
    if r.ok:
        secs = (r.json().get("jobAd") or {}).get("sections") or {}
        job.description = strip_html(" ".join((v or {}).get("text", "") for v in secs.values()))[:6000]


_WD_POSTED = re.compile(r"(\d+)\+?\s+days?", re.I)


def _wd_date(text: str) -> str | None:
    t = (text or "").lower()
    if "today" in t:
        return TODAY().isoformat()
    if "yesterday" in t:
        return (TODAY() - dt.timedelta(days=1)).isoformat()
    m = _WD_POSTED.search(t)
    return (TODAY() - dt.timedelta(days=int(m.group(1)))).isoformat() if m else None


def workday(host: str, tenant: str, site: str, s, search_terms: list[str]) -> list[Job]:
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    seen, out = set(), []
    for term in search_terms:
        for offset in (0, 20):
            r = s.post(api, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term},
                       headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30)
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            for j in posts:
                path = j.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                url = f"https://{host}/{site}{path}"
                out.append(Job(
                    id=f"wd-{tenant}-{path.rsplit('_', 1)[-1] if '_' in path else hashlib.sha1(path.encode()).hexdigest()[:10]}",
                    source="workday", title=j.get("title", ""), company=tenant.capitalize(),
                    location=j.get("locationsText", ""), url=url, apply_type="company", apply_url=url,
                    posted_at=_wd_date(j.get("postedOn", ""))))
            if len(posts) < 20:
                break
            pause(0.5, 1.2)
    return out


def workday_detail(job: Job, s) -> None:
    u = urlparse(job.url)
    host, parts = u.netloc, u.path.strip("/").split("/", 1)
    tenant = host.split(".")[0]
    r = s.get(f"https://{host}/wday/cxs/{tenant}/{parts[0]}/{parts[1]}",
              headers={"Accept": "application/json"}, timeout=30)
    if r.ok:
        info = r.json().get("jobPostingInfo") or {}
        job.description = strip_html(info.get("jobDescription", ""))[:6000]
        job.location = info.get("location") or job.location
        org = (r.json().get("hiringOrganization") or {}).get("name")
        if org:
            job.company = org


# --------------------------------------------------------------------------- runner

def fetch_company(url: str, cfg: dict) -> list[Job]:
    d = detect(url)
    if not d:
        raise ValueError("unrecognized careers URL (supported: Greenhouse, Lever, Ashby, SmartRecruiters, Workday)")
    s = session()
    if d["ats"] == "greenhouse":
        return greenhouse(d["token"], s)
    if d["ats"] == "lever":
        return lever(d["token"], s, d.get("eu", False))
    if d["ats"] == "ashby":
        return ashby(d["token"], s)
    if d["ats"] == "smartrecruiters":
        return smartrecruiters(d["token"], s)
    terms = ["engineer", "graduate", "data", "machine learning", "developer"]
    return workday(d["host"], d["tenant"], d["site"], s, terms)
