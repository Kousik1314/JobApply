"""OpenAI layer: relevance/experience classifier and cross-checked outreach drafts."""
from __future__ import annotations

import json
import re
import time
from typing import Literal

from pydantic import BaseModel


# ------------------------------------------------------------------ schemas

class Verdict(BaseModel):
    id: str
    relevant: bool
    role_family: Literal["AI/ML", "GenAI/LLM", "Software", "Data Engineering", "Data Science", "Other"]
    experience: Literal["fresher", "0-1", "2+", "not_stated"]
    location_ok: bool
    score: int
    reason: str


class Verdicts(BaseModel):
    items: list[Verdict]


class Drafts(BaseModel):
    note: str
    dm: str
    email_subject: str
    email_body: str


class Check(BaseModel):
    ok: bool
    problems: list[str]


# ------------------------------------------------------------------ prompts

CLASSIFY = """You screen job postings for one candidate: a BTech Computer Science graduate (2026)
with 0–1 year of experience, targeting AI/ML, GenAI/LLM, software engineering, data engineering,
and data science roles. Acceptable locations: Delhi NCR (Delhi, Noida, Gurgaon), Bengaluru, Mumbai,
Pune, Kolkata, or fully remote roles that can hire someone living in India.

For EACH posting return:
- relevant: true only if it is a real engineering/data role in the target families.
- experience: what the posting REQUIRES. "fresher" = no experience or new grads welcome;
  "0-1" = up to about 1 year; "2+" = 2 or more years required; "not_stated" if truly absent.
  Seniority words (Senior, Lead, SDE-2) imply "2+".
- location_ok: false if it is on-site elsewhere, or remote but restricted to other countries.
- score 0–100: how good a match this is for the candidate profile below (skills overlap, level
  fit, role family). 80+ = apply today; 60–79 = reasonable; under 50 = weak.
- reason: at most 15 words, concrete (e.g. "Entry-level GenAI role, RAG + Azure OpenAI match").

CANDIDATE PROFILE:
{profile}"""

DRAFT = """You write outreach for a job seeker contacting a recruiter or hiring manager on LinkedIn
about one specific job. Write like a sharp, confident new engineer: specific, warm, brief.

Produce:
1. note: LinkedIn connection note, at most {note_chars} characters INCLUDING the greeting.
   Start with "Hi {{name}}," (keep the literal {{name}} placeholder). Name the exact role and the
   company, one proof point, and a light ask. No links.
2. dm: follow-up message after they accept, 60–110 words. Start with "Hi {{name}},". Name the role and
   company, mention 1–2 proof points that match THIS job's requirements, say you've applied or are
   applying, and end with one clear ask (a referral, a quick look at your profile, or 10 minutes).
3. email_subject: at most 70 characters, specific to the role.
4. email_body: 90–150 words, same substance as the dm, ending with a sign-off using the
   candidate's name and their LinkedIn/GitHub links from the profile.

RULES:
- Every claim about the candidate must come from the profile. Never invent numbers, employers,
  skills, or degrees. Use at most the numbers the profile states.
- Tie the proof point to something concrete in the job description.
- No clichés: "I hope this finds you well", "passionate", "rockstar", "dream company",
  "I came across", "esteemed", "kindly", "revert". No desperation, no over-flattery, no emojis.
- American English, correct grammar, no placeholders other than {{name}}.

CANDIDATE PROFILE:
{profile}"""

VERIFY = """You are the final reviewer of a job-outreach message before it is sent.
Check it against the job and the candidate profile. Report ok=false with specific problems if ANY:
- the company or role is wrong, missing, or generic (message could be sent to any company)
- a claim about the candidate is not supported by the profile
- grammar/spelling errors, awkward phrasing, clichés, or an unclear ask
- it breaks the length limits: note ≤ {note_chars} characters, dm 60–110 words, email body 90–150 words
Otherwise ok=true with an empty problems list.

CANDIDATE PROFILE:
{profile}"""

CLICHES = ["hope this finds you", "passionate", "rockstar", "dream company", "i came across",
           "esteemed", "kindly", "revert", "ninja", "guru"]


# ------------------------------------------------------------------ client

