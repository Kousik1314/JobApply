"""Load config.yaml and the candidate profile."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    cfg.setdefault("roles", {}).setdefault("include", [])
    cfg["roles"].setdefault("exclude", [])
    cfg.setdefault("experience", {}).setdefault("allowed", ["fresher", "0-1", "not_stated"])
    cfg.setdefault("locations", {}).setdefault("groups", {})
    cfg["locations"].setdefault("remote", True)
    cfg["locations"].setdefault("allow_india_unspecified", True)
    cfg.setdefault("max_age_days", 14)
    cfg.setdefault("expire_days", 21)
    cfg.setdefault("sources", {})
    cfg.setdefault("companies", [])
    llm = cfg.setdefault("llm", {})
    llm.setdefault("classify_model", "gpt-5.4-mini")
    llm.setdefault("draft_model", "gpt-5.4")
    llm.setdefault("min_score", 55)
    llm.setdefault("draft_min_score", 70)
    llm.setdefault("max_drafts_per_run", 30)
    llm.setdefault("note_chars", 200)
    return cfg


def load_profile() -> str:
    env = os.getenv("CANDIDATE_PROFILE", "").strip()
    if env:
        return env
    p = ROOT / "profile.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def profile_is_filled(profile: str) -> bool:
    return bool(profile.strip()) and "YOUR NAME" not in profile
