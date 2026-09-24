"""Generic site adapter: html, rss, JSON-LD, and a real headless-browser render of a JS page."""
import os, sys, pathlib, re, threading, http.server, socketserver, functools
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import jobradar.sources.sites as S
from jobradar.models import Job

ASTRA_LIST = '''<ul><li><a href="/jobs/acme-ai-engineer-fresher-10500">AI Engineer - Fresher at Acme AI</a></li>
<li><a href="https://useastra.in/jobs/accor-outlet-manager-10475">Outlet Manager at Accor</a></li>
<li><a href="/jobs/category/ai-ml">AI/ML jobs</a></li></ul>'''
ASTRA_DETAIL = '''<html><head><title>AI Engineer - Fresher at Acme AI, Gurugram, Haryana, India | Astra</title>
<meta name="description" content="B.Tech CSE; 0-1 years experience; Python, LLMs, RAG.">
<meta property="article:published_time" content="2026-09-23T10:07:56.897Z"></head><body>x</body></html>'''
JG_LIST = '''<div class="card"><img><a href="/offer/6ab177fa865119c687d775f5-software-engineer-1" title="Software Engineer 1">Software Engineer 1</a>1 day ago
<div><a href="https://jobgether.com/remote-jobs/company-modmed">ModMed</a> - SaaS</div><a href="/remote-jobs/india">Remote from India</a></div>
<div class="card"><a href="/offer/6ab32be1865119c687de545a-principal-software-engineer-react">Principal Software Engineer (React)</a>
<a href="https://jobgether.com/remote-jobs/company-newfolddigital">Newfold Digital</a></div>'''
LD = '''<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"x"},
{"@type":"JobPosting","title":"Junior Data Engineer","datePosted":"2026-09-22","description":"&lt;p&gt;Freshers welcome. Spark, SQL.&lt;/p&gt;",
"hiringOrganization":{"@type":"Organization","name":"DataCo"},"jobLocation":[{"@type":"Place","address":{"addressLocality":"Pune","addressRegion":"MH","addressCountry":"IN"}}],
"experienceRequirements":{"@type":"OccupationalExperienceRequirements","monthsOfExperience":12}}]}</script></html>'''
RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Oracle Recruitment 2026 | Programmer Analyst 1-IT | Bengaluru</title><link>https://blog.example/oracle-2026/</link>
<pubDate>Tue, 22 Sep 2026 10:00:00 +0000</pubDate><description>&lt;p&gt;B.Tech 2025/2026 batch, freshers.&lt;/p&gt;</description></item>
<item><title>TCS NQT 2026 Registration Open</title><link>https://blog.example/tcs/</link><pubDate>Tue, 22 Sep 2026 09:00:00 +0000</pubDate></item>
</channel></rss>'''

class FakeResp:
    def __init__(self, text=None, content=None, ok=True): self.text = text or ""; self.content = content or (text or "").encode(); self.ok = ok
    def raise_for_status(self):
        if not self.ok: raise RuntimeError("404")

def test_parsers():
    links = S.extract_links(ASTRA_LIST, "https://useastra.in/jobs", re.compile(r"useastra\.in/jobs/[a-z0-9-]+-\d+$"), None)
    assert [l["url"] for l in links] == ["https://useastra.in/jobs/acme-ai-engineer-fresher-10500", "https://useastra.in/jobs/accor-outlet-manager-10475"]
    assert S.split_title(links[0]["text"]) == ("AI Engineer - Fresher", "Acme AI")
    j = Job(id="x", source="astra", title="AI Engineer - Fresher", company="Acme AI", location="", url="u")
    assert S.parse_detail(ASTRA_DETAIL, j)
    assert (j.location, j.posted_at) == ("Gurugram, Haryana, India", "2026-09-23") and "0-1 years" in j.description
    jg = S.extract_links(JG_LIST, "https://jobgether.com/remote-jobs/india/software-engineer", re.compile(r"/offer/[0-9a-f]{24}-"),
                         re.compile(r"remote-jobs/company-|search-offers\?company="))
    assert [(l["text"], l["company"]) for l in jg] == [("Software Engineer 1", "ModMed"), ("Principal Software Engineer (React)", "Newfold Digital")], jg
    k = Job(id="y", source="s", title="t", company="", location="", url="u")
    assert S.parse_detail(LD, k)
    assert (k.title, k.company, k.location, k.posted_at, k.experience_text) == ("Junior Data Engineer", "DataCo", "Pune, MH, IN", "2026-09-22", "1+ years"), k
    assert "Freshers welcome" in k.description
    items = S.parse_feed(RSS, "blog")
    assert items[0]["posted"] == "2026-09-22" and "freshers" in items[0]["desc"]
    assert S.blog_fields(items[0]["text"]) == ("Programmer Analyst 1-IT", "Oracle", "Bengaluru")

def test_fetch_site_html_and_rss(monkeypatch=None):
    pages = {"https://useastra.in/jobs/experience/fresher": ASTRA_LIST,
             "https://useastra.in/jobs/acme-ai-engineer-fresher-10500": ASTRA_DETAIL}
    class Sess:
        headers = {"User-Agent": "x"}
        def get(self, url, **k):
            if url.endswith("feed/"): return FakeResp(content=RSS)
            return FakeResp(text=pages[url]) if url in pages else FakeResp(ok=False)
    S.session = lambda: Sess(); S.pause = lambda *a: None
    from jobradar.config import load_config
    from jobradar.filters import compile_roles, title_ok
    inc, exc = compile_roles(load_config())
    site = {"name": "Astra", "mode": "html", "list_urls": ["https://useastra.in/jobs/experience/fresher"], "pages": 2,
            "link_pattern": r"useastra\.in/jobs/[a-z0-9-]+-\d+$", "default_location": "India"}
    jobs = S.fetch_site(site, known=lambda i: False, title_ok=lambda t: title_ok(t, inc, exc)[0], browser=None)
    ai = next(j for j in jobs if "AI Engineer" in j.title)
    om = next(j for j in jobs if "Outlet" in j.title)
    assert ai.location.startswith("Gurugram") and om.location == "India", (ai, om)   # outlet manager: no detail fetch
    rss = S.fetch_site({"name": "Blog", "mode": "rss", "list_urls": ["https://blog.example/feed/"], "default_location": "India"},
                       known=lambda i: False, title_ok=lambda t: True, browser=None)
    assert rss[0].title == "Programmer Analyst 1-IT" and rss[0].company == "Oracle" and rss[0].location == "Bengaluru"

JS_PAGE = '''<html><body><div id="app">loading…</div><script>
setTimeout(()=>{document.getElementById("app").innerHTML=
 '<a href="/j/acme-ml-engineer-fresher-1650001">ML Engineer - Fresher</a><a href="/j/beta-sde-1-1650002">SDE 1</a>';},300);
</script></body></html>'''

def test_browser_mode():
    chrome = os.getenv("CHROME_PATH")
    if not chrome:
        print("  (skipping browser test: set CHROME_PATH)"); return
    d = pathlib.Path("/tmp/jsite"); d.mkdir(exist_ok=True); (d / "list.html").write_text(JS_PAGE)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(d))
    handler.log_message = lambda *a: None
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    b = S.Browser()
    try:
        html = b.html(f"http://127.0.0.1:{port}/list.html", scrolls=1)
    finally:
        b.close(); srv.shutdown()
    links = S.extract_links(html, f"http://127.0.0.1:{port}/", re.compile(r"/j/[a-z0-9.-]+-\d+"), None)
    assert [l["text"] for l in links] == ["ML Engineer - Fresher", "SDE 1"], links
    print("  browser render OK:", [l["url"].split("/")[-1] for l in links])

if __name__ == "__main__":
    test_parsers(); test_fetch_site_html_and_rss(); test_browser_mode(); print("sites OK")
