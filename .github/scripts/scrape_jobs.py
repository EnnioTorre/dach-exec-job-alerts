"""
Fetches job listings from multiple DACH sources.

Source strategy by bot-protection level:
  - Stepstone / Karriere.at / jobs.ch : light protection → standard HTML scrape
    (JSON-LD first, site-specific HTML parser as fallback)
  - Indeed                             : moderate protection → use RSS feed
    (XML, no JS wall, officially supported, returns structured data)
  - LinkedIn                           : very strong (JS wall + ToS block) →
    never scraped directly; reached only via Google search proxy queries
  - Google search                      : used as a bot-safe proxy for
    LinkedIn and hard-to-reach career pages

Writes /tmp/jobs/jobs_raw.json.
"""

import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import urlencode, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

try:
    import certifi
except Exception:  # pragma: no cover - optional dependency
    certifi = None

# ---------------------------------------------------------------------------
# Source registry
#   type:
#     "html"        — standard HTTP fetch → JSON-LD / HTML parser
#     "rss"         — XML RSS/Atom feed   → parse_rss()
#     "google_proxy"— Google search HTML  → parse_google_jobs()
#                    (used as bot-safe proxy for LinkedIn and closed career pages)
# ---------------------------------------------------------------------------
SOURCES = [
    # Stepstone — light bot protection, JSON-LD present on result pages
    {"name": "stepstone_at_cto",  "type": "html",
     "url": "https://www.stepstone.at/jobs/cto",                 "region": "AT"},
    {"name": "stepstone_at_hoe",  "type": "html",
     "url": "https://www.stepstone.at/jobs/head-of-engineering", "region": "AT"},
    {"name": "stepstone_de_hoe",  "type": "html",
     "url": "https://www.stepstone.de/jobs/head-of-engineering", "region": "DE"},

    # Karriere.at — Austria's primary job board, light protection
    {"name": "karriere_at_cto",   "type": "html",
     "url": "https://www.karriere.at/jobs/cto",                  "region": "AT"},
    {"name": "karriere_at_hoe",   "type": "html",
     "url": "https://www.karriere.at/jobs/head-of-engineering",  "region": "AT"},
    {"name": "karriere_at_software", "type": "html",
     "url": "https://www.karriere.at/jobs/software-engineering", "region": "AT"},
    {"name": "karriere_at_platform", "type": "html",
     "url": "https://www.karriere.at/jobs/platform-engineering", "region": "AT"},
    {"name": "karriere_at_cloud", "type": "html",
     "url": "https://www.karriere.at/jobs/cloud-engineering",    "region": "AT"},

    # Indeed AT — use RSS feed (no JS wall, structured data, officially supported)
    {"name": "indeed_at_rss",     "type": "rss",
     "url": "https://at.indeed.com/rss?" + urlencode({
         "q": "head of engineering OR CTO OR engineering manager OR director of engineering",
         "l": "Austria", "sort": "date",
     }), "region": "AT"},

    # jobs.ch — Switzerland, light protection
    {"name": "jobs_ch",           "type": "html",
     "url": "https://www.jobs.ch/en/vacancies/?term=head+of+engineering", "region": "CH"},

    # LinkedIn is NOT scraped directly (JS wall + ToS prohibition).
    # Reached via search-engine proxies which return public snippets.
    {"name": "google_linkedin_at", "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs "Head of Engineering" OR "CTO" Austria OR Bolzano OR Bozen',
         "num": "20",
     }), "region": "AT"},
    {"name": "google_linkedin_de", "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs "Director of Engineering" OR "VP Engineering" Germany',
         "num": "20",
     }), "region": "DE"},

    # Bing/DuckDuckGo proxies improve resilience when Google yields no extractable cards.
    {"name": "bing_linkedin_at", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs "Head of Engineering" OR CTO Austria',
         "count": "20",
     }), "region": "AT"},
    {"name": "bing_linkedin_de", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs "Platform Engineering Manager" OR "Director of Engineering" Germany',
         "count": "20",
     }), "region": "DE"},
    {"name": "ddg_linkedin_dach", "type": "search_proxy",
     "url": "https://duckduckgo.com/html/?" + urlencode({
         "q": 'site:linkedin.com/jobs "Head of Engineering" OR "Cloud Engineering Manager" DACH',
     }), "region": "DACH"},

    # Proxies for sources that fail direct fetch in CI.
    {"name": "bing_stepstone_dach", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:stepstone.at/jobs OR site:stepstone.de/jobs "Head of Engineering" OR "Platform Engineer"',
         "count": "20",
     }), "region": "DACH"},
    {"name": "bing_indeed_dach", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:indeed.com OR site:indeed.de "Engineering Manager" OR "Head of Engineering"',
         "count": "20",
     }), "region": "DACH"},

    # Google Jobs structured results — broad DACH sweep
    {"name": "google_jobs_at",    "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": '"Head of Engineering" OR "CTO" OR "Engineering Manager" jobs Austria OR Bolzano OR Bozen',
         "num": "20",
     }), "region": "AT"},

    # Bolzano/Bozen focused sweep (South Tyrol, frequent DACH overlap)
    {"name": "google_jobs_bolzano", "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": '"Head of Engineering" OR CTO OR "Engineering Manager" jobs Bolzano OR Bozen',
         "num": "20",
     }), "region": "IT"},
]

