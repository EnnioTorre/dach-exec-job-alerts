#!/usr/bin/env python3
"""
SearXNG query probe — measures per-query job-URL yield to design optimal
scrape sources. Runs on a GitHub runner where SearXNG can reach upstream
engines (locally the corporate proxy blocks it).

Reads SCRAPER_SEARXNG_URL (default http://localhost:8080).
Prints, for each candidate query:
  total results, job-matching results, and a few sample job URLs.
Also probes pageno depth to see if fetching pages 2/3 adds results.
"""
from __future__ import annotations

import json
import os
import time
from urllib.parse import urlparse

import requests

SEARXNG_URL = (os.getenv("SCRAPER_SEARXNG_URL") or "http://localhost:8080").rstrip("/")


def is_job_url(url: str, kind: str) -> bool:
    p = urlparse(url)
    host, path = p.netloc.lower(), p.path.lower()
    if any(b in host for b in ("google.", "bing.com", "duckduckgo.com", "wikipedia.org", "youtube.com")):
        return False
    if kind == "linkedin":
        return "linkedin.com" in host and "/jobs/view/" in path
    if kind == "stepstone":
        return "stepstone." in host and "/jobs/" in path
    if kind == "indeed":
        return "indeed." in host and ("/viewjob" in path or "/job" in path)
    if kind == "jobs_ch":
        return "jobs.ch" in host and ("/vacanc" in path or "/job" in path)
    if kind == "xing":
        return "xing.com" in host and ("/jobs/" in path or "/job/" in path)
    return any(h in path for h in ("/jobs/", "/job/", "/vacanc", "/viewjob", "/stellen", "/career"))


def search(q: str, pageno: int = 1) -> list[dict]:
    try:
        r = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": q, "format": "json", "language": "all", "safesearch": "0", "pageno": str(pageno)},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if r.status_code != 200:
            return [{"_err": f"HTTP {r.status_code}"}]
        return r.json().get("results", [])
    except Exception as e:  # noqa: BLE001
        return [{"_err": f"{type(e).__name__}: {e}"}]


# Candidate queries: (label, kind, query)
CANDIDATES = [
    # LinkedIn SERP (supplements guest API)
    ("li_at_lead", "linkedin", 'site:linkedin.com/jobs/view ("Head of Engineering" OR "VP Engineering" OR CTO) Austria'),
    ("li_de_lead", "linkedin", 'site:linkedin.com/jobs/view ("CTO" OR "VP Engineering" OR "Director of Engineering") Germany'),
    ("li_ch_lead", "linkedin", 'site:linkedin.com/jobs/view ("Engineering Manager" OR "Head of Engineering") Switzerland'),
    ("li_dach_principal", "linkedin", 'site:linkedin.com/jobs/view ("Principal Engineer" OR "Staff Engineer" OR "Tech Lead") Germany OR Austria'),
    # Stepstone
    ("ss_at", "stepstone", 'site:stepstone.at/jobs ("CTO" OR "Head of Engineering" OR "VP Engineering")'),
    ("ss_de", "stepstone", 'site:stepstone.de/jobs ("CTO" OR "Head of Engineering" OR "Engineering Manager")'),
    # Indeed
    ("in_de", "indeed", 'site:indeed.de ("Head of Engineering" OR "VP Engineering" OR "Engineering Manager")'),
    ("in_at", "indeed", 'site:indeed.com/viewjob OR site:indeed.de/viewjob "Engineering Manager" Austria'),
    # jobs.ch
    ("jch", "jobs_ch", 'site:jobs.ch ("Head of Engineering" OR "Engineering Manager" OR CTO)'),
    # Xing
    ("xing_de", "xing", 'site:xing.com/jobs ("CTO" OR "VP Engineering" OR "Head of Engineering") Germany'),
    # Broader multi-site sweeps
    ("multi_at", "generic", 'site:linkedin.com/jobs/view OR site:stepstone.at/jobs OR site:jobs.ch ("Director of Engineering" OR "Engineering Manager") Austria'),
    ("multi_ch", "generic", 'site:linkedin.com/jobs/view OR site:jobs.ch ("VP Engineering" OR "Head of Engineering") Switzerland OR Zurich OR Geneva'),
    # City-focused
    ("city_munich", "linkedin", 'site:linkedin.com/jobs/view ("Engineering Manager" OR CTO OR "Head of Engineering") Munich'),
    ("city_zurich", "linkedin", 'site:linkedin.com/jobs/view ("Engineering Manager" OR CTO OR "VP Engineering") Zurich'),
]


def main() -> None:
    print(f"SearXNG: {SEARXNG_URL}\n")
    summary = []
    for label, kind, q in CANDIDATES:
        res = search(q)
        if res and isinstance(res[0], dict) and res[0].get("_err"):
            print(f"[{label:16}] ERROR {res[0]['_err']}")
            summary.append((label, -1, -1))
            time.sleep(1)
            continue
        total = len(res)
        job_urls = [r.get("url", "") for r in res if is_job_url(r.get("url", ""), kind)]
        print(f"[{label:16}] total={total:3}  job_urls={len(job_urls):3}  kind={kind}")
        for u in job_urls[:2]:
            print(f"                    - {u}")
        summary.append((label, total, len(job_urls)))
        time.sleep(1.2)

    print("\n== pageno depth probe (li_de_lead) ==")
    q = CANDIDATES[1][2]
    seen = 0
    for pg in (1, 2, 3):
        res = search(q, pageno=pg)
        n = len([r for r in res if is_job_url(r.get("url", ""), "linkedin")])
        print(f"  pageno={pg}: job_urls={n} total={len(res)}")
        time.sleep(1.2)

    print("\n== SUMMARY (label, total, job_urls) sorted by job_urls ==")
    for label, total, jobs in sorted(summary, key=lambda x: x[2], reverse=True):
        print(f"  {label:16} total={total:3} job_urls={jobs:3}")


if __name__ == "__main__":
    main()
