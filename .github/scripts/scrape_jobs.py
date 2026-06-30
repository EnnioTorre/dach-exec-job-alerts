"""
Fetches job listings from multiple DACH sources.

Tries JSON-LD structured data first (most reliable), then site-specific HTML
parsers as fallback. Writes /tmp/jobs/jobs_raw.json.
"""

import json
import os
import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

SOURCES = [
    {"name": "stepstone_at_cto",   "url": "https://www.stepstone.at/jobs/cto",               "region": "AT"},
    {"name": "stepstone_at_hoe",   "url": "https://www.stepstone.at/jobs/head-of-engineering","region": "AT"},
    {"name": "karriere_at_cto",    "url": "https://www.karriere.at/jobs/cto",                 "region": "AT"},
    {"name": "karriere_at_hoe",    "url": "https://www.karriere.at/jobs/head-of-engineering", "region": "AT"},
    {"name": "indeed_at",          "url": "https://at.indeed.com/jobs?q=head+of+engineering+OR+CTO+OR+engineering+manager+OR+director+of+engineering&sort=date", "region": "AT"},
    {"name": "jobs_ch",            "url": "https://www.jobs.ch/en/vacancies/?term=head+of+engineering", "region": "CH"},
    {"name": "stepstone_de_hoe",   "url": "https://www.stepstone.de/jobs/head-of-engineering","region": "DE"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-AT,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def fetch(url: str, retries: int = 3) -> str | None:
    session = requests.Session()
    session.headers.update(HEADERS)
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=25, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code} for {url}")
            if r.status_code in (403, 429):
                break  # blocked — no point retrying
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
    # broader fallback: any article with a recognisable job-card class
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


def parse_indeed(html: str, source_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    # Indeed uses data-jk as the job key
    for card in soup.find_all("div", class_=re.compile(r"job_seen_beacon|jobCard|mosaic-provider-jobcards", re.I)):
        title_el = card.find("h2", class_=re.compile(r"jobTitle", re.I))
        if not title_el:
            title_el = card.find(["h2", "h3"])
        company_el = card.find(attrs={"data-testid": "company-name"}) or \
                     card.find(class_=re.compile(r"companyName|company", re.I))
        location_el = card.find(attrs={"data-testid": "text-location"}) or \
                      card.find(class_=re.compile(r"companyLocation|location", re.I))
        link_el = card.find("a", href=re.compile(r"/jobs/|/rc/clk"))
        if not title_el:
            continue
        href = ""
        if link_el:
            href = link_el.get("href", "")
            if not href.startswith("http"):
                href = "https://at.indeed.com" + href
        jobs.append({
            "title": _text(title_el),
            "company": _text(company_el),
            "location": _text(location_el) or "Austria",
            "source_name": source_name,
            "source_url": href,
            "application_url": href,
            "publish_date": "",
            "salary_text": "",
            "language_hint": "en",
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
    "stepstone_at_cto": parse_stepstone,
    "stepstone_at_hoe": parse_stepstone,
    "stepstone_de_hoe": parse_stepstone,
    "karriere_at_cto": parse_karriere_at,
    "karriere_at_hoe": parse_karriere_at,
    "indeed_at": parse_indeed,
    "jobs_ch": parse_jobs_ch,
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
        print(f"\nFetching {name} ...")
        html = fetch(url)
        if not html:
            print(f"  SKIP {name}: fetch failed")
            stats[name] = 0
            continue

        # Try JSON-LD structured data first
        jobs = [normalize_jsonld(j, name) for j in extract_jsonld_jobs(html)]
        if jobs:
            print(f"  JSON-LD: {len(jobs)} jobs")
        else:
            parser = PARSER_MAP.get(name)
            if parser:
                jobs = parser(html, name)
                print(f"  HTML parser: {len(jobs)} jobs")
            else:
                print(f"  No parser for {name}")

        # Drop records without a title
        jobs = [j for j in jobs if j.get("title")]
        stats[name] = len(jobs)
        all_jobs.extend(jobs)

    print(f"\nTotal raw: {len(all_jobs)} across {len(SOURCES)} sources")
    print(f"Stats: {stats}")

    output = {
        "date": str(date.today()),
        "stats": stats,
        "jobs": all_jobs[:80],  # cap to limit downstream token use
    }
    out_path = "/tmp/jobs/jobs_raw.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