# Rotate through a small pool of realistic browser UAs to reduce fingerprinting.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-AT,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
}

# Track last-fetch time per hostname to enforce a polite inter-request delay.
_last_fetch: dict[str, float] = {}
_MIN_DELAY = 2.0   # seconds between requests to the same host
_MAX_JITTER = 2.0  # additional random jitter (uniform)


def _ssl_verify_option() -> str | bool:
    """
    Resolve TLS verification mode.

    Default: verify with certifi bundle when available, else requests default.
    Override: SCRAPER_SSL_VERIFY=false disables verification (last resort).
    """
    if os.getenv("SCRAPER_SSL_VERIFY", "true").lower() in {"0", "false", "no"}:
        return False
    if certifi is not None:
        return certifi.where()
    return True


def _polite_delay(url: str) -> None:
    """Sleep enough to respect a per-host minimum delay + random jitter."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    elapsed = time.monotonic() - _last_fetch.get(host, 0)
    wait = max(0.0, _MIN_DELAY - elapsed) + random.uniform(0, _MAX_JITTER)
    if wait > 0.05:
        time.sleep(wait)
    _last_fetch[host] = time.monotonic()


def fetch(url: str, retries: int = 3) -> str | None:
    """HTTP GET with UA rotation, per-host polite delay, and retry logic."""
    session = requests.Session()
    headers = dict(_BASE_HEADERS)
    headers["User-Agent"] = random.choice(_USER_AGENTS)
    session.headers.update(headers)
    verify_opt = _ssl_verify_option()

    for attempt in range(retries):
        _polite_delay(url)
        try:
            r = session.get(url, timeout=25, allow_redirects=True, verify=verify_opt)
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code} for {url}")
            if r.status_code in (403, 429):
                # Blocked — rotate UA and wait longer before retry
                headers["User-Agent"] = random.choice(_USER_AGENTS)
                session.headers.update(headers)
                time.sleep(8 + random.uniform(0, 4))
                if attempt == retries - 1:
                    break
        except requests.exceptions.SSLError as exc:
            print(f"  SSL attempt {attempt + 1} failed: {exc}")
            # Optional fallback for environments with broken trust stores.
            # Enable only when explicitly requested.
            if os.getenv("SCRAPER_SSL_FALLBACK_INSECURE", "false").lower() in {"1", "true", "yes"}:
                try:
                    print("  Retrying once with SSL verification disabled (fallback mode)")
                    r = session.get(url, timeout=25, allow_redirects=True, verify=False)
                    if r.status_code == 200:
                        return r.text
                    print(f"  HTTP {r.status_code} for {url} (insecure fallback)")
                except requests.RequestException:
                    pass
            time.sleep(2)
        except requests.RequestException as exc:
            print(f"  Attempt {attempt + 1} failed: {exc}")
            time.sleep(3)
    return None


# ---------------------------------------------------------------------------
# JSON-LD extraction (works on any site that publishes structured data)
# ---------------------------------------------------------------------------

def extract_jsonld_jobs(html: str) -> list[dict]:
    """Return list of raw JSON-LD JobPosting dicts found in the page."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            raw = tag.string or ""
            data = json.loads(raw)
            if not isinstance(data, (dict, list)):
                continue
            if isinstance(data, list):
                data = data[0] if data else {}
            t = data.get("@type", "")
            if t == "JobPosting":
                jobs.append(data)
            elif t in ("ItemList", "SearchResultsPage"):
                for item in data.get("itemListElement", []):
                    if not isinstance(item, dict):
                        continue
                    inner = item.get("item", item)
                    if isinstance(inner, dict) and inner.get("@type") == "JobPosting":
                        jobs.append(inner)
        except (json.JSONDecodeError, TypeError):
            pass
    return jobs