class AI:
    def __init__(self, api_key: str, cfg: dict, profile: str, log=print):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, timeout=180)
        self.cfg = cfg["llm"]
        self.profile = profile.strip()[:6000]
        self.log = log
        self.classify_model = self.cfg["classify_model"]
        self.calls = 0

    def _parse(self, model: str, instructions: str, user: str, schema):
        last = None
        for attempt in range(4):
            try:
                resp = self.client.responses.parse(model=model, instructions=instructions,
                                                   input=user, text_format=schema)
                self.calls += 1
                if resp.output_parsed is None:
                    raise ValueError("no parseable output")
                return resp.output_parsed
            except Exception as e:  # noqa: BLE001
                last = e
                msg = str(e).lower()
                if ("model" in msg and ("not found" in msg or "does not exist" in msg)
                        and model != self.cfg["draft_model"]):
                    self.log(f"Model {model} unavailable; using {self.cfg['draft_model']}.")
                    model = self.classify_model = self.cfg["draft_model"]
                    continue
                if any(k in msg for k in ("429", "rate", "timeout", "timed out", "500", "502", "503", "parseable")):
                    time.sleep(4 * 2 ** attempt)
                    continue
                raise
        raise RuntimeError(f"OpenAI call failed: {last}")

    # ---------------- classification
    def classify(self, jobs: list[dict], batch: int = 12) -> dict[str, Verdict]:
        out: dict[str, Verdict] = {}
        instr = CLASSIFY.format(profile=self.profile or "(profile not provided)")
        for i in range(0, len(jobs), batch):
            chunk = jobs[i:i + batch]
            payload = [{"id": j["id"], "title": j["title"], "company": j["company"],
                        "location": j["location"], "remote": j["is_remote"],
                        "seniority": j.get("seniority", ""), "experience_hint": j.get("experience_text", ""),
                        "description": (j.get("description") or "")[:1800]} for j in chunk]
            try:
                res = self._parse(self.classify_model, instr, json.dumps(payload, ensure_ascii=False), Verdicts)
                for v in res.items:
                    out[v.id] = v
            except Exception as e:  # noqa: BLE001
                self.log(f"Classifier batch failed ({e}); those jobs fall back to rules.")
        return out

    # ---------------- drafts
    def draft(self, job: dict) -> dict:
        nc = self.cfg["note_chars"]
        instr = DRAFT.format(note_chars=nc, profile=self.profile)
        job_txt = (f"ROLE: {job['title']}\nCOMPANY: {job['company']}\nLOCATION: {job['location']}\n"
                   f"JOB DESCRIPTION:\n{(job.get('description') or '')[:3500]}")
        feedback = ""
        d = None
        for round_ in range(2):
            d = self._parse(self.cfg["draft_model"], instr, job_txt + feedback, Drafts)
            problems = local_checks(d, job, nc)
            chk = self._parse(self.classify_model, VERIFY.format(note_chars=nc, profile=self.profile),
                              job_txt + "\n\nMESSAGE TO REVIEW:\n" + d.model_dump_json(indent=1), Check)
            problems += chk.problems if not chk.ok else []
            if not problems:
                return {**d.model_dump(), "verified": True, "problems": []}
            feedback = "\n\nYOUR PREVIOUS DRAFT FAILED REVIEW. Fix these:\n- " + "\n- ".join(problems)
        return {**d.model_dump(), "verified": False, "problems": problems}


def _company_variants(company: str) -> list[str]:
    c = re.sub(r"\b(pvt\.?|private|ltd\.?|limited|inc\.?|llc|technologies|software)\b", "", company, flags=re.I)
    c = c.strip(" ,.")
    return [v.lower() for v in {company.strip(), c, c.split()[0] if c.split() else c} if len(v) >= 2]


def local_checks(d: Drafts, job: dict, note_chars: int) -> list[str]:
    """Deterministic checks the message must pass before it is shown as verified."""
    p = []
    if len(d.note.replace("{name}", "Alexandra")) > note_chars:
        p.append(f"note is {len(d.note)} chars; must be ≤ {note_chars} with a 9-letter name filled in")
    for field, lo, hi in (("dm", 55, 120), ("email_body", 85, 165)):
        n = len(getattr(d, field).split())
        if not lo <= n <= hi:
            p.append(f"{field} has {n} words; target {lo + 5}–{hi - 10}")
    variants = _company_variants(job["company"])
    for field in ("note", "dm", "email_body"):
        low = getattr(d, field).lower()
        if variants and not any(v in low for v in variants):
            p.append(f"{field} does not name the company '{job['company']}'")
        for c in CLICHES:
            if c in low:
                p.append(f"{field} uses cliché '{c}'")
    role_words = [w for w in re.findall(r"[A-Za-z/+]+", job["title"]) if len(w) > 2][:3]
    if role_words and not any(w.lower() in d.dm.lower() for w in role_words):
        p.append("dm does not name the role")
    for field in ("note", "dm", "email_subject", "email_body"):
        leftovers = [x for x in re.findall(r"\{[^}]*\}|\[[A-Z _]+\]", getattr(d, field)) if x != "{name}"]
        if leftovers:
            p.append(f"{field} has unfilled placeholders {leftovers}")
    return p
