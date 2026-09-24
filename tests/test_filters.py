import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from jobradar.config import load_config
from jobradar.filters import compile_roles, parse_experience, location_tags, rule_check, title_ok
from jobradar.models import Job

cfg = load_config()
inc, exc = compile_roles(cfg)
J = lambda **k: Job(**{"id": "x", "source": "linkedin", "title": "Software Engineer", "company": "A",
                       "location": "Bengaluru, Karnataka, India", "url": "u", **k})

def test_titles():
    good = ["AI Engineer", "Associate Software Engineer", "SDE-1", "Machine Learning Engineer I", "Data Engineer",
            "GenAI Developer", "Graduate Engineer Trainee", "Python Developer", "Software Development Engineer",
            "Junior Data Scientist", "AI/ML Engineer - Fresher", "Full Stack Developer"]
    bad = ["Senior Software Engineer", "Lead Data Engineer", "SDE II", "Software Engineer III", "ML Intern",
           "Engineering Manager", "Sales Executive", "Staff ML Engineer", "Principal Architect", "HR Recruiter"]
    for t in good: assert title_ok(t, inc, exc)[0], t
    for t in bad: assert not title_ok(t, inc, exc)[0], t

def test_experience():
    cases = {
        "We welcome freshers and recent graduates.": "fresher",
        "Experience: 0-1 years in Python.": "fresher",
        "1+ years of experience building APIs.": "0-1",
        "Minimum 3 years of experience with Spark.": "2+",
        "2-4 years experience required": "2+",
        "Build ML models with PyTorch.": "not_stated",
        "Our company has 10 years of history. You will build APIs.": "not_stated",
        "Entry level role. 5+ years of experience required.": "2+",
    }
    for d, want in cases.items():
        got = parse_experience(J(description=d))[0]
        assert got == want, (d, got, want)
    assert parse_experience(J(experience_text="0-2 Yrs"))[0] == "fresher"
    assert parse_experience(J(experience_text="3-6 Yrs"))[0] == "2+"

def test_locations():
    ok = {"Gurugram, Haryana, India": ["Delhi NCR"], "Noida": ["Delhi NCR"], "Bangalore Urban": ["Bengaluru"],
          "Navi Mumbai, Maharashtra": ["Mumbai"], "Remote (Anywhere)": ["Remote"], "India": ["India"],
          "Pune, Maharashtra, India (Hybrid)": ["Pune"], "Remote - India": ["Remote"]}
    for loc, want in ok.items():
        good, tags = location_tags(J(location=loc), cfg)
        assert good and tags == want, (loc, tags)
    for loc in ["Hyderabad, Telangana, India", "Chennai", "Remote (US only)", "San Francisco, CA", "Remote - USA"]:
        assert not location_tags(J(location=loc, is_remote="remote" in loc.lower()), cfg)[0], loc

def test_rule_check():
    assert rule_check(J(), cfg, inc, exc)[0]
    assert not rule_check(J(seniority="Mid-Senior level"), cfg, inc, exc)[0]
    assert not rule_check(J(posted_at="2020-01-01"), cfg, inc, exc)[0]

if __name__ == "__main__":
    test_titles(); test_experience(); test_locations(); test_rule_check(); print("filters OK")
