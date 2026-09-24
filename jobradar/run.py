"""One refresh: fetch every source → filter → enrich → classify → draft → save.

    python -m jobradar.run                 # full run
    python -m jobradar.run --no-ai         # rules only
    python -m jobradar.run --only linkedin,companies
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import time
from concurrent.futures import ThreadPoolExecutor

from .config import DATA, load_config, load_profile, profile_is_filled
from .filters import compile_roles, parse_experience, rule_check
from .models import Job
from .store import Store, now_iso

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(IST):%H:%M:%S}] {msg}", flush=True)


def timed(name: str, fn, *a, **k) -> tuple[list[Job], dict]:
    t0 = time.time()
    try:
        got = fn(*a, **k)
        h = {"source": name, "ok": True, "count": len(got)}
    except Exception as e:  # noqa: BLE001
        got, h = [], {"source": name, "ok": False, "count": 0, "error": str(e)[:240]}
    h["secs"] = round(time.time() - t0, 1)
    log(f"{name}: {'ok' if h['ok'] else 'FAILED'} {h['count']} jobs ({h['secs']}s)"
        + (f" - {h.get('error')}" if not h["ok"] else ""))
    return got, h


# ------------------------------------------------------------------ collection

def collect(cfg: dict, only: set[str] | None, known=lambda _id: False,
            title_ok=lambda _t: True) -> tuple[list[Job], list[dict], dict[str, set[str]]]:
    from .sources import ats, boards, community, linkedin, remote, sites
    src = cfg["sources"]
    want = lambda k: (only is None or k in only) and src.get(k, {}).get("enabled", True)  # noqa: E731
    jobs: list[Job] = []
    health: list[dict] = []
    ats_ids: dict[str, set[str]] = {}   # company url -> ids currently listed (to detect closed jobs)

    if only is None or "companies" in only:
        def one(url):
            got, h = timed(f"company: {url.split('//')[-1][:60]}", ats.fetch_company, url, cfg)
            return url, got, h
        with ThreadPoolExecutor(max_workers=6) as pool:
            for url, got, h in pool.map(one, cfg["companies"]):
                jobs += got
                health.append(h)
                if h["ok"]:
                    ats_ids[url] = {j.id for j in got}

    if want("linkedin"):
        li = src["linkedin"]
        got, h = timed("linkedin (India)", linkedin.search, li["terms"], li.get("location", "India"),
                       pages=li.get("pages", 2), exp_levels=li.get("experience_levels", "1,2"))
        jobs += got
        health.append(h)
        if li.get("remote_terms"):
            got, h = timed("linkedin (remote, worldwide)", linkedin.search, li["remote_terms"], "Worldwide",
                           remote=True, pages=1, exp_levels=li.get("experience_levels", "1,2"))
            jobs += got
            health.append(h)

    for site in ("indeed", "naukri"):
        if want(site):
            got, h = timed(site, boards.jobspy_search, site, src[site]["terms"], src[site].get("results", 40))
            jobs += got
            health.append(h)

    if want("remote_boards"):
        got, hs = remote.all_remote()
        for h in hs:
            log(f"{h['source']}: {'ok' if h['ok'] else 'FAILED'} {h['count']} jobs ({h['secs']}s)"
                + (f" - {h.get('error')}" if not h["ok"] else ""))
        jobs += got
        health += hs
    if want("hackernews"):
        got, h = timed("hackernews", community.hackernews)
        jobs += got
        health.append(h)
    if want("reddit"):
        got, h = timed("reddit", community.reddit, src["reddit"].get("subreddits", []))
        jobs += got
        health.append(h)
    if (only is None or "sites" in only) and cfg.get("sites"):
        got, hs = sites.all_sites(cfg, known, title_ok)
        for h in hs:
            log(f"{h['source']}: {'ok' if h['ok'] else 'FAILED'} {h['count']} jobs ({h['secs']}s)"
                + (f" - {h.get('error')}" if not h["ok"] else ""))
        jobs += got
        health += hs
    return jobs, health, ats_ids


# ------------------------------------------------------------------ main

def run(no_ai: bool = False, only: set[str] | None = None) -> dict:
    cfg = load_config()
    store = Store(DATA)
    t = now_iso()
    inc, exc = compile_roles(cfg)
    log("JobRadar refresh started")

    from .filters import title_ok
    raw, health, ats_ids = collect(cfg, only, known=store.known,
                                   title_ok=lambda t: title_ok(t, inc, exc)[0])
    uniq: dict[str, Job] = {}
    for j in raw:
        if j.id and j.title:
            uniq.setdefault(j.id, j)
    log(f"Fetched {len(raw)} postings ({len(uniq)} unique)")

    # 1) rules on new postings only (pre-enrichment pass uses title/location/age)
    fresh: list[tuple[Job, dict]] = []
    for j in uniq.values():
        if store.known(j.id):
            store.touch(j, t)
            continue
        keep, why, facts = rule_check(j, cfg, inc, exc)
        if keep:
            fresh.append((j, facts))
        else:
            store.reject(j, why, t)
    log(f"{len(fresh)} new postings passed the rule filter")

    # 2) enrich LinkedIn (Easy Apply detection + description) and company detail pages
    from .sources import ats, linkedin
    li_jobs = [j for j, _ in fresh if j.source == "linkedin"]
    if li_jobs:
        lim = cfg["sources"].get("linkedin", {}).get("enrich_limit", 120)
        ok, fail = linkedin.enrich(li_jobs, lim)
        log(f"LinkedIn job pages: {ok} read, {fail} failed, {max(0, len(li_jobs) - lim)} over limit")
    from .http import session
    s = session()
    for j, _ in fresh:
        try:
            if j.source == "workday" and not j.description:
                ats.workday_detail(j, s)
            elif j.source == "smartrecruiters" and not j.description:
                ats.smartrecruiters_detail(j, s)
        except Exception:  # noqa: BLE001
            pass

    # 3) second rule pass now that descriptions/seniority are known
    kept_map: dict[str, dict] = {}
    for j, facts in fresh:
        keep, why, facts2 = rule_check(j, cfg, inc, exc)
        if not keep:
            store.reject(j, why, t)
            continue
        rec = store.add(j, facts2 or facts, t)
        if rec.get("score") is None:          # merged duplicates of scored jobs are not re-scored
            kept_map[rec["id"]] = rec
    kept = list(kept_map.values())
    log(f"{len(kept)} new jobs kept after enrichment")

    # 4) AI classification + drafts
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    profile = load_profile()
    ai_calls = 0
    if api_key and not no_ai and kept:
        from .ai import AI
        ai = AI(api_key, cfg, profile if profile_is_filled(profile) else "", log=log)
        verdicts = ai.classify(kept)
        allowed = set(cfg["experience"]["allowed"])
        dropped = 0
        for rec in kept:
            v = verdicts.get(rec["id"])
            if not v:
                continue
            rec.update(score=v.score, role_family=v.role_family, reason=v.reason)
            if rec["exp"] == "not_stated" and v.experience != "not_stated":
                rec["exp"] = v.experience
            if not v.relevant or not v.location_ok or v.experience not in allowed or v.score < cfg["llm"]["min_score"]:
                store.drop(rec["id"], f"ai: {v.reason}")
                dropped += 1
        log(f"AI classified {len(verdicts)} jobs, dropped {dropped}")

        if profile_is_filled(profile):
            todo = sorted((r for r in store.jobs.values()
                           if r["status"] == "active" and not r.get("drafts") and r["apply_type"] != "community"
                           and (r.get("score") or 0) >= cfg["llm"]["draft_min_score"]),
                          key=lambda r: -(r.get("score") or 0))[: cfg["llm"]["max_drafts_per_run"]]
            for rec in todo:
                try:
                    rec["drafts"] = ai.draft(rec)
                except Exception as e:  # noqa: BLE001
                    log(f"Draft failed for {rec['company']}: {e}")
            log(f"Wrote outreach drafts for {len(todo)} jobs")
        else:
            log("profile.md not filled in (or CANDIDATE_PROFILE secret missing): skipping drafts")
        ai_calls = ai.calls
    elif not api_key:
        log("No OPENAI_API_KEY: rules-only mode (no scores or drafts)")

    # 5) expire closed/old jobs, save
    closed = set()
    for url, ids in ats_ids.items():
        from .sources.ats import detect
        d = detect(url) or {}
        prefix = {"greenhouse": "gh-", "lever": "lv-", "ashby": "ab-", "smartrecruiters": "sr-",
                  "workday": "wd-"}.get(d.get("ats", ""), "?")
        tag = d.get("token") or d.get("tenant")
        closed |= {jid for jid in store.jobs if jid.startswith(f"{prefix}{tag}-") and jid not in ids
                   and d.get("ats") != "workday"}  # workday search is partial; don't infer closure
    expired = store.expire(cfg["expire_days"], closed)

    active = [r for r in store.jobs.values() if r["status"] == "active"]
    new_ids = [r["id"] for r in active if r["first_seen"] == t]
    m = store.meta
    m["prev_run"] = m.get("last_run")
    m["last_run"] = t
    m["health"] = health
    m["stats"] = {"fetched": len(uniq), "new": len(new_ids), "active": len(active), "expired_now": expired,
                  "ai_calls": ai_calls,
                  "by_type": {k: sum(r["apply_type"] == k for r in active)
                              for k in ("easy_apply", "company", "linkedin_unknown", "board", "community")}}
    m["history"] = (m.get("history", []) + [{"t": t, "new": len(new_ids), "active": len(active)}])[-60:]
    m["profile_ready"] = profile_is_filled(profile)
    m["note_chars"] = cfg["llm"]["note_chars"]
    store.save()
    log(f"Done: {len(new_ids)} new, {len(active)} active, {expired} expired")
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--only", default="", help="comma list: companies,linkedin,indeed,naukri,remote_boards,hackernews,reddit,sites")
    a = ap.parse_args()
    run(no_ai=a.no_ai, only=set(a.only.split(",")) if a.only else None)


if __name__ == "__main__":
    main()
