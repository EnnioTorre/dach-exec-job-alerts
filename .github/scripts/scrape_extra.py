"""
Second-pass scraper — fetches AI-suggested extra sources and merges results
into the existing jobs_raw.json so rank_jobs.py can re-score the full set.

Reads  /tmp/jobs/extra_sources.json  (written by ai_suggest_sources.py)
       /tmp/jobs/jobs_raw.json        (existing first-pass data)
Writes /tmp/jobs/jobs_raw.json        (merged, in-place update)

If extra_sources.json does not exist the script exits 0 silently — this is the
normal path when the first pass already found enough listings.
"""

import json
import os
import sys
from pathlib import Path

# Reuse all fetch/parse logic from the first-pass scraper.
sys.path.insert(0, os.path.dirname(__file__))
from scrape_jobs import (
    fetch,
    extract_jsonld_jobs,
    normalize_jsonld,
    enrich_from_company_page,
    PARSER_MAP,
    parse_rss,
    parse_stepstone,
    parse_karriere_at,
    parse_google_jobs,
    parse_jobs_ch,
    _infer_language_hint,
)

EXTRA_SOURCES_PATH = "/tmp/jobs/extra_sources.json"
RAW_PATH = "/tmp/jobs/jobs_raw.json"


def scrape_url(url: str, name: str) -> list[dict]:
    """Fetch one URL: try JSON-LD first, then a matching HTML parser, then generic."""
    html = fetch(url)
    if not html:
        print(f"  SKIP {name}: fetch failed")
        return []

    # JSON-LD is always preferred
    jobs = [normalize_jsonld(j, name) for j in extract_jsonld_jobs(html)]
    if jobs:
        print(f"  JSON-LD: {len(jobs)} jobs")
        return jobs

    # Pick the closest HTML parser from the known map, or do a generic title scan
    parser = PARSER_MAP.get(name)
    if not parser:
        # Heuristic: match common domain patterns
        if "stepstone" in url:
            parser = parse_stepstone
        elif "karriere" in url:
            parser = parse_karriere_at
        elif "indeed" in url:
            # Indeed: use RSS endpoint if we have an rss/feed URL, else html parse
            if "/rss" in url or "rss." in url:
                parser = parse_rss
            else:
                parser = parse_rss  # prefer RSS for Indeed even on HTML URLs
        elif "linkedin" in url or "google" in url:
            parser = parse_google_jobs
        elif "jobs.ch" in url:
            parser = parse_jobs_ch

    if parser:
        jobs = parser(html, name)
        print(f"  HTML parser ({parser.__name__}): {len(jobs)} jobs")

        # For google/linkedin discovery sources, enrich by scraping destination pages.
        if parser.__name__ == "parse_google_jobs":
            for row in jobs[:8]:
                row.update(enrich_from_company_page(row))
    else:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        for tag in soup.find_all(["h2", "h3"], string=True):
            text = tag.get_text(strip=True)
            if len(text) > 5:
                link = tag.find_parent("a") or tag.find("a")
                href = link["href"] if link and link.get("href") else ""
                if href and not href.startswith("http"):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                jobs.append({
                    "title": text,
                    "company": "",
                    "location": "",
                    "source_name": name,
                    "source_url": href,
                    "application_url": href,
                    "publish_date": "",
                    "salary_text": "",
                    "language_hint": _infer_language_hint(text),
                })
        print(f"  Generic heading scan: {len(jobs)} jobs")

    return [j for j in jobs if j.get("title")]


def main() -> None:
    extra_path = Path(EXTRA_SOURCES_PATH)
    if not extra_path.exists():
        print("No extra_sources.json — skipping second scrape pass")
        sys.exit(0)

    with open(extra_path, encoding="utf-8") as f:
        extra = json.load(f)

    new_sources: list[dict] = extra.get("new_sources", [])
    improvements: list[dict] = extra.get("source_improvements", [])

    if not new_sources and not improvements:
        print("extra_sources.json has no actionable URLs — skipping")
        sys.exit(0)

    # Load existing raw data
    with open(RAW_PATH, encoding="utf-8") as f:
        raw_data = json.load(f)

    existing_jobs: list[dict] = raw_data.get("jobs", [])
    existing_stats: dict = raw_data.get("stats", {})
    second_pass_jobs: list[dict] = []

    # Scrape new sources suggested by AI
    for src in new_sources:
        name = src.get("name", "ai_extra")
        url = src.get("url", "")
        if not url:
            continue
        print(f"\nFetching (new) {name}: {url}")
        jobs = scrape_url(url, name)
        existing_stats[name] = len(jobs)
        second_pass_jobs.extend(jobs)

    # Scrape improved URLs for existing thin sources
    for improvement in improvements:
        src_name = improvement.get("source", "improved")
        improved_url = improvement.get("improved_url", "")
        if not improved_url:
            continue
        improved_name = f"{src_name}_v2"
        print(f"\nFetching (improved) {improved_name}: {improved_url}")
        jobs = scrape_url(improved_url, improved_name)
        existing_stats[improved_name] = len(jobs)
        second_pass_jobs.extend(jobs)

    print(f"\nSecond pass: {len(second_pass_jobs)} new jobs fetched")

    # Merge, cap total at 120 to avoid downstream memory / token issues
    merged = existing_jobs + second_pass_jobs
    merged = merged[:120]

    raw_data["jobs"] = merged
    raw_data["stats"] = existing_stats
    raw_data["second_pass_count"] = len(second_pass_jobs)

    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    print(f"Updated {RAW_PATH} — total jobs: {len(merged)}")


if __name__ == "__main__":
    main()
