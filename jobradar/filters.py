"""Deterministic filters. Cheap, explainable, and applied before any AI call."""
from __future__ import annotations

import datetime as dt
import re

from .models import Job

# ------------------------------------------------------------------ title

def compile_roles(cfg: dict) -> tuple[list[re.Pattern], list[re.Pattern]]:
    inc = [re.compile(p, re.I) for p in cfg["roles"]["include"]]
    exc = [re.compile(p, re.I) for p in cfg["roles"]["exclude"]]
    return inc, exc


def title_ok(title: str, inc, exc) -> tuple[bool, str]:
    if any(p.search(title) for p in exc):
        return False, "excluded title"
    if not any(p.search(title) for p in inc):
        return False, "role not targeted"
    return True, ""


BAD_SENIORITY = re.compile(r"mid-senior|director|executive", re.I)

# ------------------------------------------------------------------ experience

FRESH = re.compile(r"\b(freshers?|new grad(uate)?s?|entry[- ]level|recent (college )?graduates?|"
                   r"campus hir\w+|graduate engineer trainee|early[- ]career|no (prior )?experience "
                   r"(is )?required|batch of 20(2[4-7]))\b", re.I)
RANGE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
PLUS = re.compile(r"(?:minimum(?: of)?|min\.?|at least|atleast)?\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
EXP_CTX = re.compile(r"experience|exp\b|expertise|working in|background", re.I)
JUNIOR_TITLE = re.compile(r"\b(junior|jr\.?|graduate|trainee|fresher|entry|new grad|associate|"
                          r"(engineer|developer|sde|swe)[- ]?(i|1)\b)", re.I)


def parse_experience(job: Job) -> tuple[str, float | None]:
    """Return (label, minimum years). Labels: fresher | 0-1 | 2+ | not_stated."""
    if job.experience_text:  # Naukri gives "0-2 Yrs" directly
        m = RANGE.search(job.experience_text) or PLUS.search(job.experience_text)
        if m:
            lo = float(m.group(1))
            return _label(lo), lo
    text = f"{job.title}\n{job.description}"
    if FRESH.search(text):
        mins = _mins(job.description)
        if not mins or min(mins) <= 1:
            return "fresher", 0.0
    mins = _mins(job.description)
    if mins:
        lo = min(mins)
        return _label(lo), lo
    return "not_stated", None


def _mins(desc: str) -> list[float]:
    mins = []
    for sent in re.split(r"(?<=[.;\n])\s+|\n|•", desc or ""):
        if not EXP_CTX.search(sent):
            continue
        for m in RANGE.finditer(sent):
            mins.append(float(m.group(1)))
        if not RANGE.search(sent):
            for m in PLUS.finditer(sent):
                n = float(m.group(1))
                if n <= 15:
                    mins.append(n)
    return mins


def _label(lo: float) -> str:
    if lo < 1:
        return "fresher"
    if lo <= 1:
        return "0-1"
    return "2+"


# ------------------------------------------------------------------ location

NON_INDIA = re.compile(r"\b(us|usa|u\.s\.|united states|canada|uk|united kingdom|europe|eu|emea|latam|"
                       r"americas|north america|germany|france|spain|netherlands|poland|brazil|mexico|"
                       r"australia|japan|singapore only|us only|us-only|est|pst|cet)\b", re.I)
OPEN_REMOTE = re.compile(r"\b(india|asia|apac|anywhere|worldwide|global|any location|all countries)\b", re.I)
REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere|distributed)\b", re.I)


def location_tags(job: Job, cfg: dict) -> tuple[bool, list[str]]:
    loc = (job.location or "").lower()
    tags = [g for g, aliases in cfg["locations"]["groups"].items()
            if any(re.search(r"\b" + re.escape(a) + r"\b", loc) for a in aliases)]
    remote = job.is_remote or bool(REMOTE.search(loc))
    if remote and cfg["locations"].get("remote", True):
        restricted = NON_INDIA.search(loc) and not OPEN_REMOTE.search(loc)
        if not restricted:
            tags.append("Remote")
    if tags:
        return True, tags
    if cfg["locations"].get("allow_india_unspecified", True) and re.fullmatch(r"\s*india\s*", loc):
        return True, ["India"]
    if job.source in ("hackernews", "reddit") and remote:
        return True, ["Remote"]  # the AI classifier checks eligibility from the post text
    return False, []


# ------------------------------------------------------------------ age

def age_days(posted_at: str | None) -> int | None:
    if not posted_at:
        return None
    try:
        d = dt.date.fromisoformat(str(posted_at)[:10])
    except ValueError:
        return None
    return (dt.date.today() - d).days


def rule_check(job: Job, cfg: dict, inc, exc) -> tuple[bool, str, dict]:
    """Full rule pass. Returns (keep, reject_reason, facts)."""
    ok, why = title_ok(job.title, inc, exc)
    if not ok and job.source not in ("hackernews", "reddit"):
        return False, why, {}
    if job.seniority and BAD_SENIORITY.search(job.seniority):
        return False, f"seniority: {job.seniority}", {}
    ok, tags = location_tags(job, cfg)
    if not ok:
        return False, "location", {}
    age = age_days(job.posted_at)
    if age is not None and age > cfg["max_age_days"]:
        return False, "too old", {}
    label, lo = parse_experience(job)
    if label not in cfg["experience"]["allowed"]:
        return False, f"experience {label}", {}
    junior_hint = bool(JUNIOR_TITLE.search(job.title))
    return True, "", {"cities": tags, "exp": label, "exp_min": lo, "junior_title": junior_hint}
