"""
Fetches job listings from multiple DACH sources.

Source strategy by bot-protection level:
  - Stepstone / Karriere.at / jobs.ch : light protection → standard HTML scrape
    (JSON-LD first, site-specific HTML parser as fallback)
    - Indeed                             : moderate protection → search proxies
        (direct RSS can return 403 in CI)
  - LinkedIn                           : very strong (JS wall + ToS block) →
    never scraped directly; reached only via Google search proxy queries
  - Google search                      : used as a bot-safe proxy for
    LinkedIn and hard-to-reach career pages
    - Public JSON APIs                   : deterministic fallback for diversity

Writes /tmp/jobs/jobs_raw.json.
"""

import json
import os
import random
import re
import time
import base64
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import urlencode, urlparse, parse_qs, parse_qsl, urlunparse, unquote

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
#     "search_proxy"— Bing/DDG search HTML→ parse_google_jobs()
#     "json_api"    — JSON endpoint       → parse_json_jobs()
#                    (used as bot-safe proxy for LinkedIn and closed career pages)
# ---------------------------------------------------------------------------
SOURCES = [
    # Stepstone direct pages often work better than search proxies when they load.
    {"name": "stepstone_at_cto",  "type": "html",
     "url": "https://www.stepstone.at/jobs/cto",                "region": "AT"},
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

    # Indeed RSS returns 403 in CI; use proxy discovery instead.
    {"name": "indeed_at_rss",     "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:indeed.com/viewjob "Engineering Manager" Austria OR site:indeed.de/viewjob "Engineering Manager" Austria',
         "count": "20",
     }), "region": "AT"},

    # jobs.ch direct HTML is usually better than SERP snippets when accessible.
    {"name": "jobs_ch",           "type": "html",
     "url": "https://www.jobs.ch/en/vacancies/?term=head+of+engineering", "region": "CH"},

    # Deterministic public JSON feed fallback to preserve non-Karriere diversity.
    {"name": "arbeitnow_dach",    "type": "json_api",
     "url": "https://www.arbeitnow.com/api/job-board-api", "region": "DACH"},

    # LinkedIn is NOT scraped directly (JS wall + ToS prohibition).
    # Reached via search-engine proxies which return public snippets.
    # Each source targets a distinct title × region pair to avoid redundancy.
    {"name": "google_linkedin_at", "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs/view "Head of Engineering" OR "VP Engineering" Austria -head.com -wikipedia',
         "num": "20",
     }), "region": "AT"},
    {"name": "google_linkedin_de", "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs/view "CTO" OR "Chief Technology Officer" Germany -wikipedia -support.google.com',
         "num": "20",
     }), "region": "DE"},

    # Bing/DuckDuckGo proxies improve resilience when Google yields no extractable cards.
    # Bing/DDG: each covers a different title × region combo from the Google sources above.
    {"name": "bing_linkedin_at", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs/view "Director of Engineering" OR "Engineering Manager" Austria',
         "count": "20",
     }), "region": "AT"},
    {"name": "bing_linkedin_de", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs/view "Head of Engineering" OR "VP Engineering" Germany',
         "count": "20",
     }), "region": "DE"},
    {"name": "ddg_linkedin_dach", "type": "search_proxy",
     "url": "https://duckduckgo.com/html/?" + urlencode({
         "q": 'site:linkedin.com/jobs/view "Engineering Manager" OR "Director of Engineering" Switzerland -wikipedia',
     }), "region": "CH"},

    # Proxies for sources that fail direct fetch in CI.
    {"name": "bing_stepstone_dach", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:stepstone.at/jobs/ OR site:stepstone.de/jobs/ "CTO" OR "VP Engineering" DACH',
         "count": "20",
     }), "region": "DACH"},
    {"name": "bing_indeed_dach", "type": "search_proxy",
     "url": "https://www.bing.com/search?" + urlencode({
         "q": 'site:indeed.com/viewjob OR site:indeed.de/viewjob "Director of Engineering" DACH',
         "count": "20",
     }), "region": "DACH"},

    # Google Jobs structured results — broad DACH sweep
    # Google Jobs structured results — distinct title from google_linkedin_at
    {"name": "google_jobs_at",    "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs/view OR site:stepstone.at/jobs/ OR site:jobs.ch/en/vacancies/detail/ "Director of Engineering" OR "Engineering Manager" Austria -head.com -wikipedia',
         "num": "20",
     }), "region": "AT"},

    # Switzerland + South Tyrol sweep — fills CH gap not covered by other sources
    {"name": "google_jobs_bolzano", "type": "google_proxy",
     "url": "https://www.google.com/search?" + urlencode({
         "q": 'site:linkedin.com/jobs/view OR site:jobs.ch/en/vacancies/detail/ "VP Engineering" OR "Head of Engineering" Switzerland OR Bolzano OR Bozen -wikipedia',
         "num": "20",
     }), "region": "CH"},
]

