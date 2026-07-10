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

# Optional statistical language detector. Pure-Python, CI-friendly and
# deterministic (see seed below). When it is not installed the pipeline falls
# back to the dependency-free heuristic in `_infer_language_hint`, so this is a
# soft/optional enhancement — never a hard requirement.
try:
    from langdetect import detect_langs as _ld_detect_langs
    from langdetect import DetectorFactory as _LdDetectorFactory

    _LdDetectorFactory.seed = 0  # deterministic output across runs
except Exception:  # pragma: no cover - optional dependency
    _ld_detect_langs = None

# ---------------------------------------------------------------------------
# Source registry
#   type:
#     "html"        — standard HTTP fetch → JSON-LD / HTML parser
#     "rss"         — XML RSS/Atom feed   → parse_rss()
#     "google_proxy"— Google search HTML  → parse_google_jobs()
#     "search_proxy"— Bing/DDG search HTML→ parse_google_jobs()
#     "json_api"    — JSON endpoint       → parse_json_jobs()
#                    (used as bot-safe proxy for LinkedIn and closed career pages)
#
#   The concrete scrape targets — search keywords, locations, page offsets and
#   query strings — live in an external JSON config so they can be tuned without
#   editing this file. Only the SEARCH CONTENT is external; the type→parser
#   wiring and name-prefix contracts (PARSER_MAP, _source_domain_ok, …) stay in
#   Python. See .github/config/sources.json and _load_source_config() below.
# ---------------------------------------------------------------------------

# Location of the external source registry. Overridable via env for tests /
# alternate rota files.
SOURCES_CONFIG_PATH = os.getenv(
    "SCRAPER_SOURCES_CONFIG",
    os.path.join(os.path.dirname(__file__), "..", "config", "sources.json"),
)

_LINKEDIN_GUEST_SEARCH = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
)
_GOOGLE_SEARCH = "https://www.google.com/search?"

# Source types that must resolve to a parser / handler at scrape time. Used by
# the config validator to fail fast on an unknown type.
_KNOWN_SOURCE_TYPES = {"html", "rss", "google_proxy", "search_proxy", "json_api", "linkedin_api"}


def _build_source_url(entry: dict, keyword_sets: dict) -> str:
    """
    Build a source's fetch URL from its config entry.

    An explicit ``url`` always wins. Otherwise the URL is templated from the
    entry's type (matching the historical hand-written URLs byte-for-byte):
      - linkedin_api : keywords (literal or via keyword_set) + location + start
      - google_proxy : query + num
    """
    if entry.get("url"):
        return entry["url"]

    stype = entry.get("type")
    if stype == "linkedin_api":
        keywords = entry.get("keywords")
        if not keywords:
            ks = entry.get("keyword_set")
            if ks not in keyword_sets:
                raise ValueError(
                    f"source {entry.get('name')!r} references unknown keyword_set {ks!r}"
                )
            keywords = keyword_sets[ks]
        return _LINKEDIN_GUEST_SEARCH + urlencode({
            "keywords": keywords,
            "location": entry.get("location", ""),
            "start": str(entry.get("start", 0)),
        })

    if stype in {"google_proxy", "search_proxy"}:
        query = entry.get("query")
        if not query:
            raise ValueError(f"source {entry.get('name')!r} ({stype}) is missing 'query'")
        return _GOOGLE_SEARCH + urlencode({
            "q": query,
            "num": str(entry.get("num", 20)),
        })

    raise ValueError(
        f"source {entry.get('name')!r} of type {stype!r} needs an explicit 'url'"
    )


def _load_source_config(path: str = SOURCES_CONFIG_PATH) -> tuple[list[dict], dict[str, list[str]]]:
    """
    Load and validate the external source registry.

    Returns (sources, fallbacks) where each source is a fully-resolved dict with
    ``name``/``type``/``url``/``region`` (the shape the rest of this module
    expects). Raises ValueError with a clear message on any malformed entry so
    the workflow fails fast rather than silently scraping fewer sources.
    """
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    keyword_sets = cfg.get("keyword_sets", {})
    raw_sources = cfg.get("sources", [])
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError(f"{path}: 'sources' must be a non-empty list")

    seen: set[str] = set()
    sources: list[dict] = []
    for entry in raw_sources:
        name = entry.get("name")
        stype = entry.get("type")
        if not name:
            raise ValueError(f"{path}: a source entry is missing 'name'")
        if name in seen:
            raise ValueError(f"{path}: duplicate source name {name!r}")
        seen.add(name)
        if stype not in _KNOWN_SOURCE_TYPES:
            raise ValueError(f"{path}: source {name!r} has unknown type {stype!r}")
        sources.append({
            "name": name,
            "type": stype,
            "region": entry.get("region"),
            "url": _build_source_url(entry, keyword_sets),
        })

    fallbacks = cfg.get("fallbacks", {})
    if not isinstance(fallbacks, dict):
        raise ValueError(f"{path}: 'fallbacks' must be an object")

    return sources, fallbacks


SOURCES, SOURCE_URL_FALLBACKS = _load_source_config()

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

# SearXNG anti-throttle spacing: Google throttles a SearXNG instance after
# ~8-9 rapid queries. Space consecutive SearXNG calls apart so all
# google_proxy sources reliably yield results within one run.
_SEARXNG_MIN_SPACING = 5.0  # seconds between consecutive SearXNG queries
_last_searxng_call = 0.0    # monotonic timestamp of the previous SearXNG call

# LinkedIn language enrichment: guest job cards expose only the (often English)
# title, but the posting body may be German. The public guest endpoint
# /jobs-guest/jobs/api/jobPosting/<id> returns the description without auth, so
# we fetch it to detect the true language for cards whose title looks English.
# Bounded per run (cost / 429 protection) and cached per job id.
_LINKEDIN_LANG_CACHE: dict[str, str] = {}
_linkedin_lang_enrich_used = 0

# Generic job-page language enrichment for German-market boards (Stepstone,
# karriere.at). Their listing cards only expose a title; many roles carry an
# English-looking title but a German posting body. When a card's title looks
# English we fetch the destination page and detect the language from its
# description. Bounded per run and cached per URL.
_PAGE_LANG_CACHE: dict[str, str] = {}
_page_lang_enrich_used = 0