def normalize_jsonld(job: dict, source_name: str) -> dict:
    location = ""
    job_loc = job.get("jobLocation")
    if isinstance(job_loc, dict):
        addr = job_loc.get("address", {})
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality", ""), addr.get("addressCountry", "")]
            location = ", ".join(p for p in parts if p)
        elif isinstance(addr, str):
            location = addr

    salary = ""
    base = job.get("baseSalary")
    if isinstance(base, dict):
        val = base.get("value", {})
        if isinstance(val, dict):
            lo = val.get("minValue", "")
            hi = val.get("maxValue", "")
            currency = base.get("currency", "EUR")
            if lo and hi:
                salary = f"{lo}–{hi} {currency}"
            elif hi:
                salary = f"up to {hi} {currency}"

    org = job.get("hiringOrganization", {})
    company = org.get("name", "") if isinstance(org, dict) else str(org)

    desc = (job.get("description") or "").lower()
    lang_hint = "de" if re.search(r"\bdeutsch\b|\bdeutschkenntnisse\b", desc) else "en"

    return {
        "title": job.get("title", "").strip(),
        "company": company.strip(),
        "location": location.strip(),
        "source_name": source_name,
        "source_url": job.get("url", job.get("sameAs", "")),
        "application_url": job.get("url", ""),
        "publish_date": job.get("datePosted", ""),
        "salary_text": salary,
        "language_hint": lang_hint,
    }


# ---------------------------------------------------------------------------
# RSS parser — works for Indeed and any standard RSS 2.0 / Atom feed
# ---------------------------------------------------------------------------

