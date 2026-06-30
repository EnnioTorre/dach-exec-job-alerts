"""
Deduplicates, filters by relevance, and deterministically scores raw jobs.

Reads  /tmp/jobs/jobs_raw.json
Writes /tmp/jobs/jobs_ranked.json
"""

import json
import os
import re
from urllib.parse import urlparse
from collections import defaultdict
from datetime import date

# ---------------------------------------------------------------------------
# Relevance filter
# ---------------------------------------------------------------------------

ROLE_KEYWORDS = [
    "cto",
    "chief technology officer",
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
    # German software/infra leadership terms
    "teamleitung engineering",
    "leitung softwareentwicklung",
    "leiter softwareentwicklung",
    "leitung plattform",
    "leiter plattform",
    "leitung cloud",
    "leiter cloud",
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
]


def is_relevant(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    url = (job.get("application_url") or job.get("source_url") or "").lower()

    if any(re.search(p, title) for p in NON_JOB_TITLE_PATTERNS):
        return False

    if any(host in url for host in ("bing.com/search", "google.com/search", "duckduckgo.com/html")):
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


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

_VIENNA = ["vienna", "wien", "1010", "1020", "1030", "1040", "1050",
           "1060", "1070", "1080", "1090", "1100", "1110", "1120",
           "1130", "1140", "1150", "1160", "1170", "1180", "1190",
           "1200", "1210", "1220", "1230"]
_BOLZANO = ["bolzano", "bozen", "south tyrol", "sudtirol", "südtirol"]
_AUSTRIA = ["austria", "österreich", "graz", "linz", "salzburg",
            "innsbruck", "klagenfurt", "villach", ".at"]
_SWITZERLAND = ["switzerland", "schweiz", "zürich", "zurich", "basel",
                "bern", "geneva", "genf", "lausanne", ".ch"]
_GERMANY = ["germany", "deutschland", "berlin", "munich", "münchen",
            "frankfurt", "hamburg", "cologne", "köln", "düsseldorf",
            "stuttgart", "leipzig", ".de"]

_BIG_TECH = [
    "google", "amazon", "aws", "microsoft", "apple", "meta", "spotify",
    "zalando", "booking", "siemens", "bosch", "bmw", "mercedes", "sap",
    "intel", "oracle", "red hat", "ibm", "cisco", "salesforce", "atlassian",
    "netflix", "airbnb", "stripe", "github", "gitlab", "hashicorp",
]


def location_score(location: str) -> float:
    loc = location.lower()
    if any(k in loc for k in _VIENNA):
        return 5.0
    if any(k in loc for k in _BOLZANO):
        # Bolzano/Bozen is near-Austria and fits DACH-adjacent exec searches.
        return 4.5
    if any(k in loc for k in _AUSTRIA):
        return 4.0
    if any(k in loc for k in _SWITZERLAND):
        return 3.5
    if any(k in loc for k in _GERMANY):
        return 3.0
    if "remote" in loc:
        return 3.5
    return 2.0


def company_score(company: str) -> float:
    c = company.lower()
    if any(k in c for k in _BIG_TECH):
        return 5.0
    if len(company.strip()) > 2:
        return 3.0
    return 2.0


def salary_score(salary_text: str) -> float:
    if not salary_text:
        return 2.5
    # Extract all numbers; treat largest as the ceiling/max
    nums = [int(n.replace(".", "").replace(",", ""))
            for n in re.findall(r"[\d.,]+", salary_text)
            if n.replace(".", "").replace(",", "").isdigit()]
    if not nums:
        return 2.5
    peak = max(nums)
    if peak >= 150_000:
        return 5.0
    if peak >= 120_000:
        return 4.0
    if peak >= 90_000:
        return 3.0
    if peak >= 60_000:
        return 2.0
    return 1.5


def language_score(language_hint: str, company: str) -> float:
    if (language_hint or "").lower() == "en":
        return 5.0
    if "international" in company.lower():
        return 4.0
    return 1.5


def it_management_focus_score(title: str) -> float:
    """
    Score how closely a title matches IT-management focus.

    5.0: IT + management leadership role
    2.5: IT role but not clearly management
    1.5: management role without clear IT signal
    1.0: explicitly non-IT indicators
    """
    t = title.lower()

    if _contains_any(t, EXCLUDE_KEYWORDS):
        return 1.0

    has_cto = bool(re.search(r"\bcto\b", t)) or "chief technology officer" in t
    has_it = has_cto or _contains_any(t, DOMAIN_KEYWORDS) or bool(re.search(r"\bit\b", t))
    has_management = has_cto or _contains_any(t, MANAGEMENT_KEYWORDS)

    # Pure CTO titles often surface less actionable results than explicit engineering-manager roles.
    if has_cto and not _contains_any(t, ["engineering", "platform", "cloud", "software", "devops"]):
        return 2.0

    if has_it and has_management:
        return 5.0
    if has_it:
        return 2.5
    if has_management:
        return 1.5
    return 1.0


def score_job(job: dict) -> float:
    ls = location_score(job.get("location", ""))
    cs = company_score(job.get("company", ""))
    ss = salary_score(job.get("salary_text", ""))
    lang = language_score(job.get("language_hint", ""), job.get("company", ""))
    focus = it_management_focus_score(job.get("title", ""))
    raw = 0.20 * ls + 0.10 * cs + 0.10 * ss + 0.30 * lang + 0.30 * focus
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
    with open("/tmp/jobs/jobs_raw.json", encoding="utf-8") as f:
        data = json.load(f)

    jobs: list[dict] = data["jobs"]
    print(f"Input: {len(jobs)} raw jobs")

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
        if len(capped) >= 40:
            break

    # Preserve diversification constraints and strongly prefer English listings at the top.
    capped = sorted(
        capped,
        key=lambda j: ((j.get("language_hint") or "").lower() == "en", j["score"]),
        reverse=True,
    )

    print(f"Final ranked: {len(capped)}")

    output = {
        "date": data.get("date", str(date.today())),
        "source_stats": data.get("stats", {}),
        "total_raw": len(jobs),
        "total_relevant": len(relevant),
        "total_deduped": len(deduped),
        "ranked_source_count": len({j.get("source_name", "unknown") for j in capped}),
        "jobs": capped,
    }
    out_path = "/tmp/jobs/jobs_ranked.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