# High-signal function/marker words for the German-vs-English language detector
# (_infer_language_hint). Kept small and high-precision to avoid false hits.
_DE_FUNCTION_WORDS = {
    "und", "der", "die", "das", "für", "mit", "bei", "eine", "einen", "einer",
    "wir", "sie", "ist", "sind", "im", "zur", "zum", "auf", "von", "des", "den",
    "als", "aus", "sowie", "oder", "über", "unser", "unsere", "unternehmen",
    "mitarbeiter", "aufgaben", "kenntnisse", "erfahrung", "bereich", "leitung",
    "standort", "stelle", "stellenangebot", "gehalt", "personal", "bewerbung",
    "arbeitszeit", "abteilung", "vollzeit", "teilzeit", "festanstellung",
}
_EN_FUNCTION_WORDS = {
    "and", "the", "for", "with", "you", "your", "our", "are", "of", "to",
    "in", "as", "we", "will", "have", "who", "this", "that", "role", "team",
    "experience", "skills", "requirements", "responsibilities", "join", "about",
    "work", "company", "position", "opportunity", "candidate", "leadership",
}


# =========================================================================
# Token Bucket Rate Limiter (pre-emptive, prevents 429 entirely)
# =========================================================================

class TokenBucket:
    """
    Token bucket rate limiter for a domain family.
    
    Prevents exceeding rate limits by enforcing a "fill rate" (tokens/second).
    - Each request consumes 1 token.
    - Tokens refill over time at the specified rate.
    - Requests are blocked until a token is available.
    - No 429s, no retries — just honest rate limiting.
    
    Example: TokenBucket(refill_rate=0.1) allows 1 request per 10 seconds.
    """
    
    def __init__(self, refill_rate: float, capacity: int = 1):
        """
        Initialize token bucket.
        
        Args:
            refill_rate: tokens per second (e.g., 0.1 = 1 request per 10s)
            capacity: max tokens in bucket (default 1 for strict cadence)
        """
        self.refill_rate = refill_rate  # tokens/second
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill_time = time.monotonic()
    
    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self.last_refill_time
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill_time = now
    
    def consume(self, tokens: float = 1.0) -> float:
        """
        Consume tokens, waiting if necessary.
        
        Returns: seconds waited (0 if token was available immediately)
        """
        wait_time = 0.0
        while self.tokens < tokens:
            self._refill()
            # Calculate time needed to accumulate required tokens
            deficit = tokens - self.tokens
            wait_seconds = deficit / self.refill_rate
            if wait_seconds > 0.01:
                time.sleep(wait_seconds)
                wait_time += wait_seconds
            self._refill()
        self.tokens -= tokens
        return wait_time


# Per-domain-family rate limiters (configured by source type)
_rate_limiters: dict[str, TokenBucket] = {}

_RATE_LIMIT_CONFIG = {
    # Google: VERY aggressive per-IP rate limit — apply extreme caution
    # Experience shows Google blocks quickly even at 0.15 req/s.
    # Setting to 0.02 req/s (~1 request per 50 seconds) for absolute safety.
    "google": 0.02,  # ~1 request per 50 seconds
    # Bing: moderate limit
    "bing.com": 0.15,  # ~1 request per 6-7 seconds
    # DuckDuckGo: friendly endpoint
    "duckduckgo.com": 0.20,  # ~1 request per 5 seconds
    # LinkedIn: their guest API is stable
    "linkedin.com": 0.50,  # ~1 request per 2 seconds
    # Direct job boards
    "stepstone.at": 1.0,  # ~1 request per second
    "stepstone.de": 1.0,
    "karriere.at": 1.0,
    "jobs.ch": 1.0,
    "xing.com": 1.0,
    "arbeitnow.com": 1.0,
    # Default for unknown hosts
    "_default": 0.5,  # ~1 request per 2 seconds
}


def _get_rate_limiter(domain_family: str) -> TokenBucket:
    """Get or create rate limiter for a domain family."""
    if domain_family not in _rate_limiters:
        rate = _RATE_LIMIT_CONFIG.get(domain_family, _RATE_LIMIT_CONFIG["_default"])
        _rate_limiters[domain_family] = TokenBucket(refill_rate=rate)
    return _rate_limiters[domain_family]


def _domain_family(url: str) -> str:
    """Return a canonical domain-family key for rate limiting (e.g. all google.* → 'google')."""
    netloc = urlparse(url).netloc.lower()
    if re.search(r"\bgoogle\.", netloc):
        return "google"
    parts = netloc.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc


def _is_google_url(url: str) -> bool:
    """Return True when URL host belongs to Google search domains."""
    return bool(re.search(r"\bgoogle\.", urlparse(url).netloc.lower()))


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


def _parse_retry_after(response: requests.Response) -> int | None:
    """
    Extract Retry-After header from response (RFC 7231).
    Format: Retry-After: <integer_seconds>  (most common) or <HTTP-date>.
    This function handles integer seconds format.
    """
    retry_after = response.headers.get("Retry-After", "").strip()
    if not retry_after:
        return None
    try:
        return max(1, int(retry_after))
    except ValueError:
        # If not an integer, likely HTTP-date format; not parsed here.
        return None


