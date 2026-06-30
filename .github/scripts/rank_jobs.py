"""
Deduplicates, filters by relevance, and deterministically scores raw jobs.

Reads  /tmp/jobs/jobs_raw.json
Writes /tmp/jobs/jobs_ranked.json
"""

import json
import re
from datetime import date

# ---------------------------------------------------------------------------
# Relevance filter
# ---------------------------------------------------------------------------

TITLE_KEYWORDS = [
    "cto",
    "chief technology officer",
    "head of engineering",
    "head of platform",
    "head of cloud",
    "head of infrastructure",
    "head of it",
    "head of tech",
    "engineering manager",
    "director of engineering",
    "vp engineering",
    "vp of engineering",
    "vice president engineering",
    "chief engineer",
    "engineering lead",
    "technical director",
]


def is_relevant(job: dict) -> bool:
    title = (job.get("title") or "").lower()
    return any(kw in title for kw in TITLE_KEYWORDS)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

_VIENNA = ["vienna", "wien", "1010", "1020", "1030", "1040", "1050",
           "1060", "1070", "1080", "1090", "1100", "1110", "1120",
           "1130", "1140", "1150", "1160", "1170", "1180", "1190",
           "1200", "1210", "1220", "1230"]
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
        return 4.0
    if "international" in company.lower():
        return 4.0
    return 3.0


def score_job(job: dict) -> float:
    ls = location_score(job.get("location", ""))
    cs = company_score(job.get("company", ""))
    ss = salary_score(job.get("salary_text", ""))
    lang = language_score(job.get("language_hint", ""), job.get("company", ""))
    raw = 0.35 * ls + 0.20 * cs + 0.30 * ss + 0.15 * lang
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
    print(f"After title filter: {len(relevant)}")

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

    # 5. Per-source diversity cap (≤40% of final list from any one source)
    source_counts: dict[str, int] = {}
    capped: list[dict] = []
    cap = max(1, round(len(ranked) * 0.40))
    for j in ranked:
        src = j.get("source_name", "unknown")
        if source_counts.get(src, 0) < cap:
            source_counts[src] = source_counts.get(src, 0) + 1
            capped.append(j)
        if len(capped) >= 25:
            break

    print(f"Final ranked: {len(capped)}")

    output = {
        "date": data.get("date", str(date.today())),
        "source_stats": data.get("stats", {}),
        "total_raw": len(jobs),
        "total_relevant": len(relevant),
        "total_deduped": len(deduped),
        "jobs": capped,
    }
    out_path = "/tmp/jobs/jobs_ranked.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
