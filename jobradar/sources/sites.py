"""Generic job-site adapter, so any job site can be added from config.yaml without new code.

Modes
  html     plain HTTP fetch of listing pages (server-rendered sites: Astra, Jobgether, ...)
  browser  headless Chromium renders the page first (JavaScript sites: Hirist, engineerHUB, ...)
  rss      RSS/Atom feed (most WordPress fresher-job blogs expose /feed)

Flow per site
  1. Open each listing URL (+ extra pages) and collect links matching `link_pattern`.
  2. Title-filter using the link text, so only relevant jobs cost a detail request.
  3. For new, relevant links, open the job page and read schema.org JobPosting JSON-LD
     (what job sites publish for Google Jobs); fall back to <title>/meta tags/page text.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import pause, session
from ..models import Job
from .ats import strip_html


def _id(site: str, url: str) -> str:
    return f"site-{re.sub(r'[^a-z0-9]+', '', site.lower())[:12]}-{hashlib.sha1(url.encode()).hexdigest()[:12]}"


# ------------------------------------------------------------------ browser

class Browser:
    """Lazy Playwright wrapper; only started if some site uses mode: browser."""

    def __init__(self):
        self._pw = self._browser = None

    def html(self, url: str, scrolls: int = 3) -> str:
        if self._browser is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            import os
            self._browser = self._pw.chromium.launch(args=["--no-sandbox"],
                                                     executable_path=os.getenv("CHROME_PATH") or None)
        page = self._browser.new_page(user_agent=session().headers["User-Agent"])
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            for _ in range(scrolls):  # trigger lazy-loaded lists
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1200)
            return page.content()
        finally:
            page.close()

    def close(self):
        if self._browser:
            self._browser.close()
            self._pw.stop()


@contextmanager
def browser_ctx():
    b = Browser()
    try:
        yield b
    finally:
        b.close()


# ------------------------------------------------------------------ parsing

def extract_links(html: str, base: str, link_re: re.Pattern, company_re: re.Pattern | None) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].split("#")[0])
        if not link_re.search(href):
            continue
        text = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        rec = found.setdefault(href, {"url": href, "text": "", "company": ""})
        if len(text) > len(rec["text"]) and len(text) < 200:
            rec["text"] = text
        if company_re and not rec["company"]:
            node = a
            for _ in range(5):  # look for a company link in the surrounding card
                node = node.parent
                if node is None:
                    break
                c = node.find("a", href=company_re)
                if c and c is not a:
                    rec["company"] = c.get_text(" ", strip=True)
                    break
    return [r for r in found.values() if r["text"]]


def split_title(text: str) -> tuple[str, str]:
    """'AI Engineer at Acme' -> ('AI Engineer', 'Acme')."""
    m = re.match(r"^(.*?)\s+at\s+(.+)$", text)
    return (m.group(1).strip(), m.group(2).strip()) if m else (text.strip(), "")


def _walk_ld(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _walk_ld(x)
    elif isinstance(obj, dict):
        yield obj
        if "@graph" in obj:
            yield from _walk_ld(obj["@graph"])


def _loc_text(jl) -> str:
    out = []
    for loc in jl if isinstance(jl, list) else [jl]:
        if not isinstance(loc, dict):
            continue
        a = loc.get("address") or {}
        if isinstance(a, str):
            out.append(a)
            continue
        country = a.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts = [a.get("addressLocality"), a.get("addressRegion"), country]
        out.append(", ".join(p for p in parts if p))
    return " | ".join(x for x in out if x)


def parse_detail(html: str, job: Job) -> bool:
    """Fill job from JSON-LD JobPosting, else meta tags. Returns True if anything useful was found."""
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_ld(data):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                job.title = strip_html(node.get("title") or "") or job.title
                org = node.get("hiringOrganization") or {}
                job.company = (org.get("name") if isinstance(org, dict) else str(org)) or job.company
                job.location = _loc_text(node.get("jobLocation")) or job.location
                if str(node.get("jobLocationType", "")).upper() == "TELECOMMUTE":
                    job.is_remote = True
                    req = node.get("applicantLocationRequirements")
                    names = [r.get("name", "") for r in (req if isinstance(req, list) else [req]) if isinstance(r, dict)]
                    job.location = job.location or ("Remote (" + (", ".join(n for n in names if n) or "Anywhere") + ")")
                job.posted_at = str(node.get("datePosted") or "")[:10] or job.posted_at
                job.description = strip_html(node.get("description") or "")[:6000] or job.description
                exp = node.get("experienceRequirements")
                if isinstance(exp, dict) and exp.get("monthsOfExperience") is not None:
                    yrs = float(exp["monthsOfExperience"]) / 12
                    job.experience_text = f"{yrs:.0f}+ years"
                elif isinstance(exp, str):
                    job.experience_text = exp[:60] if re.search(r"\d", exp) else job.experience_text
                return True
    # ---- fallbacks: page title / meta tags
    got = False
    title_tag = (soup.title.get_text(strip=True) if soup.title else "")
    m = re.match(r"^(.+?)\s+at\s+(.+?),\s*(.+?)\s*[|\-–]\s*[^|\-–]+$", title_tag)
    if m:  # "Data Engineer at Acme, Pune, Maharashtra, India | Site"
        job.title, job.company, job.location = m.group(1), job.company or m.group(2), job.location or m.group(3)
        got = True
    desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    if desc and desc.get("content"):
        job.description = job.description or desc["content"]
        got = True
    pub = soup.find("meta", property="article:published_time")
    if pub and pub.get("content"):
        job.posted_at = job.posted_at or pub["content"][:10]
    if not job.description:
        main = soup.find("main") or soup.body
        if main:
            text = main.get_text("\n", strip=True)
            if len(text) > 200:
                job.description = text[:6000]
                got = True
    return got


def parse_feed(xml: bytes, site: str) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//a:entry", ns)
    for it in items:
        title = (it.findtext("title") or it.findtext("a:title", namespaces=ns) or "").strip()
        link = it.findtext("link") or ""
        if not link:
            le = it.find("a:link", ns)
            link = le.get("href", "") if le is not None else ""
        date = it.findtext("pubDate") or it.findtext("a:updated", namespaces=ns) or ""
        try:
            posted = parsedate_to_datetime(date).date().isoformat() if "," in date else date[:10]
        except (TypeError, ValueError):
            posted = None
        desc = it.findtext("description") or it.findtext("a:summary", namespaces=ns) or ""
        out.append({"url": link.strip(), "text": title, "posted": posted, "desc": strip_html(desc)})
    return out


# blog post titles like "Oracle Recruitment 2026 | Programmer Analyst | Bengaluru"
BLOG_TITLE = re.compile(r"^(?P<co>.+?)\s+(?:off[- ]campus|recruitment|hiring|careers?|walk[- ]in|jobs?)\b[^|]*"
                        r"(?:\|\s*(?P<role>[^|]+))?(?:\|\s*(?P<loc>[^|]+))?", re.I)


def blog_fields(title: str) -> tuple[str, str, str]:
    m = BLOG_TITLE.match(title)
    if not m:
        return title, "", ""
    return (m.group("role") or title).strip(), m.group("co").strip(), (m.group("loc") or "").strip()


# ------------------------------------------------------------------ runner

def fetch_site(site: dict, *, known, title_ok, browser: Browser | None) -> list[Job]:
    """Return Job objects for one configured site. `known(id)` skips detail fetches for seen jobs."""
    name = site["name"]
    mode = site.get("mode", "html")
    link_re = re.compile(site.get("link_pattern", r"."))
    company_re = re.compile(site["company_pattern"]) if site.get("company_pattern") else None
    default_loc = site.get("default_location", "")
    apply_type = site.get("apply_type", "board")
    detail_limit = site.get("detail_limit", 40)
    s = session()

    def get_html(url: str) -> str:
        if mode == "browser":
            if browser is None:
                raise RuntimeError("browser mode needs Playwright (pip install playwright && playwright install chromium)")
            return browser.html(url)
        r = s.get(url, timeout=30)
        r.raise_for_status()
        return r.text

    # 1) collect candidate links
    cands: dict[str, dict] = {}
    for base in site.get("list_urls", []):
        if mode == "rss":
            r = s.get(base, timeout=30)
            r.raise_for_status()
            for it in parse_feed(r.content, name):
                cands.setdefault(it["url"], it)
            continue
        for n in range(1, site.get("pages", 1) + 1):
            url = base if n == 1 else site.get("page_format", "{url}?page={n}").format(url=base.rstrip("/"), n=n)
            try:
                links = extract_links(get_html(url), url, link_re, company_re)
            except Exception:  # noqa: BLE001
                if n == 1:
                    raise
                break
            for l in links:
                cands.setdefault(l["url"], l)
            if not links:
                break
            pause(1.0, 2.0)

    if not cands:
        raise RuntimeError("0 job links found: the site blocked this run, needs a login, or its layout "
                           "changed (check link_pattern in config.yaml)")

    # 2) build jobs; fetch details only for new, title-relevant ones
    out, fetched = [], 0
    for url, c in cands.items():
        if mode == "rss":
            title, company, loc = blog_fields(c["text"])
        else:
            title, company = split_title(c["text"])
            company, loc = c.get("company") or company, ""
        job = Job(id=_id(name, url), source=name.lower(), title=title[:160], company=company[:100],
                  location=loc, url=url, apply_type=apply_type, posted_at=c.get("posted"),
                  description=c.get("desc", ""))
        if not known(job.id) and title_ok(job.title) and fetched < detail_limit and mode != "rss":
            try:
                html = s.get(url, timeout=30)
                ok = html.ok and parse_detail(html.text, job)
                if not ok and mode == "browser" and browser:
                    parse_detail(browser.html(url, scrolls=0), job)
            except Exception:  # noqa: BLE001
                pass
            fetched += 1
            pause(0.8, 1.8)
        if not job.location:
            job.location = default_loc
        job.is_remote = job.is_remote or "remote" in (job.location or "").lower()
        out.append(job)
    return out


def all_sites(cfg: dict, known, title_ok) -> tuple[list[Job], list[dict]]:
    sites = [x for x in cfg.get("sites", []) if x.get("enabled", True)]
    jobs, health = [], []
    need_browser = any(x.get("mode") == "browser" for x in sites)
    browser = None
    if need_browser:
        try:
            browser = Browser()
        except Exception:  # noqa: BLE001
            browser = None
    try:
        for site in sites:
            t0 = time.time()
            try:
                got = fetch_site(site, known=known, title_ok=title_ok, browser=browser)
                jobs += got
                health.append({"source": f"site: {site['name']}", "ok": True, "count": len(got)})
            except Exception as e:  # noqa: BLE001
                health.append({"source": f"site: {site['name']}", "ok": False, "count": 0, "error": str(e)[:240]})
            health[-1]["secs"] = round(time.time() - t0, 1)
    finally:
        if browser:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
    return jobs, health