def _exponential_backoff_delay(attempt: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    """
    Calculate exponential backoff delay with jitter to prevent thundering herd.
    
    Formula: delay = min(base * 2^attempt + jitter, max_delay)
    Jitter: random [0, delay] for stochastic spreading.
    
    Examples:
      attempt=0: base=1s → 1*2^0 = 1s + jitter up to 1s = up to 2s total
      attempt=1: 1*2^1 = 2s + jitter up to 2s = up to 4s total
      attempt=2: 1*2^2 = 4s + jitter up to 4s = up to 8s total
    """
    delay = min(base * (2 ** attempt), max_delay)
    jitter = random.uniform(0, delay)
    return delay + jitter


def fetch(url: str, retries: int = 3) -> str | None:
    """
    HTTP GET with pre-emptive rate limiting (token bucket).
    
    Rate limiting prevents 429 errors entirely by enforcing domain-family
    limits BEFORE making requests. No skipping, no blocking — just honest
    request pacing.
    
    This uses token bucket algorithm: each domain family has a refill rate
    (tokens/second). Each request consumes 1 token. If no tokens available,
    we wait until one is assigned. Prevents exceeding server rate limits.
    """
    fam = _domain_family(url)
    limiter = _get_rate_limiter(fam)
    
    # APPLY RATE LIMIT: wait if necessary, then consume 1 token
    # This is the key pre-emptive protection against 429s
    wait_time = limiter.consume(1.0)
    if wait_time > 0.1:
        print(f"  Rate limiter on {fam}: waited {wait_time:.1f}s")

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
            # With pre-emptive rate limiting, 429 should be extremely rare.
            # If we do get one, it's likely a different rate limit (per-IP, per-API-key, etc.)
            # Log it for debugging but don't retry (rate limiter did its job).
            if r.status_code == 429:
                print(f"  ⚠️  Unexpected 429 despite rate limiting. Possible per-IP limit on {fam}.")
                return None
            elif r.status_code in (403, 503):
                # Temporary issues — retry with backoff
                if attempt < retries - 1:
                    backoff_delay = _exponential_backoff_delay(attempt, base=1.0, max_delay=20.0)
                    print(f"  Retrying in {backoff_delay:.1f}s...")
                    time.sleep(backoff_delay)
                    headers["User-Agent"] = random.choice(_USER_AGENTS)
                    session.headers.update(headers)
                    continue
                return None
            else:
                # Other error codes: don't retry
                return None
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
            "language_hint": _serp_lang_hint(title, company, snippet, link),
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
            "language_hint": _serp_lang_hint(title, company, snippet, link),
        })

    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        k = (r.get("application_url") or r.get("source_url") or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def _parse_google_cse_results(payload: dict, source_name: str) -> list[dict]:
    """Normalize Google Custom Search API items into job dict rows."""
    rows: list[dict] = []
    candidates = payload.get("items")
    if not isinstance(candidates, list):
        return rows

    for item in candidates:
        if not isinstance(item, dict):
            continue

        title = (item.get("title") or "").strip()
        link = (item.get("link") or "").strip()
        snippet = (item.get("snippet") or "").strip()

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
            "language_hint": _serp_lang_hint(title, company, snippet, link),
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
            - SCRAPER_GOOGLE_CSE_KEY
            - SCRAPER_GOOGLE_CSE_CX
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
            global _last_searxng_call
            # Space consecutive SearXNG queries to stay under Google's throttle
            # ceiling (~8-9 rapid queries per run otherwise returns 0 results).
            elapsed = time.monotonic() - _last_searxng_call
            if elapsed < _SEARXNG_MIN_SPACING:
                time.sleep(_SEARXNG_MIN_SPACING - elapsed)
            _last_searxng_call = time.monotonic()
            headers: dict[str, str] = {"Accept": "application/json"}
            if searxng_key:
                headers["Authorization"] = f"Bearer {searxng_key}"
            params = {
                "q": query,
                "format": "json",
                "language": "all",
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

    # Google Custom Search JSON API (official API, avoids direct SERP scraping).
    # Requires both API key and CSE engine ID (cx).
    google_cse_key = (os.getenv("SCRAPER_GOOGLE_CSE_KEY") or "").strip()
    google_cse_cx = (os.getenv("SCRAPER_GOOGLE_CSE_CX") or "").strip()
    if google_cse_key and google_cse_cx:
        try:
            params = {
                "key": google_cse_key,
                "cx": google_cse_cx,
                "q": query,
                "num": str(max(1, min(num, 10))),
            }
            verify_opt = _ssl_verify_option()
            resp = requests.get("https://customsearch.googleapis.com/customsearch/v1", params=params, timeout=30, verify=verify_opt)
            if resp.status_code == 200:
                jobs = _parse_google_cse_results(resp.json(), source_name)
                if jobs:
                    print(f"  Google CSE: {len(jobs)} jobs")
                    return jobs
            else:
                print(f"  Google CSE HTTP {resp.status_code} for {source_name}")
        except requests.RequestException as exc:
            print(f"  Google CSE error for {source_name}: {exc}")
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

    desc = (job.get("description") or "")
    title = (job.get("title") or "").strip()
    # Language from a reliable tag (JSON-LD inLanguage) or the body — never the
    # title's prose (a structural marker like (m/w/d) in it still counts).
    lang_hint = detect_job_language(
        tag=job.get("inLanguage") or "",
        body=re.sub(r"<[^>]+>", " ", str(desc)),
        markers_text=title,
        allow_fetch=False,
    )

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
    is_proxy_source = (
        source_name.startswith(("google_", "bing_", "ddg_"))
        or source_name.endswith("_rss")
        or "proxy" in source_name
    )
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

        # Proxy RSS feeds frequently return search-engine wrapper URLs.
        # Normalize them to destination job links before downstream filtering.
        if link and is_proxy_source:
            unwrapped = _extract_search_result_url(link)
            if unwrapped:
                link = unwrapped

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
            "language_hint": detect_job_language(
                body=re.sub(r"<[^>]+>", " ", desc) if desc else "",
                markers_text=title,
                allow_fetch=False,
            ),
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

        # Detect language from the posting body, not the title: DACH feeds
        # (e.g. arbeitnow) routinely carry an English-looking title over a fully
        # German description, so a title-prose guess mislabels them as 'en'. The
        # description is HTML — strip tags before scoring. A structural German
        # marker in the title is still honored via markers_text.
        desc = item.get("description") or ""
        desc_text = re.sub(r"<[^>]+>", " ", desc) if desc else ""

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "source_name": source_name,
            "source_url": link,
            "application_url": link,
            "publish_date": publish_date,
            "salary_text": "",
            "language_hint": detect_job_language(
                body=desc_text, markers_text=title, allow_fetch=False
            ),
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
            "language_hint": _serp_lang_hint(title, company, snippet, url),
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
    # LinkedIn and Xing block direct scraping (JS wall / 403), so skip them
    # entirely to avoid wasting 20-60 s per URL on failed enrichment calls.
    _SKIP_ENRICH_DOMAINS = ("linkedin.com", "xing.com")
    _enrich_max = int(os.getenv("SCRAPER_ENRICH_MAX", "2"))
    enrich_count = 0
    for row in results:
        if enrich_count >= _enrich_max:
            break
        url = row.get("application_url") or row.get("source_url") or ""
        if any(d in url for d in _SKIP_ENRICH_DOMAINS):
            continue
        enriched = enrich_from_company_page(row)
        row.update(enriched)
        enrich_count += 1
    return results


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _normalize_for_langdetect(text: str, max_chars: int = 1500) -> str:
    """
    Reduce a job ad to a clean, bounded text sample for statistical language
    detection.

    Strips markup/URLs/emails/digits/punctuation/emoji NOISE but deliberately
    KEEPS every Unicode letter — including German umlauts (ä ö ü ß) and other
    diacritics, which are the strongest signal an n-gram detector has for
    German. Only a leading fraction (``max_chars``) is returned to bound cost.
    """
    t = text or ""
    t = re.sub(r"<[^>]+>", " ", t)                       # any stray HTML tags
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)         # URLs
    t = re.sub(r"\S+@\S+", " ", t)                       # emails
    # Keep Unicode letters and whitespace only; drop digits, underscore,
    # punctuation and emoji. With re.UNICODE, \w preserves ä ö ü ß / accents.
    t = re.sub(r"[\d_\W]+", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars]


def _langdetect_top(text: str, min_chars: int = 40):
    """
    Best ``(lang, prob)`` guess from the optional ``langdetect`` library on a
    cleaned, letter-only sample — for ANY language, not just de/en.

    Returns ``None`` when the library is unavailable, the sample is too short to
    be reliable, or detection fails. Never raises.
    """
    if _ld_detect_langs is None:
        return None
    sample = _normalize_for_langdetect(text)
    if len(sample) < min_chars:
        return None
    try:
        ranked = _ld_detect_langs(sample)
    except Exception:  # pragma: no cover - library edge cases
        return None
    if not ranked:
        return None
    best = max(ranked, key=lambda c: c.prob)
    return best.lang, best.prob


def _detect_lang_via_library(text: str, min_prob: float = 0.60):
    """
    Best-effort DE/EN detection via the optional ``langdetect`` library.

    Returns ``"de"``/``"en"`` only when the confident top guess is German or
    English; returns ``None`` in every other case (library absent, sample too
    short, low confidence, or a third language wins) so the caller can fall back
    to the dependency-free heuristic.
    """
    top = _langdetect_top(text)
    if top is None:
        return None
    lang, prob = top
    if lang in ("de", "en") and prob >= min_prob:
        return lang
    return None


# Strong DACH audience markers that pin a posting to German regardless of any
# statistical guess: explicit German-skill requirements, gender notation
# (m/w/d), and the neutral suffixes :in / :innen / *in.
_DE_EXPLICIT_RE = re.compile(
    r"deutschkenntnisse|flie\wend\s+deutsch|deutsch\s+(?:erforderlich|zwingend)|german\s+required",
    re.I,
)
_DE_AUDIENCE_RE = re.compile(
    r"\(\s*[mwfdx](?:\s*[/,]\s*[mwfdx]){1,2}\s*\)|[a-zäöü]+(?::innen|:in|\*in|\*innen)\b",
    re.I,
)


def detect_language(text: str) -> str:
    """
    Report the dominant language of a text as an ISO 639-1 code
    (e.g. ``"en"``, ``"de"``, ``"fr"``, ``"it"``).

    This is the "which language is this?" helper. Unlike ``_infer_language_hint``
    it does NOT collapse everything to de/en, so a third language surfaces as its
    own code (the "rest" bucket used by the ranker). Detection order:

      1. Empty/blank input           → ``"und"`` (undetermined).
      2. Explicit / audience markers → ``"de"`` (high-precision, always wins).
      3. Optional ``langdetect``     → confident code for any language.
      4. Fallback                    → dependency-free de/en heuristic.
    """
    if not (text or "").strip():
        return "und"
    if _DE_EXPLICIT_RE.search(text) or _DE_AUDIENCE_RE.search(text):
        return "de"
    top = _langdetect_top(text)
    if top is not None and top[1] >= 0.60:
        return top[0]
    return _infer_language_hint(text)


# Enough real letters (including spaces) in a normalized body sample to trust
# language detection on it. Below this we treat the body as effectively absent
# and require a reliable tag (or an audience marker) instead of guessing.
_MIN_BODY_CHARS_FOR_LANG = 40

# Reliable language tag / locale / name → ISO-639-1. UI locales such as "de-AT"
# and human names like "German" / "Deutsch" collapse to their base code. This
# only ever consumes structured, trustworthy tags — never a job title.
_LANG_TAG_ALIASES = {
    "de": "de", "deu": "de", "ger": "de", "german": "de", "deutsch": "de",
    "en": "en", "eng": "en", "english": "en", "englisch": "en",
    "fr": "fr", "fra": "fr", "french": "fr", "franz": "fr", "französisch": "fr",
    "it": "it", "ita": "it", "italian": "it", "italienisch": "it",
}


def _normalize_lang_tag(raw) -> str | None:
    """
    Normalize a language tag / locale / name to an ISO-639-1 code.

    Accepts JSON-LD ``inLanguage`` ("de-DE"), ``<html lang>`` ("en"), meta
    locales ("de_AT") or human names ("German"). Returns a 2-letter code, or
    None when the value is missing/unrecognized.
    """
    if not raw:
        return None
    s = str(raw).strip().lower().replace("_", "-")
    if not s:
        return None
    base = s.split("-")[0]
    if base in _LANG_TAG_ALIASES:
        return _LANG_TAG_ALIASES[base]
    if s in _LANG_TAG_ALIASES:
        return _LANG_TAG_ALIASES[s]
    # A bare, plausible ISO-639-1 code we have no alias for (e.g. "es", "nl").
    if re.fullmatch(r"[a-z]{2}", base):
        return base
    return None


def _language_tag_from_html(html: str) -> str | None:
    """
    Extract a reliable language tag from HTML: JSON-LD ``inLanguage`` first
    (per-posting), then ``<html lang>``, then content-language / og:locale
    meta tags. Returns an ISO-639-1 code or None.
    """
    if not html:
        return None
    for j in extract_jsonld_jobs(html):
        if isinstance(j, dict):
            code = _normalize_lang_tag(j.get("inLanguage"))
            if code:
                return code
    soup = BeautifulSoup(html, "html.parser")
    html_el = soup.find("html")
    if html_el is not None:
        code = _normalize_lang_tag(html_el.get("lang") or html_el.get("xml:lang"))
        if code:
            return code
    for attrs in (
        {"http-equiv": re.compile(r"content-language", re.I)},
        {"property": re.compile(r"og:locale", re.I)},
    ):
        meta = soup.find("meta", attrs=attrs)
        if meta is not None:
            code = _normalize_lang_tag(meta.get("content"))
            if code:
                return code
    return None


def _audience_marker_lang(text: str) -> str | None:
    """
    Return ``"de"`` when a text carries a high-precision German *audience*
    marker — gender notation ``(m/w/d)``, neutral suffixes ``:in`` / ``:innen``
    / ``*in``, or an explicit German-skill requirement. These are structural
    locale signals, not a guess at the prose language, so they are trustworthy
    even when found in an otherwise-English title. Returns None otherwise.
    """
    if text and (_DE_EXPLICIT_RE.search(text) or _DE_AUDIENCE_RE.search(text)):
        return "de"
    return None


def _body_language(body: str) -> str | None:
    """
    Detect language from an ad BODY only. Returns an ISO code when the body
    carries a German audience marker or is substantial enough to be reliable,
    else None so the caller falls back to a tag rather than a title guess.
    """
    if not body:
        return None
    marker = _audience_marker_lang(body)
    if marker:
        return marker
    if len(_normalize_for_langdetect(body)) < _MIN_BODY_CHARS_FOR_LANG:
        return None
    lang = detect_language(body)
    return lang if lang and lang != "und" else None


def detect_job_language(
    *,
    body: str = "",
    tag: str = "",
    html: str = "",
    markers_text: str = "",
    url: str = "",
    market_default: str | None = None,
    allow_fetch: bool = True,
) -> str:
    """
    Resolve a posting's language WITHOUT trusting the prose of its job title.

    Priority (first reliable signal wins):
      1. Explicit language ``tag`` passed in (e.g. JSON-LD ``inLanguage``).
      2. A language tag embedded in provided ``html`` (``<html lang>`` / meta).
      3. Body-text detection, when the ``body`` is substantial or carries a
         German audience marker.
      4. A German audience marker in ``markers_text`` (e.g. the title) — a
         structural locale signal, not a prose-language guess.
      5. Fetch the destination ``url`` for its tag/body (bounded & cached).
      6. ``market_default`` — a domain-locale prior for German-audience boards.
      7. ``""`` (unknown) — left for the AI pre-rank step to fill; never a
         title-prose guess.
    """
    code = _normalize_lang_tag(tag)
    if code:
        return code
    if html:
        code = _language_tag_from_html(html)
        if code:
            return code
    code = _body_language(body)
    if code:
        return code
    code = _audience_marker_lang(markers_text)
    if code:
        return code
    if allow_fetch and url:
        code = _fetch_job_page_language(url)
        if code:
            return code
    return market_default or ""



def _infer_language_hint(text: str) -> str:
    """
    German-vs-English detector.

    Layered for precision + robustness:
      1. Explicit "Deutschkenntnisse / German required" signals win outright.
      2. High-precision DACH *audience* markers — gender notation (m/w/d) and
         the neutral suffixes :in / :innen / *in — force "de" even when the job
         TITLE is English (common for exec/tech roles).
      3. If the optional `langdetect` library is available AND the ad body is
         long enough to be reliable, trust its confident DE/EN verdict.
      4. Otherwise fall back to the dependency-free function-word / umlaut /
         job-stem scoring heuristic below.
    """
    t = (text or "").lower()
    if not t.strip():
        return "en"

    # (1) Strong explicit signals and (2) German/Austrian audience markers —
    # gender notation (m/w/d) and neutral suffixes :in / :innen / *in — are
    # high-precision: they pin a posting to German even when the TITLE is
    # English, and must win before any statistical guess.
    if _DE_EXPLICIT_RE.search(text or "") or _DE_AUDIENCE_RE.search(text or ""):
        return "de"

    # (3) Optional statistical detector for longer bodies (fixes long German
    # ads that lack the markers above, e.g. issue #44). No-op when langdetect
    # is not installed or the sample is too short/low-confidence.
    lib_lang = _detect_lang_via_library(text)
    if lib_lang is not None:
        return lib_lang

    # (4) Dependency-free scoring heuristic (fallback).
    tokens = re.findall(r"[a-zäöüß]+", t)
    if not tokens:
        return "en"

    umlaut_hits = len(re.findall(r"[äöüß]", t))

    de_score = sum(1 for w in tokens if w in _DE_FUNCTION_WORDS)
    en_score = sum(1 for w in tokens if w in _EN_FUNCTION_WORDS)

    # German umlauts almost never appear in English job text — weight them.
    de_score += 1.5 * umlaut_hits

    # German job-title / description stems (leiter, geschäftsführer, entwicklung…).
    if re.search(
        r"\b(?:leiter|leiterin|leitung|gesch\wftsf\whr|standort|mitarbeiter|"
        r"entwickl|vertrieb|einkauf|bereichsleit|abteilungsleit|"
        r"aufgaben|kenntnisse|bewerb|verantwort)\w*",
        t,
    ):
        de_score += 2

    if de_score > en_score:
        return "de"
    if en_score > de_score:
        return "en"
    # Tie-break: any umlaut → German; otherwise English (pipeline default).
    return "de" if umlaut_hits else "en"


# Job-board hosts whose postings are written for a German-language audience by
# default. Used as the language fallback for SERP rows when reading the actual
# posting body is unavailable (enrichment disabled or the per-run fetch cap is
# hit) — better to lean 'de' on these than trust a short, English-looking SERP
# snippet.
_GERMAN_MARKET_HOST_RE = re.compile(
    r"(?:stepstone\.(?:de|at)|karriere\.at|xing\.com|indeed\.(?:de|at))", re.I
)


def _serp_lang_hint(title: str, company: str, snippet: str, url: str) -> str:
    """
    Language for a SERP-proxied row (LinkedIn/Stepstone/jobs.ch/…) WITHOUT
    trusting the (often English) title prose. Order: the SERP snippet as a body
    proxy (when substantial) or a German audience marker in the title → the
    real posting body/tag — the LinkedIn guest endpoint for
    linkedin.com/jobs/view URLs, otherwise the destination page → a
    German-market domain default → unknown (''). ``company`` is not a language
    signal and is ignored.
    """
    body_lang = _body_language(snippet) or _audience_marker_lang(title)
    if body_lang:
        return body_lang

    u = (url or "").lower()
    market_default = "de" if _GERMAN_MARKET_HOST_RE.search(u) else ""
    if os.getenv("SCRAPER_PAGE_LANG_ENRICH", "true").lower() in {"1", "true", "yes"}:
        m = re.search(r"linkedin\.com/jobs/view/(\d+)", u)
        enriched = (
            _fetch_linkedin_job_language(m.group(1))
            if m
            else _fetch_job_page_language(url)
        )
        if enriched:
            return enriched

    return market_default


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


def _fetch_job_page_language(url: str) -> str | None:
    """
    Fetch a destination job page and determine its language from a reliable
    signal: first a language tag (JSON-LD ``inLanguage`` / ``<html lang>`` /
    meta), then the posting body (JSON-LD description or a visible description
    container). Returns an ISO code, or None if unavailable. Cached per URL and
    capped per run (SCRAPER_PAGE_LANG_ENRICH_MAX, default 80) to bound runtime
    and 429 risk.
    """
    if not url or not url.startswith("http"):
        return None
    if url in _PAGE_LANG_CACHE:
        return _PAGE_LANG_CACHE[url] or None

    global _page_lang_enrich_used
    cap = int(os.getenv("SCRAPER_PAGE_LANG_ENRICH_MAX", "80"))
    if _page_lang_enrich_used >= cap:
        return None
    _page_lang_enrich_used += 1

    html = fetch(url, retries=1)
    if not html:
        _PAGE_LANG_CACHE[url] = ""
        return None

    # Prefer an explicit, reliable language tag over body detection.
    lang = _language_tag_from_html(html)
    if not lang:
        text = ""
        for j in extract_jsonld_jobs(html):
            if isinstance(j, dict) and j.get("description"):
                text = str(j["description"])
                break
        if not text:
            soup = BeautifulSoup(html, "html.parser")
            el = (
                soup.find(attrs={"data-at": re.compile(r"job-ad-content|listing-content", re.I)})
                or soup.find(class_=re.compile(r"job-ad|description|listing__content|job-detail", re.I))
            )
            text = el.get_text(" ", strip=True) if el else ""
        lang = _body_language(re.sub(r"<[^>]+>", " ", text)) if text else None

    _PAGE_LANG_CACHE[url] = lang or ""
    return lang


def _market_lang_hint(url: str, source_name: str = "", title: str = "") -> str:
    """
    Language for a job-board *card* (Stepstone, karriere.at, jobs.ch) where the
    listing carries no body text. The title's prose is deliberately ignored as
    unreliable; only a structural German audience marker in it counts. Order:
    audience marker in the title → reliable tag/body fetched from the
    destination page (bounded/cached) → the board's audience locale ('de' for
    German-market boards) → unknown ('').
    """
    market_default = "de" if _GERMAN_MARKET_HOST_RE.search((url or "").lower()) else ""
    allow_fetch = os.getenv("SCRAPER_PAGE_LANG_ENRICH", "true").lower() in {"1", "true", "yes"}
    return detect_job_language(
        markers_text=title,
        url=url,
        market_default=market_default,
        allow_fetch=allow_fetch,
    )


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
            "language_hint": _market_lang_hint(href, source_name, _text(title_el)),
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
                "language_hint": _market_lang_hint(link_el["href"] if link_el else "", source_name, _text(title_el)),
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
            "language_hint": _market_lang_hint(href, source_name, _text(title_el)),
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
            "language_hint": _market_lang_hint(href, source_name, _text(title_el)),
        })
    return jobs