def parse_rss(xml_text: str, source_name: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feeds into normalised job dicts."""
    jobs: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"  RSS parse error for {source_name}: {exc}")
        return jobs

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    for item in items:
        def _t(tag: str) -> str:
            el = item.find(tag) or item.find(f"atom:{tag}", ns)
            return (el.text or "").strip() if el is not None else ""

        title = _t("title")
        if not title:
            continue
        link = _t("link") or _t("guid")
        if not link:
            link_el = item.find("atom:link", ns)
            link = (link_el.attrib.get("href", "") if link_el is not None else "")

        # Indeed RSS embeds location/company as plain text in <description>
        desc = _t("description")
        company = ""
        location = ""
        if desc:
            m_company = re.search(r"(?:company|employer)[:\s]+([^<\n,]+)", desc, re.I)
            m_location = re.search(r"(?:location|ort)[:\s]+([^<\n,]+)", desc, re.I)
            company = m_company.group(1).strip() if m_company else ""
            location = m_location.group(1).strip() if m_location else ""

        pub_date = _t("pubDate") or _t("published") or _t("updated")

        jobs.append({
            "title": title,
            "company": company,
            "location": location or "Austria",
            "source_name": source_name,
            "source_url": link,
            "application_url": link,
            "publish_date": pub_date,
            "salary_text": "",
            "language_hint": "en",
        })
    return jobs


# ---------------------------------------------------------------------------
# Google search proxy parser
#   Extracts job-card snippets from Google SERP HTML.
#   Used for LinkedIn and generic career pages that block direct scraping.
# ---------------------------------------------------------------------------

def _extract_search_result_url(href: str) -> str:
    """Normalize search-engine result URLs, including redirect wrappers."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        parsed = urlparse(href)
        if parsed.path in {"/url", "/link"}:
            qs = parse_qs(parsed.query)
            target = qs.get("q", [""])[0] or qs.get("url", [""])[0]
            return unquote(target) if target else ""
    return ""


def parse_google_jobs(html: str, source_name: str) -> list[dict]:
    """
    Extract job listings from Google search results.
    Google renders 'job carousel' cards for job-related queries — these contain
    structured data (JSON-LD ItemList with JobPosting) that the JSON-LD extractor
    already handles. This function handles the plain organic-result fallback.
    """
    # First try JSON-LD (Google often embeds JobPosting structured data in SERP)
    jobs = [normalize_jsonld(j, source_name) for j in extract_jsonld_jobs(html)]
    if jobs:
        return jobs

    # Fallback: parse organic result titles + URLs from common SERP layouts.
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []

    def _append_result(title: str, href: str, snippet: str) -> None:
        url = _extract_search_result_url(href)
        if not url:
            return
        title = title.strip()
        if not title:
            return
        m_at = re.search(r"(?:at|bei|@)\s+([A-Z][\w\s&.]+?)(?:\s*[·|\-,]|$)", snippet)
        company = m_at.group(1).strip() if m_at else ""
        results.append({
            "title": title,
            "company": company,
            "location": "",
            "source_name": source_name,
            "source_url": url,
            "application_url": url,
            "publish_date": "",
            "salary_text": "",
            "language_hint": "en",
        })

    # Google layout
    for div in soup.find_all("div", class_=re.compile(r"^g$|tF2Cxc", re.I)):
        title_el = div.find("h3")
        link_el = div.find("a", href=True)
        snippet_el = div.find(class_=re.compile(r"VwiC3b|IsZvec", re.I))
        if not title_el or not link_el:
            continue
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        _append_result(title_el.get_text(strip=True), link_el.get("href", ""), snippet)

    # Bing layout
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a[href]")
        if not a:
            continue
        snippet_el = li.select_one("p")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        _append_result(a.get_text(strip=True), a.get("href", ""), snippet)

    # DuckDuckGo layout
    for item in soup.select("article[data-testid='result'], .result"):
        a = item.select_one("h2 a[href], a.result__a[href]")
        if not a:
            continue
        snippet_el = item.select_one(".result__snippet, [data-result='snippet'], .snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        _append_result(a.get_text(strip=True), a.get("href", ""), snippet)

    # Keep only the domain family implied by the source name.
    if "linkedin" in source_name:
        results = [r for r in results if "linkedin.com" in (r.get("application_url") or "")]
    elif "stepstone" in source_name:
        results = [r for r in results if "stepstone." in (r.get("application_url") or "")]
    elif "indeed" in source_name:
        results = [r for r in results if "indeed." in (r.get("application_url") or "")]

    # Deduplicate proxy results by destination URL.
    seen_urls: set[str] = set()
    uniq: list[dict] = []
    for row in results:
        key = (row.get("application_url") or row.get("source_url") or "").strip().lower()
        if key and key not in seen_urls:
            seen_urls.add(key)
            uniq.append(row)
    results = uniq

    # Enrich google-discovered entries by scraping destination career pages
    # directly (best effort, capped to keep runtime bounded).
    for row in results[:8]:
        enriched = enrich_from_company_page(row)
        row.update(enriched)
    return results


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _infer_language_hint(text: str) -> str:
    t = text.lower()
    if re.search(r"\bdeutsch\b|\bgerman\s+required\b|\bdeutschkenntnisse\b", t):
        return "de"
    return "en"


def _extract_salary(text: str) -> str:
    # Capture common EUR/CHF salary snippets from page text.
    m = re.search(r"((?:EUR|CHF|€)\s?[\d.,]{4,}(?:\s?[-–]\s?(?:EUR|CHF|€)?\s?[\d.,]{4,})?)", text, re.I)
    return _clean_text(m.group(1)) if m else ""


def _extract_company_from_host(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    host = re.sub(r"^www\.", "", host)
    base = host.split(".")[0]
    return base.replace("-", " ").replace("_", " ").title()


def enrich_from_company_page(job: dict) -> dict:
    """
    Best-effort enrichment from the destination page.
    - If a LinkedIn URL is present, try to pivot to company career URL hints on page/snippet.
    - Fetch destination page and extract minimal structured fields.
    Returns only fields that should override existing values.
    """
    url = job.get("application_url") or job.get("source_url") or ""
    if not url:
        return {}

    # Keep Linkedin direct URLs as source reference, but enrich from whatever page we can fetch.
    html = fetch(url, retries=1)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    page_text = _clean_text(soup.get_text(" ", strip=True))

    out: dict[str, str] = {}

    # 1) Try JSON-LD JobPosting on destination
    jsonld_jobs = [normalize_jsonld(j, job.get("source_name", "")) for j in extract_jsonld_jobs(html)]
    if jsonld_jobs:
        best = jsonld_jobs[0]
        if best.get("title"):
            out["title"] = best["title"]
        if best.get("company"):
            out["company"] = best["company"]
        if best.get("location"):
            out["location"] = best["location"]
        if best.get("publish_date"):
            out["publish_date"] = best["publish_date"]
        if best.get("salary_text"):
            out["salary_text"] = best["salary_text"]
        if best.get("application_url"):
            out["application_url"] = best["application_url"]

    # 2) Heuristic fallbacks when JSON-LD is missing
    if not out.get("title"):
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            out["title"] = _clean_text(og_title["content"])
        elif soup.title and soup.title.string:
            out["title"] = _clean_text(soup.title.string)

    if not out.get("company"):
        out["company"] = job.get("company") or _extract_company_from_host(url)

    if not out.get("location"):
        m_loc = re.search(r"\b(Vienna|Wien|Austria|Germany|Switzerland|Zurich|Zürich|Berlin|Munich|München|Graz|Linz|Salzburg|Bolzano|Bozen|South\s*Tyrol|Südtirol)\b", page_text, re.I)
        if m_loc:
            out["location"] = m_loc.group(1)

    if not out.get("salary_text"):
        out["salary_text"] = _extract_salary(page_text)

    out["language_hint"] = _infer_language_hint(page_text)
    return out


# ---------------------------------------------------------------------------
# Site-specific HTML parsers (fallback when JSON-LD is absent)
# ---------------------------------------------------------------------------

def _text(el) -> str:
    return el.get_text(strip=True) if el else ""


def parse_stepstone(html: str, source_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    for card in soup.find_all(True, attrs={"data-at": re.compile(r"job-item")}):
        title_el = card.find(attrs={"data-at": "job-item-title"}) or card.find(["h2", "h3"])
        company_el = card.find(attrs={"data-at": "job-item-company-name"})
        location_el = card.find(attrs={"data-at": "job-item-location"})
        link_el = card.find("a", href=True)
        if not title_el:
            continue
        href = (link_el["href"] if link_el else "")
        if href and not href.startswith("http"):
            base = "https://www.stepstone.at" if "_at" in source_name else "https://www.stepstone.de"
            href = base + href
        jobs.append({
            "title": _text(title_el),
            "company": _text(company_el),
            "location": _text(location_el),
            "source_name": source_name,
            "source_url": href,
            "application_url": href,
            "publish_date": "",
            "salary_text": "",
            "language_hint": "de",
        })
    if not jobs:
        for card in soup.find_all("article", class_=re.compile(r"[Jj]ob[Cc]ard|[Jj]ob[Ii]tem")):
            title_el = card.find(["h2", "h3", "h4"])
            link_el = card.find("a", href=True)
            if not title_el:
                continue
            jobs.append({
                "title": _text(title_el),
                "company": "",
                "location": "",
                "source_name": source_name,
                "source_url": link_el["href"] if link_el else "",
                "application_url": link_el["href"] if link_el else "",
                "publish_date": "",
                "salary_text": "",
                "language_hint": "de",
            })
    return jobs


def parse_karriere_at(html: str, source_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    selectors = [
        {"class": re.compile(r"m-jobsListItem|jobsListItem|job-list-item", re.I)},
        {"class": re.compile(r"JobCard|job-card", re.I)},
    ]
    cards = []
    for sel in selectors:
        cards = soup.find_all(True, attrs=sel)
        if cards:
            break
    for card in cards:
        title_el = card.find(["h2", "h3", "h1"])
        company_el = card.find(class_=re.compile(r"company|employer|firm", re.I))
        link_el = card.find("a", href=True)
        if not title_el:
            continue
        href = link_el["href"] if link_el else ""
        if href and not href.startswith("http"):
            href = "https://www.karriere.at" + href
        jobs.append({
            "title": _text(title_el),
            "company": _text(company_el),
            "location": "Austria",
            "source_name": source_name,
            "source_url": href,
            "application_url": href,
            "publish_date": "",
            "salary_text": "",
            "language_hint": "de",
        })
    return jobs


def parse_jobs_ch(html: str, source_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    for card in soup.find_all(["article", "div", "li"],
                               class_=re.compile(r"job|vacancy", re.I)):
        title_el = card.find(["h2", "h3"])
        company_el = card.find(class_=re.compile(r"company|employer", re.I))
        location_el = card.find(class_=re.compile(r"location|place", re.I))
        link_el = card.find("a", href=True)
        if not title_el:
            continue
        href = link_el["href"] if link_el else ""
        if href and not href.startswith("http"):
            href = "https://www.jobs.ch" + href
        jobs.append({
            "title": _text(title_el),
            "company": _text(company_el),
            "location": _text(location_el) or "Switzerland",
            "source_name": source_name,
            "source_url": href,
            "application_url": href,
            "publish_date": "",
            "salary_text": "",
            "language_hint": "en",
        })
    return jobs


PARSER_MAP = {
    # html sources
    "stepstone_at_cto":    parse_stepstone,
    "stepstone_at_hoe":    parse_stepstone,
    "stepstone_de_hoe":    parse_stepstone,
    "karriere_at_cto":     parse_karriere_at,
    "karriere_at_hoe":     parse_karriere_at,
    "karriere_at_software": parse_karriere_at,
    "karriere_at_platform": parse_karriere_at,
    "karriere_at_cloud":    parse_karriere_at,
    "jobs_ch":             parse_jobs_ch,
    # rss sources (also used by scrape_extra.py for any indeed-like suggestion)
    "indeed_at_rss":       parse_rss,
    # google proxy sources
    "google_linkedin_at":  parse_google_jobs,
    "google_linkedin_de":  parse_google_jobs,
    "google_jobs_at":      parse_google_jobs,
    "google_jobs_bolzano": parse_google_jobs,
    "bing_linkedin_at":    parse_google_jobs,
    "bing_linkedin_de":    parse_google_jobs,
    "ddg_linkedin_dach":   parse_google_jobs,
    "bing_stepstone_dach": parse_google_jobs,
    "bing_indeed_dach":    parse_google_jobs,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs("/tmp/jobs", exist_ok=True)
    all_jobs: list[dict] = []
    stats: dict[str, int] = {}

    for src in SOURCES:
        name = src["name"]
        url = src["url"]
        src_type = src.get("type", "html")
        print(f"\nFetching {name} [{src_type}] ...")

        content = fetch(url)
        if not content:
            print(f"  SKIP {name}: fetch failed")
            stats[name] = 0
            continue

        if src_type == "rss":
            jobs = parse_rss(content, name)
            print(f"  RSS: {len(jobs)} jobs")
        elif src_type in {"google_proxy", "search_proxy"}:
            jobs = parse_google_jobs(content, name)
            print(f"  Google proxy: {len(jobs)} jobs")
        else:
            # html: try JSON-LD first, fall back to site-specific HTML parser
            jobs = [normalize_jsonld(j, name) for j in extract_jsonld_jobs(content)]
            if jobs:
                print(f"  JSON-LD: {len(jobs)} jobs")
            else:
                parser = PARSER_MAP.get(name)
                if parser:
                    jobs = parser(content, name)
                    print(f"  HTML parser ({parser.__name__}): {len(jobs)} jobs")
                else:
                    print(f"  No parser for {name}")
                    jobs = []

        # Drop records without a title
        jobs = [j for j in jobs if j.get("title")]
        stats[name] = len(jobs)
        all_jobs.extend(jobs)

    print(f"\nTotal raw: {len(all_jobs)} across {len(SOURCES)} sources")
    print(f"Stats: {stats}")

    # Deduplicate by title+company before the output cap so triplication
    # in site-specific parsers doesn't inflate counts or waste the cap quota.
    _seen_raw: set[str] = set()
    _unique: list[dict] = []
    for j in all_jobs:
        fp = f"{(j.get('title') or '').lower().strip()}|{(j.get('company') or '').lower().strip()}"
        if fp not in _seen_raw:
            _seen_raw.add(fp)
            _unique.append(j)
    all_jobs = _unique
    print(f"After raw dedup: {len(all_jobs)} unique jobs")

    output = {
        "date": str(date.today()),
        "stats": stats,
        "jobs": all_jobs[:200],  # cap to limit downstream token use
    }
    out_path = "/tmp/jobs/jobs_raw.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
