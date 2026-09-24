"""Indeed India and Naukri via the open-source JobSpy library."""
from __future__ import annotations

import math

from ..models import Job


def _v(row, key, default=""):
    v = row.get(key, default)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return v


def jobspy_search(site: str, terms: list[str], results: int, hours: int = 26) -> list[Job]:
    from jobspy import scrape_jobs  # imported lazily: heavy dependency

    found: dict[str, Job] = {}
    for term in terms:
        kw = dict(site_name=[site], search_term=term, results_wanted=results, hours_old=hours,
                  description_format="markdown", verbose=0)
        if site == "indeed":
            kw.update(location="India", country_indeed="India")
        df = scrape_jobs(**kw)
        for row in df.to_dict("records"):
            jid = f"{site}-{_v(row, 'id') or _v(row, 'job_url')}"
            direct = _v(row, "job_url_direct")
            found.setdefault(jid, Job(
                id=jid, source=site, title=str(_v(row, "title")), company=str(_v(row, "company")),
                location=str(_v(row, "location")), url=str(_v(row, "job_url")),
                apply_type="company" if direct else "board", apply_url=str(direct),
                posted_at=str(_v(row, "date_posted"))[:10] or None,
                is_remote=bool(_v(row, "is_remote", False)),
                description=str(_v(row, "description"))[:6000],
                experience_text=str(_v(row, "experience_range")),
                company_url=str(_v(row, "company_url"))))
    return list(found.values())
