"""
Deduplicates, filters by relevance, and deterministically scores raw jobs.

Reads  /tmp/jobs/jobs_raw.json
Writes /tmp/jobs/jobs_ranked.json
"""

import json
import os
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import position_filter
from locations import (
    PLACEHOLDER,
    _is_out_of_geo,
    load_location_cache,
    normalize_location,
    save_location_cache,
)
from scoring import _load_geocode_cache, _save_geocode_cache, score_job
from text_match import _contains_any

# ---------------------------------------------------------------------------
# Relevance filter
# ---------------------------------------------------------------------------

# Role-rule keyword lists now live in .github/config/position_filter.json
# (loaded via position_filter.POSITION_FILTER); the data-integrity lists below
# (job-URL / non-job-title / bad-URL patterns) stay inline.

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


def _has_it_signal(job: dict) -> bool:
    """True when the title or company carries an explicit software/IT signal."""
    title = (job.get("title") or "").lower()
    company = (job.get("company") or "").lower()
    it_signal = position_filter.POSITION_FILTER["it_signal_terms"]
    return _contains_any(title, it_signal) or _contains_any(company, it_signal)


def _hard_reject_common(title: str, url: str, ai_rel: str, ai_url_quality: str) -> bool:
    """
    Reject checks shared by both the strict and relaxed relevance gates.
    Returns True when the job should be rejected outright.
    """
    if ai_rel == "reject":
        return True
    if ai_url_quality in {"search", "listing"}:
        return True
    if any(re.search(p, title) for p in NON_JOB_TITLE_PATTERNS):
        return True
    if any(host in url for host in ("bing.com/search", "google.com/search", "duckduckgo.com/html")):
        return True
    if any(p in url for p in BAD_URL_PATTERNS):
        return True
    return False


def _source_domain_ok(job: dict) -> bool:
    """Enforce source-domain consistency (especially for proxy RSS results)."""
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


def is_relevant(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    url = (job.get("application_url") or job.get("source_url") or "").lower()
    ai_rel = (job.get("ai_relevance") or "").lower()
    ai_url_quality = (job.get("ai_url_quality") or "").lower()

    if _hard_reject_common(title, url, ai_rel, ai_url_quality):
        return False

    # If URL lacks typical job path hints, require stronger job-title signal.
    has_job_url_hint = any(h in url for h in JOB_URL_HINTS)
    pf = position_filter.POSITION_FILTER
    if any(kw in title for kw in pf["excluded_areas"]):
        return False

    # Reject non-IT engineering disciplines (mechanical/electrical/industrial/…).
    if _contains_any(title, pf["non_it_excluded"]):
        return False

    cto_like = bool(re.search(r"\bcto\b", title)) or "chief technology officer" in title
    has_role = any(kw in title for kw in pf["in_focus_roles"])
    has_domain = any(kw in title for kw in pf["domain_terms"])
    has_management = _contains_any(title, pf["management_terms"])

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

    # Strict pool targets management / leadership roles only (Engineering /
    # Program Manager, Lead, Director, Head of…, VP, CTO). Individual-contributor
    # titles (Senior/Staff/Platform/Cloud Engineer, Developer) are intentionally
    # excluded here and only enter via is_relevant_relaxed as a fill-in when too
    # few leadership roles are found.
    if not (cto_like or has_management):
        return False

    # Restrict to genuinely IT/software roles: a generic "engineering" title
    # (which in DACH is frequently mechanical/electrical/industrial) only
    # qualifies when the title or company carries an explicit software/IT
    # signal. CTO and clear tech leadership titles already contain such a signal.
    return _has_it_signal(job)


def is_relevant_relaxed(job: dict) -> bool:
    """
    Broader relevance gate used only as fallback when strict filtering
    produces too few results.
    """
    title = (job.get("title") or "").lower()
    url = (job.get("application_url") or job.get("source_url") or "").lower()
    ai_rel = (job.get("ai_relevance") or "").lower()
    ai_url_quality = (job.get("ai_url_quality") or "").lower()

    if _hard_reject_common(title, url, ai_rel, ai_url_quality):
        return False
    pf = position_filter.POSITION_FILTER
    if any(kw in title for kw in pf["excluded_areas"]):
        return False
    if _contains_any(title, pf["non_it_excluded"]):
        return False

    # Allow strong leadership even without the full strict role+domain pair, but
    # never individual contributors — the shortfall fill stays leadership-only.
    has_mgmt = _contains_any(title, pf["management_terms"])
    has_domain = _contains_any(title, pf["domain_terms"])
    cto_like = bool(re.search(r"\bcto\b", title)) or "chief technology officer" in title

    if not (cto_like or (has_mgmt and has_domain)):
        return False

    # Same IT-only restriction as the strict gate.
    return _has_it_signal(job)


def _renormalize_rows(jobs: list[dict]) -> None:
    """Re-standardize rows the AI stage rewrote or scrape_extra appended (detected
    by display != location); scrape-normalized rows (display == location) are left
    untouched so their coords and score are preserved. Never raises."""
    for j in jobs:
        if j.get("display") != j.get("location"):
            norm = normalize_location(j.get("location", ""))
            j["location"] = norm["display"]
            j["place"] = norm["place"]
            j["region"] = norm["region"]
            j["country"] = norm["country"]
            j["coords"] = norm["coords"]
            j["display"] = norm["display"]


def _row_out_of_geo(job: dict) -> bool:
    """True only when the country is known and foreign; an unknown (empty/placeholder/
    non-string) country is kept — unknown is not out-of-geography."""
    country = job.get("country")
    country = country.strip() if isinstance(country, str) else ""
    if not country or country == PLACEHOLDER:
        return False
    region = job.get("region")
    return _is_out_of_geo(country, region if isinstance(region, str) else None)


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
    # Reuse geocoded coordinates persisted by earlier passes in this run.
    _load_geocode_cache()
    print(f"Input: {len(jobs)} raw jobs (from {in_path})")
    if ai_pre_rank:
        print(f"AI pre-rank updates: {data.get('ai_pre_rank_updates', 0)}")

    # Re-standardize AI-rewritten / second-pass rows, then drop out-of-geography
    # BEFORE the relevance/relaxed-fallback so the shortlist tops up from a
    # geo-clean pool (not short after a late drop).
    load_location_cache()
    _renormalize_rows(jobs)
    save_location_cache()
    before_geo = len(jobs)
    jobs = [j for j in jobs if not _row_out_of_geo(j)]
    if len(jobs) != before_geo:
        print(f"After out-of-geo drop: {len(jobs)} (−{before_geo - len(jobs)})")

    # 1. Relevance filter
    relevant = [j for j in jobs if is_relevant(j)]

    # 1b. Enforce source-domain consistency (especially for proxy RSS results).
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
    _save_geocode_cache()
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