def _fetch_linkedin_job_language(job_id: str) -> str | None:
    """
    Fetch the public LinkedIn guest job-posting description and detect its
    language. Returns 'de'/'en', or None if unavailable.

    The guest endpoint /jobs-guest/jobs/api/jobPosting/<id> serves the full
    description HTML without authentication. Results are cached per job id and
    the number of network fetches is capped per run (env
    SCRAPER_LINKEDIN_LANG_ENRICH_MAX, default 60) to bound runtime and 429 risk.
    """
    if not job_id:
        return None
    if job_id in _LINKEDIN_LANG_CACHE:
        return _LINKEDIN_LANG_CACHE[job_id] or None

    global _linkedin_lang_enrich_used
    cap = int(os.getenv("SCRAPER_LINKEDIN_LANG_ENRICH_MAX", "60"))
    if _linkedin_lang_enrich_used >= cap:
        return None
    _linkedin_lang_enrich_used += 1

    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    html = fetch(url, retries=1)
    if not html:
        _LINKEDIN_LANG_CACHE[job_id] = ""  # remember the failed attempt
        return None

    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.find(
        class_=lambda c: c and ("show-more-less-html__markup" in c or "description__text" in c)
    )
    text = desc_el.get_text(" ", strip=True) if desc_el else ""
    if not text:
        # Fall back to any JSON-LD JobPosting description embedded on the page.
        for j in extract_jsonld_jobs(html):
            if isinstance(j, dict) and j.get("description"):
                text = str(j["description"])
                break

    # Prefer a reliable language tag; otherwise detect from the description body.
    lang = _language_tag_from_html(html) or (_body_language(text) if text else None)
    _LINKEDIN_LANG_CACHE[job_id] = lang or ""
    return lang


