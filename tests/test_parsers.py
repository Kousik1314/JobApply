import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from jobradar.sources.linkedin import parse_cards, parse_detail
from jobradar.sources.ats import detect, strip_html, _wd_date
from jobradar.models import Job

CARD = '''<li><div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:4012345678">
<a class="base-card__full-link" href="https://in.linkedin.com/jobs/view/ai-engineer-at-acme-4012345678?position=1&amp;pageNum=0"><span class="sr-only">AI Engineer</span></a>
<div class="base-search-card__info"><h3 class="base-search-card__title">
          AI Engineer
        </h3><h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="https://in.linkedin.com/company/acme?trk=x">Acme AI</a></h4>
<div class="base-search-card__metadata"><span class="job-search-card__location">Gurugram, Haryana, India</span>
<time class="job-search-card__listdate--new" datetime="2026-09-23">5 hours ago</time></div></div></div></li>
<li><div class="base-card base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:4099999999">
<div class="base-search-card__info"><h3 class="base-search-card__title">Data Engineer</h3>
<h4 class="base-search-card__subtitle">Beta Corp</h4><span class="job-search-card__location">India</span></div></div></li>'''

EXTERNAL = '''<html><body><code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/externalApply/4012345678?url=https%3A%2F%2Fcareers%2Eacme%2Ecom%2Fjobs%2F123&urlHash=abc"--></code>
<div class="show-more-less-html__markup">We need a <b>fresher</b> AI engineer.<br>0-1 years of experience with Python.</div>
<ul><li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Seniority level</h3>
<span class="description__job-criteria-text">Entry level</span></li></ul></body></html>'''
EASY = '''<html><body><div class="show-more-less-html__markup">Build RAG apps.</div></body></html>'''

def test_linkedin():
    cards = parse_cards(CARD)
    ids = sorted(j.id for j in cards)
    assert ids == ["li-4012345678", "li-4099999999"], ids
    a = next(j for j in cards if j.id == "li-4012345678")
    assert (a.title, a.company, a.location, a.posted_at) == ("AI Engineer", "Acme AI", "Gurugram, Haryana, India", "2026-09-23")
    assert a.company_url == "https://in.linkedin.com/company/acme"
    j = Job(id="li-1", source="linkedin", title="t", company="c", location="l", url="u")
    assert parse_detail(EXTERNAL, j) and j.apply_type == "company"
    assert j.apply_url == "https://careers.acme.com/jobs/123", j.apply_url
    assert j.seniority == "Entry level" and "fresher" in j.description
    k = Job(id="li-2", source="linkedin", title="t", company="c", location="l", url="u")
    assert parse_detail(EASY, k) and k.apply_type == "easy_apply"
    assert not parse_detail("<html>login wall</html>", Job(id="x", source="linkedin", title="", company="", location="", url=""))

def test_detect():
    assert detect("https://boards.greenhouse.io/databricks") == {"ats": "greenhouse", "token": "databricks"}
    assert detect("https://job-boards.greenhouse.io/mongodb/jobs/123")["token"] == "mongodb"
    assert detect("https://jobs.lever.co/palantir/abc") == {"ats": "lever", "token": "palantir", "eu": False}
    assert detect("https://jobs.eu.lever.co/foo")["eu"] is True
    assert detect("https://jobs.ashbyhq.com/openai")["token"] == "openai"
    assert detect("https://jobs.smartrecruiters.com/Visa")["token"] == "Visa"
    w = detect("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite")
    assert w == {"ats": "workday", "host": "nvidia.wd5.myworkdayjobs.com", "tenant": "nvidia", "site": "NVIDIAExternalCareerSite"}, w
    assert detect("https://careers.example.com") is None

def test_misc():
    assert strip_html("&lt;p&gt;Hi&lt;/p&gt;&lt;ul&gt;&lt;li&gt;A&lt;/li&gt;&lt;/ul&gt;") == "Hi\nA"
    assert _wd_date("Posted Today") and _wd_date("Posted 30+ Days Ago")

if __name__ == "__main__":
    test_linkedin(); test_detect(); test_misc(); print("parsers OK")
