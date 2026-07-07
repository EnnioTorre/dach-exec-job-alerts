"""
Deduplicates, filters by relevance, and deterministically scores raw jobs.

Reads  /tmp/jobs/jobs_raw.json
Writes /tmp/jobs/jobs_ranked.json
"""

import json
import os
import re
import math
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict
from datetime import date

# ---------------------------------------------------------------------------
# Relevance filter
# ---------------------------------------------------------------------------

ROLE_KEYWORDS = [
    "cto",
    "chief technology officer",
    "engineer",
    "software engineer",
    "devops engineer",
    "platform engineer",
    "cloud engineer",
    "site reliability engineer",
    "sre engineer",
    "backend engineer",
    "infrastructure engineer",
    "head of engineering",
    "head of software",
    "head of software engineering",
    "head of platform",
    "head of platform engineering",
    "head of cloud",
    "head of cloud engineering",
    "head of digitalization",
    "head of data",
    "head of devops",
    "head of sre",
    "head of infrastructure",
    "engineering manager",
    "platform engineering manager",
    "cloud engineering manager",
    "director of engineering",
    "director of platform engineering",
    "director of cloud engineering",
    "vp engineering",
    "vp of engineering",
    "vice president engineering",
    "engineering lead",
    "technical director",
    # German technical engineer terms
    "softwareentwickler",
    "entwicklungsingenieur",
    "devops engineer",
    "plattform engineer",
    "cloud engineer",
    "site reliability engineer",
    "sre engineer",
]

DOMAIN_KEYWORDS = [
    "engineering",
    "software",
    "platform",
    "cloud",
    "devops",
    "sre",
    "site reliability",
    "infrastructure",
    "backend",
    "data platform",
    "data",
    "digitalization",
]

EXCLUDE_KEYWORDS = [
    "sales",
    "vertrieb",
    "key account",
    "kundendienst",
    "innendienst",
    "industrial",
    "electrical",
    "eletrical",
    "elektro",
    "elektrotechnik",
    "electromechanical",
    "quality",
    "landtechnik",
    "zeichner",
    "cnc",
]

MANAGEMENT_KEYWORDS = [
    "head",
    "manager",
    "director",
    "vp",
    "vice president",
    "lead",
    "leitung",
    "leiter",
    "chief",
    "cto",
]

JOB_URL_HINTS = [
    "/jobs/",
    "/job/",
    "/careers",
    "/career",
    "/vacancies",
    "/position",
    "/stellen",
]

NON_JOB_TITLE_PATTERNS = [
    r"\bwhat is\b",
    r"\bdefinition\b",
    r"\bgehalt\b",
    r"\bsalary\b",
    r"\bguide\b",
    r"\btips\b",
    r"\bblog\b",
    r"\bnews\b",
    r"\bwiki\b",
]

BAD_URL_PATTERNS = [
    "/search",
    "?q=",
    "/jobs/cto",
    "/jobs/head-of-engineering",
    "/jobs/software-engineering",
    "/jobs/platform-engineering",
    "/jobs/cloud-engineering",
    "/gehalt",
    "/salary",
    "/blog",
    "/news",
]


def _contains_keyword(text: str, keyword: str) -> bool:
    # Match keyword as a token/phrase, not a loose substring.
    pattern = r"\b" + re.escape(keyword).replace(r"\ ", r"\s+") + r"\b"
    return bool(re.search(pattern, text))


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(_contains_keyword(text, kw) for kw in keywords)

TECH_IC_KEYWORDS = [
    "software engineer",
    "software developer",
    "platform engineer",
    "cloud engineer",
    "cloud systems engineer",
    "devops engineer",
    "site reliability engineer",
    "sre engineer",
    "backend engineer",
    "infrastructure engineer",
    "softwareentwickler",
    "entwicklungsingenieur",
    "devops",
    "platform engineering",
    "cloud engineering",
    "site reliability",
    "sre",
    "backend",
    "infrastructure",
]