def parse_linkedin_guest_api(html: str, source_name: str) -> list[dict]:
    """
    Parse LinkedIn's public guest API HTML fragment response into job dicts.
    Endpoint: /jobs-guest/jobs/api/seeMoreJobPostings/search
    Returns job card HTML without auth. Tracking query params are stripped from URLs.
    """
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []

    for li in soup.find_all("li"):
        # Locate card by the entity URN attribute — more stable than class names.
        div = li.find("div", attrs={"data-entity-urn": re.compile(r":jobPosting:\d+")})
        if not div:
            continue

        entity_urn = div.get("data-entity-urn", "")
        m = re.search(r":jobPosting:(\d+)", entity_urn)
        if not m:
            continue
        job_id = m.group(1)

        # URL: strip tracking query params from the anchor href.
        link_el = li.find("a", href=True)
        raw_href = link_el["href"] if link_el else ""
        clean_url = raw_href.split("?")[0] if raw_href else f"https://www.linkedin.com/jobs/view/{job_id}/"

        # Title: h3.base-search-card__title, fall back to sr-only span in link.
        h3 = li.find("h3", class_=lambda c: c and "base-search-card__title" in c)
        title = h3.get_text(strip=True) if h3 else ""
        if not title and link_el:
            sr = link_el.find("span", class_=lambda c: c and "sr-only" in c)
            title = sr.get_text(strip=True) if sr else ""
        if not title:
            continue

        # Company from h4.base-search-card__subtitle.
        h4 = li.find("h4", class_=lambda c: c and "base-search-card__subtitle" in c)
        company = h4.get_text(strip=True) if h4 else ""

        # Location from .job-search-card__location.
        loc_el = li.find(class_=lambda c: c and "job-search-card__location" in c)
        location = loc_el.get_text(strip=True) if loc_el else ""

        # Language: never from the title prose. Read the actual posting body via
        # the LinkedIn guest endpoint (bounded/cached); a structural German
        # marker in the title still counts. Left unknown ('') when enrichment is
        # off or unavailable — the AI pre-rank step fills it.
        lang_hint = _audience_marker_lang(title) or ""
        if not lang_hint and os.getenv(
            "SCRAPER_LINKEDIN_LANG_ENRICH", "true"
        ).lower() in {"1", "true", "yes"}:
            lang_hint = _fetch_linkedin_job_language(job_id) or ""

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "source_name": source_name,
            "source_url": clean_url,
            "application_url": clean_url,
            "publish_date": "",
            "salary_text": "",
            "language_hint": lang_hint,
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


