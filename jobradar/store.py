"""Persistent state kept as JSON in docs/data/ (committed by the workflow, served by Pages).

jobs.json  - matched jobs shown on the dashboard (plus recently expired, flagged)
seen.json  - every job id ever evaluated, with its verdict, so nothing is re-scored twice
meta.json  - run times, per-source health, history of counts
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .models import Job, dedupe_key

APPLY_RANK = {"easy_apply": 0, "company": 1, "linkedin_unknown": 2, "board": 3, "community": 4}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {j["id"]: j for j in self._load("jobs.json", [])}
        self.seen: dict[str, dict] = self._load("seen.json", {})
        self.meta: dict = self._load("meta.json", {})
        self.by_key = {j["key"]: j["id"] for j in self.jobs.values()}

    def _load(self, name, default):
        p = self.dir / name
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
        except json.JSONDecodeError:
            return default

    # ------------------------------------------------------------------
    def known(self, job_id: str) -> bool:
        return job_id in self.seen or job_id in self.jobs

    def touch(self, job: Job, t: str) -> None:
        """A known job was seen again: refresh last_seen (and upgrade apply info)."""
        rec = self.jobs.get(job.id) or self.jobs.get(self.by_key.get(self.seen.get(job.id, {}).get("key", ""), ""))
        if rec:
            rec["last_seen"] = t
        if job.id in self.seen:
            self.seen[job.id]["t"] = t

    def reject(self, job: Job, reason: str, t: str) -> None:
        self.seen[job.id] = {"t": t, "v": "rejected", "why": reason}

    def add(self, job: Job, facts: dict, t: str) -> dict:
        city = (facts.get("cities") or [""])[0]
        key = dedupe_key(job.company, job.title, city)
        link = {"source": job.source, "url": job.url, "apply_type": job.apply_type,
                "apply_url": job.apply_url}
        self.seen[job.id] = {"t": t, "v": "kept", "key": key}
        if key in self.by_key and self.by_key[key] in self.jobs:  # same job from another source
            rec = self.jobs[self.by_key[key]]
            if all(l["url"] != job.url for l in rec["links"]):
                rec["links"].append(link)
            if APPLY_RANK.get(job.apply_type, 9) < APPLY_RANK.get(rec["apply_type"], 9):
                rec["apply_type"], rec["url"] = job.apply_type, job.url
                rec["apply_url"] = job.apply_url or rec["apply_url"]
            rec["last_seen"] = t
            if len(job.description) > len(rec.get("description", "")):
                rec["description"] = job.description
            return rec
        rec = {**job.to_dict(), "key": key, "links": [link], "first_seen": t, "last_seen": t,
               "cities": facts.get("cities", []), "exp": facts.get("exp", "not_stated"),
               "junior_title": facts.get("junior_title", False), "score": None,
               "role_family": "", "reason": "", "drafts": None, "status": "active"}
        self.jobs[job.id] = rec
        self.by_key[key] = job.id
        return rec

    def drop(self, job_id: str, reason: str) -> None:
        rec = self.jobs.pop(job_id, None)
        if rec:
            self.by_key.pop(rec["key"], None)
            self.seen[job_id] = {"t": rec["last_seen"], "v": "rejected", "why": reason}

    # ------------------------------------------------------------------
    def expire(self, expire_days: int, closed_ids: set[str]) -> int:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=expire_days)
        n = 0
        for jid, rec in list(self.jobs.items()):
            first = dt.datetime.fromisoformat(rec["first_seen"])
            if jid in closed_ids or first < cutoff:
                if rec["status"] == "active":
                    rec["status"] = "expired"
                    n += 1
            if first < cutoff - dt.timedelta(days=14):  # fully forget after 2 more weeks
                self.jobs.pop(jid)
                self.by_key.pop(rec["key"], None)
        seen_cut = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=45)).isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v.get("t", "") >= seen_cut}
        return n

    def save(self) -> None:
        jobs = sorted(self.jobs.values(), key=lambda j: j["first_seen"], reverse=True)
        for j in jobs:
            j["description"] = (j.get("description") or "")[:4000]
        (self.dir / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=0), encoding="utf-8")
        (self.dir / "seen.json").write_text(json.dumps(self.seen, separators=(",", ":")), encoding="utf-8")
        (self.dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")
