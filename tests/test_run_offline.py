"""Full refresh with fake sources and a fake AI (no network). Also builds demo data."""
import json, os, sys, pathlib, tempfile, datetime as dt
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import jobradar.run as R
import jobradar.ai as A
import jobradar.sources.linkedin as L
from jobradar.models import Job

TODAY = dt.date.today().isoformat()
def mk(i, src, title, company, loc, typ="board", desc="", remote=False, **k):
    return Job(id=f"{src[:2]}-{i}", source=src, title=title, company=company, location=loc,
               url=f"https://example.com/{src}/{i}", apply_type=typ, posted_at=TODAY,
               description=desc, is_remote=remote, **k)

RUN = {"n": 0}
def fake_collect(cfg, only, known=None, title_ok=None):
    RUN["n"] += 1
    jobs = [
        mk(1, "linkedin", "AI Engineer", "Acme AI", "Gurugram, Haryana, India", "linkedin_unknown", "We hire freshers to build RAG apps with LangChain and Azure OpenAI."),
        mk(2, "linkedin", "Associate Software Engineer", "Zeta Systems", "Bengaluru, Karnataka, India", "linkedin_unknown", "0-1 years experience in Python and FastAPI."),
        mk(3, "linkedin", "Senior Data Engineer", "Big Co", "Pune", "linkedin_unknown"),
        mk(4, "linkedin", "Data Engineer", "Hyd Co", "Hyderabad, Telangana, India", "linkedin_unknown"),
        mk(5, "greenhouse", "Software Engineer, New Grad", "Stripe", "Bengaluru", "company", "New grads welcome. Build payments APIs in Java or Python."),
        mk(6, "greenhouse", "Software Engineer", "Stripe", "Bengaluru", "company", "Minimum 4 years of experience in distributed systems."),
        mk(7, "naukri", "Machine Learning Engineer", "Kolkata Analytics", "Kolkata", "board", "Build ML models.", experience_text="0-2 Yrs"),
        mk(8, "remotive", "Junior ML Engineer", "RemoteFirst", "Remote (Worldwide)", "company", "Entry-level ML role, PyTorch.", remote=True),
        mk(9, "remotive", "Junior Backend Developer", "USOnly Inc", "Remote (USA only)", "company", "", remote=True),
        mk(10, "workday", "Data Scientist", "Nvidia", "India, Bengaluru", "company", "Recent graduates in CS. Python, SQL, statistics."),
        mk(11, "indeed", "AI Engineer", "Acme AI", "Gurgaon, Haryana", "board", "Duplicate of #1 on another board."),
        mk(12, "hackernews", "ML Engineer (Remote, India OK)", "TinyStartup", "Remote", "community", "TinyStartup | ML Engineer | Remote (India OK) | new grads welcome", remote=True),
        mk(13, "linkedin", "Software Engineer", "Mumbai Fintech", "Mumbai, Maharashtra, India", "linkedin_unknown", "Build trading systems. Freshers welcome."),
    ]
    if RUN["n"] == 2:   # second refresh: one new job, others repeat
        jobs.append(mk(14, "linkedin", "GenAI Engineer", "NewCo", "Noida, Uttar Pradesh, India", "linkedin_unknown", "Freshers. LLM agents with LangGraph."))
    health = [{"source": "fake", "ok": True, "count": len(jobs), "secs": 0.1},
              {"source": "company: boards.greenhouse.io/doesnotexist", "ok": False, "count": 0, "error": "404 Client Error", "secs": 0.3}]
    return jobs, health, {}

def fake_enrich(jobs, limit):
    for j in jobs:
        j.apply_type = "company" if j.id.endswith(("2", "13")) else "easy_apply"
        j.seniority = "Entry level"
    return len(jobs), 0

class FakeAI:
    def __init__(self, key, cfg, profile, log=print): self.calls = 0; self.cfg = cfg
    def classify(self, jobs):
        out = {}
        for j in jobs:
            fam = "Data Science" if "Scientist" in j["title"] else "AI/ML" if any(w in j["title"] for w in ("AI", "ML", "Machine", "GenAI")) else "Software"
            score = 92 if "AI" in j["title"] else 78 if "Software" in j["title"] else 66
            out[j["id"]] = A.Verdict(id=j["id"], relevant=True, role_family=fam,
                                     experience="fresher", location_ok=True, score=score,
                                     reason=f"Entry-level {fam} role; strong Python overlap")
        self.calls += 1
        return out
    def draft(self, job):
        c, t = job["company"], job["title"]
        return {"note": f"Hi {{name}}, I'm applying for the {t} role at {c}. I built a RAG chatbot over 150+ docs with ~90% top-3 retrieval. Would love to connect.",
                "dm": f"Hi {{name}}, thanks for connecting. I've applied for the {t} role at {c}. At MAQ Software I built an LLM-as-a-Judge agent that evaluates 2,000+ test queries for an enterprise Power BI assistant, and I shipped a RAG onboarding chatbot with about 90% top-3 retrieval accuracy. The role's focus on production GenAI matches that work closely. Would you be open to taking a quick look at my profile, or pointing me to the right person on the team? Thank you!",
                "email_subject": f"{t} application: GenAI engineer, 2026 BTech CSE",
                "email_body": "Hi {name},\n\nI've applied for the " + t + " role at " + c + " and wanted to share a short note. " + "word " * 80 + "\n\nBest,\nCandidate",
                "verified": True, "problems": []}

def main():
    tmp = pathlib.Path(os.environ.get("OUT_DIR") or tempfile.mkdtemp()) / "data"
    R.DATA = tmp
    R.collect = fake_collect
    L.enrich = fake_enrich
    A.AI = FakeAI
    os.environ["OPENAI_API_KEY"] = "sk-fake"
    os.environ["CANDIDATE_PROFILE"] = "Name: Test Candidate\nSkills: Python, RAG"
    m1 = R.run()
    jobs = {j["id"]: j for j in json.loads((tmp / "jobs.json").read_text())}
    active = {k for k, v in jobs.items() if v["status"] == "active"}
    print("run1 stats:", m1["stats"])
    print("kept ids:", sorted(active))
    assert "li-3" not in active and "li-4" not in active and "gr-6" not in active and "re-9" not in active
    assert {"li-1", "li-2", "gr-5", "na-7", "re-8", "wo-10", "ha-12", "li-13"} <= active, active
    assert "in-11" not in jobs, "indeed duplicate of li-1 should merge into li-1"
    assert len(jobs["li-1"]["links"]) == 2 and jobs["li-1"]["apply_type"] == "easy_apply"
    assert jobs["li-2"]["apply_type"] == "company"
    assert jobs["li-1"]["drafts"] and jobs["li-1"]["cities"] == ["Delhi NCR"]
    assert jobs["ha-12"]["drafts"] is None, "no drafts for community posts"
    import time; time.sleep(1.2)
    m2 = R.run()
    jobs2 = json.loads((tmp / "jobs.json").read_text())
    new = [j["id"] for j in jobs2 if j["first_seen"] == m2["last_run"]]
    print("run2 new:", new, "| stats:", m2["stats"])
    assert new == ["li-14"], new
    print("OFFLINE RUN OK ->", tmp)

if __name__ == "__main__":
    main()