def _source_family(job: dict) -> str:
    """Canonical job-board family for a job, derived from its destination host."""
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


def _parse_source_content(content: str, name: str, src_type: str) -> list[dict]:
    """
    Parse fetched content into job dicts according to the source type.

    - rss           → parse_rss
    - json_api      → parse_json_jobs
    - linkedin_api  → parse_linkedin_guest_api
    - google/search proxy → RSS when the body looks like XML, else SERP HTML
    - html (default)→ JSON-LD first, then the site-specific parser in PARSER_MAP
    """
    if src_type == "rss":
        return parse_rss(content, name)
    if src_type == "json_api":
        return parse_json_jobs(content, name)
    if src_type == "linkedin_api":
        return parse_linkedin_guest_api(content, name)
    if src_type in {"google_proxy", "search_proxy"}:
        if _looks_like_xml(content):
            return parse_rss(content, name)
        return parse_google_jobs(content, name)
    # html: JSON-LD first, fall back to the site-specific HTML parser.
    jobs = [normalize_jsonld(j, name) for j in extract_jsonld_jobs(content)]
    if jobs:
        return jobs
    parser = PARSER_MAP.get(name)
    return parser(content, name) if parser else []


def _dedup_by_source_family(jobs: list[dict]) -> list[dict]:
    """
    Collapse local duplicates within the same job-board family while letting
    different boards keep distinct copies of the same role.
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for j in jobs:
        fp = (
            f"{(j.get('title') or '').lower().strip()}|"
            f"{(j.get('company') or '').lower().strip()}|"
            f"{_source_family(j)}"
        )
        if fp not in seen:
            seen.add(fp)
            unique.append(j)
    return unique


def _count_by(jobs: list[dict], key_fn) -> dict[str, int]:
    """Tally jobs into a {key: count} dict using ``key_fn`` to derive the key."""
    counts: dict[str, int] = {}
    for j in jobs:
        k = key_fn(j)
        counts[k] = counts.get(k, 0) + 1
    return counts

def main() -> None:
    os.makedirs("/tmp/jobs", exist_ok=True)
    all_jobs: list[dict] = []
    stats: dict[str, int] = {}
    failed_fetch_sources: list[str] = []
    fail_on_fetch_error = os.getenv("SCRAPER_FAIL_ON_FETCH_ERROR", "true").lower() in {"1", "true", "yes"}
    min_per_source = int(os.getenv("SCRAPER_MIN_PER_SOURCE", "2"))
    min_real_per_source = int(os.getenv("SCRAPER_MIN_REAL_PER_SOURCE", "3"))
    enable_backfill = os.getenv("SCRAPER_SOURCE_BACKFILL", "true").lower() in {"1", "true", "yes"}
    # Google proxy handling mode:
    # - provider_only (default): use SearXNG/SerpAPI/Zenserp only; never direct Google fetch
    # - hybrid: try provider first, then allow direct Google fetch
    # - direct: direct Google fetch path only
    # - off: skip Google proxy sources entirely
    google_mode = (os.getenv("SCRAPER_GOOGLE_MODE", "provider_only") or "provider_only").strip().lower()
    avoid_google_fallbacks = os.getenv("SCRAPER_AVOID_GOOGLE_FALLBACKS", "true").lower() in {"1", "true", "yes"}
    ar_now_cache: list[dict] | None = None

    for src in SOURCES:
        name = src["name"]
        url = src["url"]
        src_type = src.get("type", "html")
        print(f"\nFetching {name} [{src_type}] ...")

        content = None

        if src_type == "google_proxy":
            if google_mode == "off":
                print("  Skipping Google proxy (SCRAPER_GOOGLE_MODE=off)")
                stats[name] = 0
                continue

            # Provider-backed fetches avoid direct Google anti-bot/IP rate limits.
            if google_mode in {"provider_only", "hybrid"}:
                provider_jobs = _fetch_google_proxy_via_provider(url, name)
                if provider_jobs:
                    jobs = [j for j in provider_jobs if j.get("title")]
                    stats[name] = len(jobs)
                    all_jobs.extend(jobs)
                    continue
                if google_mode == "provider_only":
                    print("  No provider result; skipping direct Google fetch (provider_only mode)")
                    stats[name] = 0
                    continue

        # Optional: skip search_proxy sources (Bing/DDG/Ecosia queries).
        # These sources have issues:
        #   - Timeout failures on Bing/DDG (causing hangs)
        #   - 403 from Ecosia
        #   - Fallback to Google which 429s
        #   - Zero net yield (Google alternatives return 0 jobs anyway)
        # Default is to skip; set SCRAPER_SKIP_SEARCH_PROXIES=0 to re-enable.
        if src_type == "search_proxy" and os.getenv("SCRAPER_SKIP_SEARCH_PROXIES", "true").lower() in {"1", "true", "yes"}:
            print("  Skipping search proxy (unreliable, timeouts/403s/low-yield)")
            stats[name] = 0
            continue

        # NOTE: Bing RSS mode (format=rss) is intentionally NOT used here.
        # Testing showed Bing RSS completely ignores the search query and returns
        # unrelated results (e.g. French hardware store pages). Always use HTML mode.
        if not content:
            content = fetch(url)
        if not content and name in SOURCE_URL_FALLBACKS:
            for alt in SOURCE_URL_FALLBACKS[name]:
                # Prevent indirect Google 429s via search-proxy fallback chains.
                if avoid_google_fallbacks and src_type == "search_proxy" and _is_google_url(alt):
                    print(f"  Skipping Google fallback URL for {name}")
                    continue
                print(f"  Retry via alternate URL for {name}")
                content = fetch(alt, retries=2)
                if content:
                    url = alt
                    break
        if not content:
            print(f"  SKIP {name}: fetch failed")
            stats[name] = 0
            failed_fetch_sources.append(name)
            continue

        jobs = _parse_source_content(content, name, src_type)
        print(f"  Parsed [{src_type}]: {len(jobs)} jobs")

        # Drop records without a title
        jobs = [j for j in jobs if j.get("title")]

        # Run proxy URL-quality gating before retry decisions. This ensures
        # noisy SERP/RSS parses do not prevent alternate URL retries.
        if src_type in {"google_proxy", "search_proxy"}:
            before_quality_gate = len(jobs)
            jobs = [
                j for j in jobs
                if _expected_job_url((j.get("application_url") or j.get("source_url") or ""), name)
            ]
            dropped = before_quality_gate - len(jobs)
            if dropped > 0:
                print(f"  URL quality gate dropped {dropped} rows for {name}")

        # Some proxy pages return successful responses but no extractable cards.
        # In that case, try alternate source URLs before falling back further.
        if len(jobs) < min_real_per_source and name in SOURCE_URL_FALLBACKS:
            seen = {
                (j.get("application_url") or j.get("source_url") or j.get("title") or "").strip().lower()
                for j in jobs
            }
            for alt in SOURCE_URL_FALLBACKS[name]:
                # Avoid direct Google fallback URLs during retry parsing when requested.
                # This closes the last path that could still generate Google 429s.
                if avoid_google_fallbacks and src_type in {"google_proxy", "search_proxy"} and _is_google_url(alt):
                    print(f"  Skipping Google fallback URL for {name} (retry parsing)")
                    continue
                print(f"  Retry parsing via alternate URL for {name}")
                alt_content = fetch(alt, retries=2)
                if not alt_content:
                    continue

                # Retry parsing only re-parses feed/proxy types; html sources
                # are intentionally not re-parsed here (kept as before).
                if src_type in {"google_proxy", "search_proxy", "rss", "json_api", "linkedin_api"}:
                    alt_jobs = _parse_source_content(alt_content, name, src_type)
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

    # Deduplicate within each source family so different job boards can keep
    # distinct copies of the same role while local duplicates are collapsed.
    all_jobs = _dedup_by_source_family(all_jobs)
    print(f"After raw dedup: {len(all_jobs)} unique jobs")

    # Authoritative per-source usefulness: how many de-duplicated jobs each
    # source contributed to the full pool (computed BEFORE the [:200] downstream
    # cap so late-listed sources are not falsely reported as dead weight).
    deduped_source_stats = _count_by(all_jobs, lambda j: j.get("source_name", "unknown"))
    family_stats = _count_by(all_jobs, _source_family)

    linkedin_count = family_stats.get("linkedin", 0)
    karriere_count = family_stats.get("karriere.at", 0)
    google_sources_count = sum(
        v for k, v in stats.items()
        if k.startswith(("google_", "bing_", "ddg_", "ecosia_"))
    )
    print(f"\nFamily breakdown: {family_stats}")
    print(f"\n{'='*50}")
    print(f"  Google/Bing/DDG proxy sources total  : {google_sources_count}")
    print(f"  LinkedIn destination ADs             : {linkedin_count}")
    print(f"  Karriere.at destination ADs          : {karriere_count}")
    if linkedin_count > karriere_count:
        print(f"  ✅ LinkedIn ({linkedin_count}) > Karriere ({karriere_count})")
    else:
        print(f"  ❌ LinkedIn ({linkedin_count}) <= Karriere ({karriere_count}) — check proxy sources")
    print(f"{'='*50}\n")

    output = {
        "date": str(date.today()),
        "stats": stats,
        "family_stats": family_stats,
        "deduped_source_stats": deduped_source_stats,
        "jobs": all_jobs[:200],  # cap to limit downstream token use
    }
    out_path = "/tmp/jobs/jobs_raw.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
