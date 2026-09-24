"""The normalized job record every source produces."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

APPLY_TYPES = ("easy_apply", "company", "board", "linkedin_unknown", "community")


@dataclass
class Job:
    id: str                    # source-unique id, e.g. "li-3981234", "gh-databricks-123"
    source: str                # linkedin | indeed | naukri | greenhouse | ... | hackernews | reddit
    title: str
    company: str
    location: str
    url: str                   # page to open
    apply_type: str = "board"  # one of APPLY_TYPES
    apply_url: str = ""        # direct company apply link when known
    posted_at: str | None = None   # ISO date (YYYY-MM-DD) or datetime
    is_remote: bool = False
    description: str = ""
    seniority: str = ""        # e.g. LinkedIn "Entry level"
    experience_text: str = ""  # e.g. Naukri "0-2 Yrs"
    company_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(pvt|private|ltd|limited|inc|llc|llp|technologies|technology|india|solutions)\b\.?", "", s)
    return _WS.sub(" ", re.sub(r"[^a-z0-9+#/ ]", " ", s)).strip()


def dedupe_key(company: str, title: str, city: str) -> str:
    raw = f"{norm(company)}|{norm(title)}|{norm(city)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