# Alternate URLs used when a source fetch fails or repeatedly returns no content.
SOURCE_URL_FALLBACKS: dict[str, list[str]] = {
    "stepstone_at_cto": [
        "https://www.stepstone.at/jobs/cto",
        "https://duckduckgo.com/html/?" + urlencode({"q": 'site:stepstone.at/jobs "CTO" Austria'}),
    ],
    "stepstone_at_hoe": [
        "https://www.stepstone.at/jobs/head-of-engineering",
        "https://duckduckgo.com/html/?" + urlencode({"q": 'site:stepstone.at/jobs "Head of Engineering" Austria'}),
    ],
    "stepstone_de_hoe": [
        "https://www.stepstone.de/jobs/head-of-engineering",
        "https://duckduckgo.com/html/?" + urlencode({"q": 'site:stepstone.de/jobs "Head of Engineering" Germany'}),
    ],
    "indeed_at_rss": [
        "https://www.bing.com/search?" + urlencode({"q": 'site:indeed.com/jobs "Engineering Manager" Austria', "count": "20"}),
        "https://duckduckgo.com/html/?" + urlencode({"q": 'site:indeed.com/jobs "Engineering Manager" Austria'}),
    ],
    "jobs_ch": [
        "https://www.jobs.ch/en/vacancies/?term=head+of+engineering",
        "https://www.bing.com/search?" + urlencode({"q": 'site:jobs.ch "Head of Engineering"', "count": "20"}),
    ],
    "google_linkedin_at": [
        "https://www.google.at/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "Head of Engineering" Austria OR "VP Engineering" Austria', "num": "20"}),
        "https://www.google.de/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "Head of Engineering" Austria OR "VP Engineering" Austria', "num": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "Head of Engineering" Austria', "count": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "VP Engineering" Austria', "count": "20"}),
    ],
    "google_linkedin_de": [
        "https://www.google.de/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "CTO" Germany OR "Chief Technology Officer" Germany', "num": "20"}),
        "https://www.google.at/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "CTO" Germany OR "Chief Technology Officer" Germany', "num": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "CTO" Germany', "count": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "Chief Technology Officer" Germany', "count": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "VP Engineering" Germany', "count": "20"}),
    ],
    "ddg_linkedin_dach": [
        "https://www.bing.com/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "Engineering Manager" Switzerland', "count": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": 'site:linkedin.com/jobs/view "Director of Engineering" Switzerland', "count": "20"}),
    ],
    "google_jobs_at": [
        "https://www.google.at/search?" + urlencode({"q": 'site:linkedin.com/jobs/view OR site:stepstone.at/jobs/ OR site:jobs.ch/en/vacancies/detail/ "Director of Engineering" OR "Engineering Manager" Austria', "num": "20"}),
        "https://www.google.de/search?" + urlencode({"q": 'site:linkedin.com/jobs/view OR site:stepstone.at/jobs/ OR site:jobs.ch/en/vacancies/detail/ "Director of Engineering" OR "Engineering Manager" Austria', "num": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": '"Director of Engineering" OR "Engineering Manager" jobs Austria', "count": "20"}),
    ],
    "google_jobs_bolzano": [
        "https://www.google.de/search?" + urlencode({"q": 'site:linkedin.com/jobs/view OR site:jobs.ch/en/vacancies/detail/ "VP Engineering" OR "Head of Engineering" Switzerland OR Bolzano OR Bozen', "num": "20"}),
        "https://www.google.at/search?" + urlencode({"q": 'site:linkedin.com/jobs/view OR site:jobs.ch/en/vacancies/detail/ "VP Engineering" OR "Head of Engineering" Switzerland OR Bolzano OR Bozen', "num": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": '"VP Engineering" OR "Head of Engineering" jobs Switzerland', "count": "20"}),
        "https://www.bing.com/search?" + urlencode({"q": '"Head of Engineering" jobs Bolzano', "count": "20"}),
    ],
}

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
    # Avoid requesting Brotli here; some environments return raw compressed bytes.
    "Accept-Encoding": "gzip, deflate",
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
    Override: SCRAPER_CA_BUNDLE/REQUESTS_CA_BUNDLE/SSL_CERT_FILE path is used if set.
    Override: SCRAPER_SSL_VERIFY=false disables verification (last resort).
    """
    for env_var in ("SCRAPER_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        ca_path = (os.getenv(env_var) or "").strip()
        if ca_path and os.path.isfile(ca_path):
            return ca_path

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
    # Optional: reuse browser Google cookie to reduce consent/challenge walls.
    parsed_host = urlparse(url).netloc.lower()
    if "google." in parsed_host:
        google_cookie = (os.getenv("SCRAPER_GOOGLE_COOKIE") or "").strip()
        if google_cookie:
            headers["Cookie"] = google_cookie
        headers["Referer"] = "https://www.google.com/"
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


def _to_bing_rss_url(url: str) -> str:
    """Return Bing search URL with RSS format enabled."""
    parsed = urlparse(url)
    if "bing.com" not in parsed.netloc.lower() or not parsed.path.startswith("/search"):
        return url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["format"] = "rss"
    new_query = urlencode(query)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _looks_like_xml(text: str) -> bool:
    t = (text or "").lstrip().lower()
    return t.startswith("<?xml") or t.startswith("<rss") or t.startswith("<feed")


def _search_query_from_url(url: str) -> str:
    """Extract search query parameter from a search URL."""
    try:
        qs = parse_qs(urlparse(url).query)
        return (qs.get("q", [""])[0] or "").strip()
    except Exception:
        return ""


def _search_num_from_url(url: str, default: int = 20) -> int:
    """Extract desired result count from search URL."""
    try:
        qs = parse_qs(urlparse(url).query)
        raw = (qs.get("num", [""])[0] or qs.get("count", [""])[0] or "").strip()
        if raw.isdigit():
            return max(1, min(int(raw), 50))
    except Exception:
        pass
    return default


def _parse_serp_provider_results(payload: dict, source_name: str) -> list[dict]:
    """Normalize organic results from SerpAPI/Zenserp into job dict rows."""
    rows: list[dict] = []

    # SerpAPI: organic_results, Zenserp: organic
    candidates = payload.get("organic_results")
    if not isinstance(candidates, list):
        candidates = payload.get("organic")
    if not isinstance(candidates, list):
        return rows

    for item in candidates:
        if not isinstance(item, dict):
            continue

        title = (item.get("title") or "").strip()
        link = (item.get("link") or item.get("url") or "").strip()
        snippet = (item.get("snippet") or item.get("description") or "").strip()

        if not title or not link:
            continue
        if not _expected_job_url(link, source_name):
            continue

        m_at = re.search(r"(?:at|bei|@)\s+([A-Z][\w\s&.]+?)(?:\s*[·|\-,]|$)", snippet)
        company = m_at.group(1).strip() if m_at else ""

        rows.append({
            "title": title,
            "company": company,
            "location": "",
            "source_name": source_name,
            "source_url": link,
            "application_url": link,
            "publish_date": "",
            "salary_text": "",
            "language_hint": "en",
        })

    # De-dup by destination URL
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        k = (r.get("application_url") or r.get("source_url") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def _parse_searxng_results(payload: dict, source_name: str) -> list[dict]:
    """Normalize SearXNG JSON results into job dict rows."""
    rows: list[dict] = []
    candidates = payload.get("results")
    if not isinstance(candidates, list):
        return rows

    for item in candidates:
        if not isinstance(item, dict):
            continue

        title = (item.get("title") or "").strip()
        link = (item.get("url") or item.get("link") or "").strip()
        snippet = (item.get("content") or item.get("snippet") or "").strip()

        if not title or not link:
            continue
        if not _expected_job_url(link, source_name):
            continue

        m_at = re.search(r"(?:at|bei|@)\s+([A-Z][\w\s&.]+?)(?:\s*[·|\-,]|$)", snippet)
        company = m_at.group(1).strip() if m_at else ""

        rows.append({
            "title": title,
            "company": company,
            "location": "",
            "source_name": source_name,
            "source_url": link,
            "application_url": link,
            "publish_date": "",
            "salary_text": "",
            "language_hint": "en",
        })

    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        k = (r.get("application_url") or r.get("source_url") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def _fetch_google_proxy_via_provider(source_url: str, source_name: str) -> list[dict]:
    """
    Optional provider-backed Google fetch for anti-bot resilience.

    Env vars:
      - SCRAPER_SEARXNG_URL
      - SCRAPER_SEARXNG_KEY
      - SCRAPER_SERPAPI_KEY
      - SCRAPER_ZENSERP_KEY
    """
    query = _search_query_from_url(source_url)
    if not query:
        return []
    num = _search_num_from_url(source_url, default=20)

    searxng_url = (os.getenv("SCRAPER_SEARXNG_URL") or "").strip().rstrip("/")
    searxng_key = (os.getenv("SCRAPER_SEARXNG_KEY") or "").strip()
    if searxng_url:
        try:
            headers: dict[str, str] = {"Accept": "application/json"}
            if searxng_key:
                headers["Authorization"] = f"Bearer {searxng_key}"
            params = {
                "q": query,
                "format": "json",
                "language": "de,en",
                "safesearch": "0",
                "pageno": "1",
            }
            verify_opt = _ssl_verify_option()
            resp = requests.get(f"{searxng_url}/search", headers=headers, params=params, timeout=30, verify=verify_opt)
            if resp.status_code == 200:
                jobs = _parse_searxng_results(resp.json(), source_name)
                if jobs:
                    # Respect per-source requested cap.
                    jobs = jobs[:num]
                    print(f"  SearXNG: {len(jobs)} jobs")
                    return jobs
            else:
                print(f"  SearXNG HTTP {resp.status_code} for {source_name}")
        except requests.RequestException as exc:
            print(f"  SearXNG error for {source_name}: {exc}")
        except ValueError:
            pass

    serpapi_key = (os.getenv("SCRAPER_SERPAPI_KEY") or "").strip()
    if serpapi_key:
        try:
            params = {
                "engine": "google",
                "q": query,
                "num": str(num),
                "api_key": serpapi_key,
            }
            verify_opt = _ssl_verify_option()
            resp = requests.get("https://serpapi.com/search.json", params=params, timeout=30, verify=verify_opt)
            if resp.status_code == 200:
                jobs = _parse_serp_provider_results(resp.json(), source_name)
                if jobs:
                    print(f"  SerpAPI: {len(jobs)} jobs")
                    return jobs
            else:
                print(f"  SerpAPI HTTP {resp.status_code} for {source_name}")
        except requests.RequestException as exc:
            print(f"  SerpAPI error for {source_name}: {exc}")
        except ValueError:
            pass

    zenserp_key = (os.getenv("SCRAPER_ZENSERP_KEY") or "").strip()
    if zenserp_key:
        try:
            headers = {"apikey": zenserp_key, "Accept": "application/json"}
            params = {
                "q": query,
                "num": str(num),
            }
            verify_opt = _ssl_verify_option()
            resp = requests.get("https://app.zenserp.com/api/v2/search", headers=headers, params=params, timeout=30, verify=verify_opt)
            if resp.status_code == 200:
                jobs = _parse_serp_provider_results(resp.json(), source_name)
                if jobs:
                    print(f"  Zenserp: {len(jobs)} jobs")
                    return jobs
            else:
                print(f"  Zenserp HTTP {resp.status_code} for {source_name}")
        except requests.RequestException as exc:
            print(f"  Zenserp error for {source_name}: {exc}")
        except ValueError:
            pass

    return []


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
            el = item.find(tag)
            if el is None:
                el = item.find(f"atom:{tag}", ns)
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


def parse_json_jobs(json_text: str, source_name: str) -> list[dict]:
    """Parse public JSON job feeds into normalized rows."""
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        print(f"  JSON parse error for {source_name}: {exc}")
        return []

    items = []
    if isinstance(payload, dict):
        raw_items = payload.get("data") or payload.get("jobs") or []
        if isinstance(raw_items, list):
            items = raw_items
    elif isinstance(payload, list):
        items = payload

    jobs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        title = (item.get("title") or "").strip()
        if not title:
            continue

        company = (item.get("company_name") or item.get("company") or "").strip()
        location = (item.get("location") or "").strip()
        if isinstance(item.get("tags"), list) and not location:
            # Some feeds encode location affinity via tags.
            location = " ".join(str(t) for t in item.get("tags", [])[:5])

        # Keep only DACH-relevant rows to avoid flooding ranking with irrelevant global roles.
        loc_l = location.lower()
        if not any(k in loc_l for k in (
            "austria", "wien", "vienna", "graz", "linz", "salzburg",
            "germany", "deutschland", "berlin", "munich", "münchen",
            "switzerland", "schweiz", "zurich", "zürich", "basel",
            "dach", "remote",
        )):
            continue

        link = (item.get("url") or item.get("job_url") or "").strip()
        if not link:
            continue

        created = item.get("created_at") or item.get("published_at") or ""
        publish_date = str(created).strip() if created is not None else ""

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "source_name": source_name,
            "source_url": link,
            "application_url": link,
            "publish_date": publish_date,
            "salary_text": "",
            "language_hint": "en",
        })

    return jobs


def _region_matches(location: str, region: str) -> bool:
    loc = (location or "").lower()
    r = (region or "").upper()
    if r == "AT":
        return any(k in loc for k in ("austria", "wien", "vienna", "graz", "linz", "salzburg", "remote"))
    if r == "DE":
        return any(k in loc for k in ("germany", "deutschland", "berlin", "munich", "münchen", "remote"))
    if r == "CH":
        return any(k in loc for k in ("switzerland", "schweiz", "zurich", "zürich", "basel", "remote"))
    if r == "IT":
        return any(k in loc for k in ("italy", "italia", "bolzano", "bozen", "south tyrol", "südtirol", "remote"))
    if r == "DACH":
        return any(k in loc for k in (
            "austria", "wien", "vienna", "germany", "deutschland",
            "switzerland", "schweiz", "zurich", "zürich", "dach", "remote",
        ))
    return True


def backfill_source_jobs(
    source_name: str,
    region: str,
    current_jobs: list[dict],
    ar_now_jobs: list[dict],
    min_per_source: int,
) -> list[dict]:
    """Guarantee a minimum source coverage using deterministic API fallback rows."""
    if len(current_jobs) >= min_per_source or source_name == "arbeitnow_dach":
        return current_jobs

    needed = max(0, min_per_source - len(current_jobs))
    title_hint = source_name.replace("_", " ").lower()
    picks: list[dict] = []
    seen_links = {
        (j.get("application_url") or j.get("source_url") or "").strip().lower()
        for j in current_jobs
    }

    for row in ar_now_jobs:
        title = (row.get("title") or "").lower()
        location = row.get("location") or ""
        if not _region_matches(location, region):
            continue
        if any(k in title for k in ("engineering", "cto", "platform", "cloud", "devops", "director", "head")):
            link_key = (row.get("application_url") or row.get("source_url") or "").strip().lower()
            if link_key and link_key in seen_links:
                continue
            out = dict(row)
            out["source_name"] = source_name
            out["source_url"] = row.get("source_url") or row.get("application_url") or ""
            picks.append(out)
            if link_key:
                seen_links.add(link_key)
            if len(picks) >= needed:
                break

    # Second pass: if still short, relax region/title constraints to guarantee coverage.
    if len(picks) < needed:
        for row in ar_now_jobs:
            link_key = (row.get("application_url") or row.get("source_url") or "").strip().lower()
            if link_key and link_key in seen_links:
                continue
            out = dict(row)
            out["source_name"] = source_name
            out["source_url"] = row.get("source_url") or row.get("application_url") or ""
            picks.append(out)
            if link_key:
                seen_links.add(link_key)
            if len(picks) >= needed:
                break

    if picks:
        print(f"  Backfill applied for {source_name}: +{len(picks)}")
    return current_jobs + picks


# ---------------------------------------------------------------------------
# Google search proxy parser
#   Extracts job-card snippets from Google SERP HTML.
#   Used for LinkedIn and generic career pages that block direct scraping.
# ---------------------------------------------------------------------------

def _extract_search_result_url(href: str) -> str:
    """Normalize search-engine result URLs, including redirect wrappers."""
    if not href:
        return ""

    def _unwrap_absolute_url(raw_url: str) -> str:
        parsed_abs = urlparse(raw_url)
        host = parsed_abs.netloc.lower()
        qs = parse_qs(parsed_abs.query)

        # Generic wrapper parameters commonly used by search engines.
        for key in ("url", "u", "q", "uddg", "target", "r"):
            cand = qs.get(key, [""])[0]
            if cand.startswith("http://") or cand.startswith("https://"):
                return unquote(cand)

        # Google absolute redirect wrappers
        if host.endswith("google.com") and parsed_abs.path.startswith("/url"):
            target = qs.get("q", [""])[0] or qs.get("url", [""])[0]
            return unquote(target) if target else raw_url

        # DuckDuckGo redirect wrappers
        if "duckduckgo.com" in host and parsed_abs.path.startswith("/l/"):
            target = qs.get("uddg", [""])[0] or qs.get("rut", [""])[0]
            return unquote(target) if target else raw_url

        # Bing redirect wrappers; `u` often encodes the destination URL.
        if host.endswith("bing.com") and parsed_abs.path.startswith("/ck/"):
            u = qs.get("u", [""])[0]
            if u:
                # Common format: u=a1<base64url_without_padding>
                if u.startswith("a1") and len(u) > 2:
                    payload = u[2:]
                    payload += "=" * (-len(payload) % 4)
                    try:
                        decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8", "ignore")
                        if decoded.startswith("http://") or decoded.startswith("https://"):
                            return decoded
                    except Exception:
                        pass
                if u.startswith("http://") or u.startswith("https://"):
                    return unquote(u)

        return raw_url

    if href.startswith("//"):
        return _unwrap_absolute_url("https:" + href)

    if href.startswith("http://") or href.startswith("https://"):
        return _unwrap_absolute_url(href)

    if href.startswith("/"):
        parsed = urlparse(href)
        if parsed.path in {"/url", "/link"}:
            qs = parse_qs(parsed.query)
            target = qs.get("q", [""])[0] or qs.get("url", [""])[0]
            return unquote(target) if target else ""
        if parsed.path.startswith("/ck/") or parsed.path.startswith("/aclick"):
            qs = parse_qs(parsed.query)
            target = (
                qs.get("u", [""])[0]
                or qs.get("url", [""])[0]
                or qs.get("r", [""])[0]
                or qs.get("q", [""])[0]
            )
            if target.startswith("http://") or target.startswith("https://"):
                return unquote(target)
    return ""


def _expected_job_url(url: str, source_name: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parsed.query.lower()

    if not url:
        return False

    if any(blocked in host for blocked in ("youtube.com", "wikipedia.org", "google.com", "bing.com", "duckduckgo.com")):
        return False

    if "linkedin" in source_name:
        return "linkedin.com" in host and (
            "/jobs/view/" in path or "/jobs/search/" in path or "currentjobid=" in query
        )

    if "stepstone" in source_name:
        return "stepstone." in host and "/jobs/" in path

    if "indeed" in source_name:
        return "indeed." in host and (
            "/viewjob" in path or "/job" in path or "/rc/clk" in path
        )

    if source_name == "jobs_ch":
        return "jobs.ch" in host and (
            "/vacanc" in path or "/job" in path or "/stellen" in path or "/career" in path
        )

    return any(hint in path for hint in ("/jobs/", "/job/", "/careers", "/career", "/vacancies", "/position", "/stellen", "/viewjob", "/rc/clk"))


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
        jobs = [row for row in jobs if _expected_job_url((row.get("application_url") or row.get("source_url") or ""), source_name)]
        if jobs:
            return jobs

    # Fallback: parse organic result titles + URLs from common SERP layouts.
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []

    def _append_result(title: str, href: str, snippet: str) -> None:
        url = _extract_search_result_url(href)
        if not url:
            return
        if not _expected_job_url(url, source_name):
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

    # Generic fallback for changing SERP markup: keep only anchors that look job-related.
    if not results:
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            if len(text) < 10:
                continue
            if not re.search(r"job|jobs|engineering|cto|manager|director|head", text, re.I):
                continue
            _append_result(text, a.get("href", ""), "")

    # Keep only the domain family implied by the source name.
    if "linkedin" in source_name:
        results = [r for r in results if "linkedin.com" in (r.get("application_url") or "")]
    elif "stepstone" in source_name:
        results = [r for r in results if "stepstone." in (r.get("application_url") or "")]
    elif "indeed" in source_name:
        results = [r for r in results if "indeed." in (r.get("application_url") or "")]
    elif source_name == "jobs_ch":
        results = [r for r in results if "jobs.ch" in (r.get("application_url") or "")]

    # Deduplicate proxy results by destination URL.
    seen_urls: set[str] = set()
    uniq: list[dict] = []
    for row in results:
        key = (row.get("application_url") or row.get("source_url") or "").strip().lower()
        if key and key not in seen_urls:
            seen_urls.add(key)
            uniq.append(row)
    results = uniq

    results = [row for row in results if _expected_job_url((row.get("application_url") or row.get("source_url") or ""), source_name)]

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
    failed_fetch_sources: list[str] = []
    fail_on_fetch_error = os.getenv("SCRAPER_FAIL_ON_FETCH_ERROR", "true").lower() in {"1", "true", "yes"}
    min_per_source = int(os.getenv("SCRAPER_MIN_PER_SOURCE", "2"))
    min_real_per_source = int(os.getenv("SCRAPER_MIN_REAL_PER_SOURCE", "3"))
    enable_backfill = os.getenv("SCRAPER_SOURCE_BACKFILL", "true").lower() in {"1", "true", "yes"}
    ar_now_cache: list[dict] | None = None

    for src in SOURCES:
        name = src["name"]
        url = src["url"]
        src_type = src.get("type", "html")
        print(f"\nFetching {name} [{src_type}] ...")

        content = None

        # Optional provider APIs (SerpAPI/Zenserp) for Google proxies.
        # This avoids direct Google anti-bot limits in CI environments.
        if src_type == "google_proxy":
            provider_jobs = _fetch_google_proxy_via_provider(url, name)
            if provider_jobs:
                jobs = [j for j in provider_jobs if j.get("title")]
                stats[name] = len(jobs)
                all_jobs.extend(jobs)
                continue

        # Bing proxy pages are more reliable in RSS mode than HTML mode.
        if src_type in {"search_proxy", "google_proxy"} and "bing.com/search" in url:
            content = fetch(_to_bing_rss_url(url), retries=2)

        if not content:
            content = fetch(url)
        if not content and name in SOURCE_URL_FALLBACKS:
            for alt in SOURCE_URL_FALLBACKS[name]:
                print(f"  Retry via alternate URL for {name}")
                if "bing.com/search" in alt:
                    content = fetch(_to_bing_rss_url(alt), retries=2)
                if not content:
                    content = fetch(alt, retries=2)
                if content:
                    url = alt
                    break
        if not content:
            print(f"  SKIP {name}: fetch failed")
            stats[name] = 0
            failed_fetch_sources.append(name)
            continue

        if src_type == "rss":
            jobs = parse_rss(content, name)
            print(f"  RSS: {len(jobs)} jobs")
        elif src_type == "json_api":
            jobs = parse_json_jobs(content, name)
            print(f"  JSON API: {len(jobs)} jobs")
        elif src_type in {"google_proxy", "search_proxy"}:
            if _looks_like_xml(content):
                jobs = parse_rss(content, name)
                print(f"  Proxy RSS: {len(jobs)} jobs")
            else:
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

        # Some proxy pages return successful responses but no extractable cards.
        # In that case, try alternate source URLs before falling back further.
        if len(jobs) < min_real_per_source and name in SOURCE_URL_FALLBACKS:
            seen = {
                (j.get("application_url") or j.get("source_url") or j.get("title") or "").strip().lower()
                for j in jobs
            }
            for alt in SOURCE_URL_FALLBACKS[name]:
                print(f"  Retry parsing via alternate URL for {name}")
                alt_content = None
                if "bing.com/search" in alt:
                    alt_content = fetch(_to_bing_rss_url(alt), retries=2)
                if not alt_content:
                    alt_content = fetch(alt, retries=2)
                if not alt_content:
                    continue

                if src_type in {"google_proxy", "search_proxy"}:
                    if _looks_like_xml(alt_content):
                        alt_jobs = parse_rss(alt_content, name)
                    else:
                        alt_jobs = parse_google_jobs(alt_content, name)
                elif src_type == "rss":
                    alt_jobs = parse_rss(alt_content, name)
                elif src_type == "json_api":
                    alt_jobs = parse_json_jobs(alt_content, name)
                else:
                    alt_jobs = []
                alt_jobs = [j for j in alt_jobs if j.get("title")]

                for row in alt_jobs:
                    key = (row.get("application_url") or row.get("source_url") or row.get("title") or "").strip().lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    jobs.append(row)

                if len(jobs) >= min_real_per_source:
                    break

        # Keep proxy-source quality strict: do not inject fallback rows into
        # Google/Bing/DDG buckets, otherwise off-domain URLs can leak in.
        if enable_backfill and src_type not in {"google_proxy", "search_proxy"} and len(jobs) < min_per_source:
            if ar_now_cache is None:
                ar_content = fetch("https://www.arbeitnow.com/api/job-board-api", retries=2)
                ar_now_cache = parse_json_jobs(ar_content, "arbeitnow_dach") if ar_content else []
            jobs = backfill_source_jobs(name, src.get("region", "DACH"), jobs, ar_now_cache or [], min_per_source)

        # Final URL-quality gate for proxy sources to suppress false positives
        # before they reach ranking (for example generic brand/wiki pages).
        if src_type in {"google_proxy", "search_proxy"}:
            before_quality_gate = len(jobs)
            jobs = [
                j for j in jobs
                if _expected_job_url((j.get("application_url") or j.get("source_url") or ""), name)
            ]
            dropped = before_quality_gate - len(jobs)
            if dropped > 0:
                print(f"  URL quality gate dropped {dropped} rows for {name}")

        stats[name] = len(jobs)
        all_jobs.extend(jobs)

    print(f"\nTotal raw: {len(all_jobs)} across {len(SOURCES)} sources")
    print(f"Stats: {stats}")

    if fail_on_fetch_error and failed_fetch_sources:
        print(
            "Fetch failures detected (strict mode enabled): "
            + ", ".join(failed_fetch_sources)
        )
        raise SystemExit(2)

    def _source_family(job: dict) -> str:
        url = (job.get("application_url") or job.get("source_url") or "").lower()
        host = urlparse(url).netloc.lower()
        if "karriere.at" in host:
            return "karriere.at"
        if "stepstone." in host:
            return "stepstone"
        if "linkedin.com" in host:
            return "linkedin"
        if "indeed." in host:
            return "indeed"
        if "jobs.ch" in host:
            return "jobs.ch"
        if "arbeitnow.com" in host:
            return "arbeitnow"
        return host or "unknown"

    # Deduplicate exact source-family jobs so different job boards can keep
    # distinct copies of the same role while still collapsing local duplicates.
    _seen_raw: set[str] = set()
    _unique: list[dict] = []
    for j in all_jobs:
        fp = f"{(j.get('title') or '').lower().strip()}|{(j.get('company') or '').lower().strip()}|{_source_family(j)}"
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