def is_relevant(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    url = (job.get("application_url") or job.get("source_url") or "").lower()
    ai_rel = (job.get("ai_relevance") or "").lower()
    ai_url_quality = (job.get("ai_url_quality") or "").lower()

    if ai_rel == "reject":
        return False
    if ai_url_quality in {"search", "listing"}:
        return False

    if any(re.search(p, title) for p in NON_JOB_TITLE_PATTERNS):
        return False

    if any(host in url for host in ("bing.com/search", "google.com/search", "duckduckgo.com/html")):
        return False

    if any(p in url for p in BAD_URL_PATTERNS):
        return False

    # If URL lacks typical job path hints, require stronger job-title signal.
    has_job_url_hint = any(h in url for h in JOB_URL_HINTS)
    if any(kw in title for kw in EXCLUDE_KEYWORDS):
        return False

    cto_like = bool(re.search(r"\bcto\b", title)) or "chief technology officer" in title
    has_role = any(kw in title for kw in ROLE_KEYWORDS)
    has_domain = any(kw in title for kw in DOMAIN_KEYWORDS)
    has_tech_ic_role = any(kw in title for kw in TECH_IC_KEYWORDS)

    # Filter obvious category/listing pages that are not concrete job ads.
    if "karriere.at" in url:
        if re.search(r"/jobs/(cto|head-of-engineering|software-engineering|platform-engineering|cloud-engineering)$", url):
            return False
        if re.search(r"/jobs/[a-z\-]+$", url) and not re.search(r"/jobs/\d+", url):
            return False

    if re.fullmatch(r"https?://[^/]+/?", url):
        return False

    if not has_job_url_hint and not (has_role and has_domain):
        return False

    return cto_like or (has_role and has_domain) or has_tech_ic_role


def is_relevant_relaxed(job: dict) -> bool:
    """
    Broader relevance gate used only as fallback when strict filtering
    produces too few results.
    """
    title = (job.get("title") or "").lower()
    url = (job.get("application_url") or job.get("source_url") or "").lower()
    ai_rel = (job.get("ai_relevance") or "").lower()
    ai_url_quality = (job.get("ai_url_quality") or "").lower()

    if ai_rel == "reject":
        return False
    if ai_url_quality in {"search", "listing"}:
        return False

    if any(re.search(p, title) for p in NON_JOB_TITLE_PATTERNS):
        return False
    if any(host in url for host in ("bing.com/search", "google.com/search", "duckduckgo.com/html")):
        return False
    if any(p in url for p in BAD_URL_PATTERNS):
        return False
    if any(kw in title for kw in EXCLUDE_KEYWORDS):
        return False

    # Allow strong leadership or clear tech-IC terms even without full strict role+domain pair.
    has_mgmt = _contains_any(title, MANAGEMENT_KEYWORDS)
    has_domain = _contains_any(title, DOMAIN_KEYWORDS)
    has_tech_ic = _contains_any(title, TECH_IC_KEYWORDS)
    cto_like = bool(re.search(r"\bcto\b", title)) or "chief technology officer" in title

    return cto_like or (has_mgmt and has_domain) or has_tech_ic


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

_VIENNA = ["vienna", "wien", "1010", "1020", "1030", "1040", "1050",
           "1060", "1070", "1080", "1090", "1100", "1110", "1120",
           "1130", "1140", "1150", "1160", "1170", "1180", "1190",
           "1200", "1210", "1220", "1230"]

_DACH_LOCATION_POINTS: dict[str, tuple[float, float]] = {
    "vienna": (48.2082, 16.3738),
    "wien": (48.2082, 16.3738),
    "graz": (47.0707, 15.4395),
    "linz": (48.3069, 14.2858),
    "salzburg": (47.8095, 13.0550),
    "innsbruck": (47.2692, 11.4041),
    "klagenfurt": (46.6247, 14.3053),
    "villach": (46.6103, 13.8558),
    "berlin": (52.5200, 13.4050),
    "munich": (48.1351, 11.5820),
    "münchen": (48.1351, 11.5820),
    "frankfurt": (50.1109, 8.6821),
    "hamburg": (53.5511, 9.9937),
    "cologne": (50.9375, 6.9603),
    "köln": (50.9375, 6.9603),
    "düsseldorf": (51.2277, 6.7735),
    "stuttgart": (48.7758, 9.1829),
    "leipzig": (51.3397, 12.3731),
    "zurich": (47.3769, 8.5417),
    "zürich": (47.3769, 8.5417),
    "basel": (47.5596, 7.5886),
    "bern": (46.9480, 7.4474),
    "geneva": (46.2044, 6.1432),
    "genf": (46.2044, 6.1432),
    "lausanne": (46.5197, 6.6323),
}

_COUNTRY_DISTANCE_FALLBACK = {
    "at": 280.0,
    "de": 560.0,
    "ch": 700.0,
    "dach": 500.0,
}

_AUSTRIA_HINTS = ["austria", "österreich", ".at"]
_GERMANY_HINTS = ["germany", "deutschland", ".de"]
_SWITZERLAND_HINTS = ["switzerland", "schweiz", ".ch"]

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _distance_from_vienna_km(location: str) -> float | None:
    loc = (location or "").lower()
    if not loc:
        return None

    vienna = _DACH_LOCATION_POINTS["vienna"]
    for token, (lat, lon) in _DACH_LOCATION_POINTS.items():
        if token in loc:
            return _haversine_km(vienna[0], vienna[1], lat, lon)

    if "remote" in loc or "dach" in loc:
        return _COUNTRY_DISTANCE_FALLBACK["dach"]
    if any(k in loc for k in _AUSTRIA_HINTS):
        return _COUNTRY_DISTANCE_FALLBACK["at"]
    if any(k in loc for k in _GERMANY_HINTS):
        return _COUNTRY_DISTANCE_FALLBACK["de"]
    if any(k in loc for k in _SWITZERLAND_HINTS):
        return _COUNTRY_DISTANCE_FALLBACK["ch"]
    return None


def vienna_distance_score(location: str) -> float:
    d = _distance_from_vienna_km(location)
    if d is None:
        return 2.4
    if d <= 25:
        return 5.0
    if d <= 100:
        return 4.7
    if d <= 250:
        return 4.2
    if d <= 450:
        return 3.5
    if d <= 700:
        return 2.8
    if d <= 900:
        return 2.2
    return 1.8


def language_score(language_hint: str, company: str) -> float:
    lang = (language_hint or "").lower()
    if lang == "en":
        return 5.0
    if lang == "de":
        # German-only is allowed, but clearly lower priority than English listings.
        return 1.2
    if "international" in company.lower():
        return 3.8
    return 2.8


def it_management_focus_score(title: str) -> float:
    """
    Score how closely a title matches management focus.

    5.0: clear management leadership role
    3.0: management title with strong engineering/domain signal
    2.0: engineer/devops IC role
    1.0: explicitly non-IT indicators
    """
    t = title.lower()

    if _contains_any(t, EXCLUDE_KEYWORDS):
        return 1.0

    has_engineering = _contains_any(t, [
        "engineer",
        "software engineer",
        "platform engineer",
        "cloud engineer",
        "devops engineer",
        "site reliability engineer",
        "sre engineer",
        "backend engineer",
        "infrastructure engineer",
        "softwareentwickler",
        "entwicklungsingenieur",
    ]) or _contains_any(t, DOMAIN_KEYWORDS)
    has_management = _contains_any(t, MANAGEMENT_KEYWORDS)

    # Prefer management and leadership roles over IC engineering roles.
    if has_management and has_engineering:
        return 5.0

    if has_management:
        return 1.5
    if has_engineering:
        return 2.0
    return 1.0


def score_job(job: dict) -> float:
    ls = vienna_distance_score(job.get("location", ""))
    lang = language_score(job.get("language_hint", ""), job.get("company", ""))
    focus = it_management_focus_score(job.get("title", ""))
    # Weighted formula: distance from Vienna 35%, language 35%,
    # IT relevance 30%. (Salary was dropped: no active source populates it.)
    raw = 0.35 * ls + 0.35 * lang + 0.30 * focus
    return round(max(1.0, min(5.0, raw)), 1)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def fingerprint(job: dict) -> str:
    t = re.sub(r"\s+", " ", (job.get("title") or "").lower().strip())
    c = re.sub(r"\s+", " ", (job.get("company") or "").lower().strip())
    return f"{t}|{c}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    in_path = "/tmp/jobs/jobs_raw_ai.json" if Path("/tmp/jobs/jobs_raw_ai.json").exists() else "/tmp/jobs/jobs_raw.json"
    with open(in_path, encoding="utf-8") as f:
        data = json.load(f)

    jobs: list[dict] = data["jobs"]
    # If AI pre-rank enrichment was applied, keep those corrected fields in the ranking input.
    ai_pre_rank = bool(data.get("ai_pre_rank_enriched", False))
    target_rank_count = int(os.getenv("RANK_TARGET_COUNT", "20"))
    print(f"Input: {len(jobs)} raw jobs (from {in_path})")
    if ai_pre_rank:
        print(f"AI pre-rank updates: {data.get('ai_pre_rank_updates', 0)}")

    # 1. Relevance filter
    relevant = [j for j in jobs if is_relevant(j)]

    # 1b. Enforce source-domain consistency (especially for proxy RSS results).
    def _source_domain_ok(job: dict) -> bool:
        src = (job.get("source_name") or "").lower()
        url = (job.get("application_url") or job.get("source_url") or "").lower()
        host = urlparse(url).netloc.lower()
        if "stepstone" in src:
            return "stepstone." in host
        if "indeed" in src:
            return "indeed." in host
        if "linkedin" in src:
            return "linkedin.com" in host
        if src == "jobs_ch":
            return "jobs.ch" in host
        if src.startswith("karriere_"):
            return "karriere.at" in host
        return True

    relevant = [j for j in relevant if _source_domain_ok(j)]
    print(f"After title filter: {len(relevant)}")

    # If strict filtering is too tight, add a controlled relaxed fallback so
    # ranking still yields a useful volume (target: up to 20 by default).
    if len(relevant) < target_rank_count:
        strict_fps = {fingerprint(j) for j in relevant}
        relaxed_pool = [j for j in jobs if is_relevant_relaxed(j) and _source_domain_ok(j)]
        for j in relaxed_pool:
            fp = fingerprint(j)
            if fp in strict_fps:
                continue
            relevant.append(j)
            strict_fps.add(fp)
            if len(relevant) >= target_rank_count:
                break
        print(f"After relaxed fallback: {len(relevant)}")

    # Optional language gating for issue quality.
    rank_language_only = os.getenv("RANK_LANGUAGE_ONLY", "").strip().lower()
    if rank_language_only:
        relevant = [
            j for j in relevant
            if (j.get("language_hint") or "").lower() == rank_language_only
        ]

    # 2. Deduplication
    seen: dict[str, bool] = {}
    deduped: list[dict] = []
    for j in relevant:
        fp = fingerprint(j)
        if fp not in seen:
            seen[fp] = True
            deduped.append(j)
    print(f"After dedup: {len(deduped)}")

    # 3. Score
    for j in deduped:
        j["score"] = score_job(j)

    # 4. Sort descending
    ranked = sorted(deduped, key=lambda j: j["score"], reverse=True)

    # 5. Prefer English roles first; retain German only to fill the list.
    english = [j for j in ranked if (j.get("language_hint") or "").lower() == "en"]
    non_english = [j for j in ranked if (j.get("language_hint") or "").lower() != "en"]
    ranked = english + non_english

    # 6. Preserve cross-source spread when multiple sources exist.
    # First take one item per source in score order, then fill remaining slots.
    by_source: dict[str, list[dict]] = defaultdict(list)
    for job in ranked:
        by_source[job.get("source_name", "unknown")].append(job)

    diversified: list[dict] = []
    source_order = sorted(
        by_source,
        key=lambda src: by_source[src][0]["score"] if by_source[src] else 0,
        reverse=True,
    )
    for src in source_order:
        if by_source[src]:
            diversified.append(by_source[src].pop(0))

    for src in source_order:
        diversified.extend(by_source[src])

    # 7. Per-source diversity cap (≤50% of final list, minimum 10 per source)
    # A hard min of 10 prevents over-pruning when only 1–2 sources are active.
    source_counts: dict[str, int] = {}
    capped: list[dict] = []
    cap = max(10, round(len(diversified) * 0.50))
    for j in diversified:
        src = j.get("source_name", "unknown")
        if source_counts.get(src, 0) < cap:
            source_counts[src] = source_counts.get(src, 0) + 1
            capped.append(j)
        if len(capped) >= target_rank_count:
            break

    # Order strictly by the weighted score (language is already weighted at
    # 30% within the score, so no extra language bias is applied here).
    capped = sorted(capped, key=lambda j: j["score"], reverse=True)

    print(f"Final ranked: {len(capped)}")

    # Per-source unique contribution (after filter + dedup, BEFORE the top-N
    # shortlist cap). This is the honest "what was useful" signal: how many
    # de-duplicated jobs each source actually contributed to the pool.
    # Prefer the scraper's authoritative count (computed over the FULL pool
    # before the raw 200-job cap); fall back to this ranker's local view.
    deduped_source_stats: dict = data.get("deduped_source_stats") or {}
    if not deduped_source_stats:
        computed: dict[str, int] = {}
        for j in deduped:
            src = j.get("source_name", "unknown")
            computed[src] = computed.get(src, 0) + 1
        deduped_source_stats = computed

    output = {
        "date": data.get("date", str(date.today())),
        "source_stats": data.get("stats", {}),
        "deduped_source_stats": deduped_source_stats,
        "total_raw": len(jobs),
        "total_relevant": len(relevant),
        "total_deduped": len(deduped),
        "ranked_source_count": len({j.get("source_name", "unknown") for j in capped}),
        "ai_pre_rank_enriched": ai_pre_rank,
        "second_pass_count": data.get("second_pass_count", 0),
        "extra_sources_merged": data.get("second_pass_count", 0) > 0,
        "jobs": capped,
    }
    out_path = "/tmp/jobs/jobs_ranked.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
