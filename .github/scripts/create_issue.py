"""
Formats the final job digest and creates a GitHub issue via gh CLI.

Reads  /tmp/jobs/jobs_enriched.json  (preferred)
       /tmp/jobs/jobs_ranked.json    (fallback)

Exits 0 on success, 1 on failure.
"""

import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


def load_data() -> dict:
    for path in ("/tmp/jobs/jobs_enriched.json", "/tmp/jobs/jobs_ranked.json"):
        if Path(path).exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError("No job data file found in /tmp/jobs/")


def ensure_label(label: str, color: str = "0075ca") -> bool:
    """Create the label if it doesn't exist; return True if successful or exists."""
    result = subprocess.run(
        ["gh", "label", "create", label, "--color", color],
        capture_output=True,
        text=True,
    )
    # Succeed if exit code 0, or if the label already exists (common error is "already exists")
    if result.returncode == 0:
        return True
    if "already exists" in result.stderr.lower():
        return True
    # Label creation failed; log it for debugging
    error_msg = result.stderr.strip() if result.stderr else "unknown error"
    print(f"Warning: label creation failed ({error_msg}); issue will be created without labels", file=sys.stderr)
    return False


def format_body(data: dict) -> str:
    today = data.get("date", str(date.today()))
    jobs: list[dict] = data.get("jobs", [])
    ai_enriched: bool = data.get("ai_enriched", False)
    stats: dict = data.get("source_stats", {})
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    lines: list[str] = []
    lines += [
        f"**Date:** {today}",
        f"**Sources scraped:** {len(stats)}",
        f"**Total raw listings:** {data.get('total_raw', '?')}",
        f"**After filter + dedup:** {data.get('total_deduped', '?')}",
        f"**AI pre-rank cleanup:** {'✅ Yes' if data.get('ai_pre_rank_enriched') else '⚠️ No'}",
        f"**AI enrichment:** {'✅ Yes (GitHub Models)' if ai_enriched else '⚠️ No — deterministic fallback'}",
        "",
    ]

    def _is_job_ad_url(url: str) -> bool:
        u = (url or "").lower()
        if not u:
            return False
        if any(x in u for x in ("google.com/search", "bing.com/search", "duckduckgo.com/html")):
            return False
        if any(x in u for x in ("/search", "?q=", "/blog", "/news", "/salary", "/gehalt")):
            return False
        if "karriere.at" in u:
            if "/jobs/" not in u:
                return False
            if any(u.endswith(x) for x in (
                "/jobs/cto",
                "/jobs/head-of-engineering",
                "/jobs/software-engineering",
                "/jobs/platform-engineering",
                "/jobs/cloud-engineering",
            )):
                return False
        if re.match(r"https?://[^/]+/?$", u):
            return False
        return True

    def _looks_useful(job: dict) -> bool:
        title = (job.get("title") or "").lower()
        if any(x in title for x in ("jobs |", "jobbörse", "stellenmarkt", "karriere.at")):
            return False
        return _is_job_ad_url(job.get("application_url") or job.get("source_url") or "")

    useful_jobs = [j for j in jobs if _looks_useful(j)]
    english_jobs = [j for j in useful_jobs if (j.get("language_hint") or "").lower() == "en"]
    non_english_jobs = [j for j in useful_jobs if (j.get("language_hint") or "").lower() != "en"]
    digest_jobs = (english_jobs + non_english_jobs)[:10]

    # ---- Job table ----
    if ai_enriched:
        ai_jobs: list[dict] = data.get("ai_enrichment", {}).get("top_jobs", [])
        if ai_jobs:
            lines += [
                "## 🏆 AI-Ranked Top Opportunities",
                "",
                "| # | Role | Company | Location | Score | Why Apply |",
                "|---|------|---------|----------|-------|-----------|",
            ]
            # Build a quick lookup: (title, company) → application_url
            url_map = {
                (j.get("title", ""), j.get("company", "")): j.get("application_url") or j.get("source_url", "")
                for j in jobs
            }
            for j in ai_jobs:
                title = j.get("title", "N/A")
                company = j.get("company", "N/A")
                location = j.get("location", "N/A")
                ai_score = j.get("ai_score", "")
                why = j.get("why_apply", "")
                url = url_map.get((title, company), "")
                title_md = f"[{title}]({url})" if url else title
                lines.append(f"| {j.get('rank', '')} | {title_md} | {company} | {location} | {ai_score} | {why} |")
            lines.append("")
    # Deterministic top-10 (always shown as a section; also sole section when AI unavailable)
    if not ai_enriched or not data.get("ai_enrichment", {}).get("top_jobs"):
        lines += [
            "## 📋 Top Executive Roles (deterministic ranking)",
            "",
            "| # | Role | Company | Location | Lang | Score | Salary |",
            "|---|------|---------|----------|------|-------|--------|",
        ]
        for i, j in enumerate(digest_jobs, 1):
            title = j.get("title", "N/A")
            company = j.get("company", "N/A")
            location = j.get("location", "N/A")
            lang = (j.get("language_hint") or "").lower() or "n/a"
            score = j.get("score", "")
            salary = j.get("salary_text") or "N/A"
            url = j.get("application_url") or j.get("source_url", "")
            title_md = f"[{title}]({url})" if url else title
            lines.append(f"| {i} | {title_md} | {company} | {location} | {lang} | {score} | {salary} |")
        lines.append("")

    # ---- Source performance table ----
    lines += [
        "## 📊 Source Performance",
        "",
        "| Source | Listings | AI Quality | Recommendation |",
        "|--------|----------|------------|----------------|",
    ]
    ai_src_map = {
        s["source"]: s
        for s in (data.get("ai_enrichment") or {}).get("source_analysis", [])
    }
    for src, count in sorted(stats.items(), key=lambda x: -x[1]):
        ai_info = ai_src_map.get(src, {})
        quality = ai_info.get("quality_rating", "—")
        rec = ai_info.get("recommendation", "—")
        note = ai_info.get("note", "")
        rec_str = f"{rec} — {note}" if note else rec
        lines.append(f"| `{src}` | {count} | {quality}/5 | {rec_str} |")
    lines += [
        "",
        "---",
        f"*Auto-generated by [dach-exec-job-alerts](https://github.com/{repo}/actions) · {today}*",
    ]
    return "\n".join(lines)


def create_issue(title: str, body: str, labels: list[str]) -> bool:
    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    # Only add labels if they were successfully ensured
    for label in labels:
        cmd += ["--label", label]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Issue created: {result.stdout.strip()}")
        return True
    
    # If label error, retry without labels
    if "label" in result.stderr.lower() and "not found" in result.stderr.lower():
        print(f"Label not found; retrying without labels...", file=sys.stderr)
        cmd = ["gh", "issue", "create", "--title", title, "--body", body]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Issue created (without labels): {result.stdout.strip()}")
            return True
    
    print(f"gh issue create failed:\n{result.stderr.strip()}", file=sys.stderr)
    return False


def main() -> None:
    data = load_data()
    today = data.get("date", str(date.today()))
    jobs = data.get("jobs", [])

    # Try to ensure the label exists, but don't fail if it doesn't
    label_created = ensure_label("job-digest", "0075ca")

    if not jobs:
        title = f"[DACH Jobs] No Listings Retrieved — {today}"
        stats = data.get("source_stats", {})
        body_lines = [f"No relevant job listings were retrieved on {today}.\n"]
        body_lines.append("**Source diagnostics:**")
        for src, count in stats.items():
            body_lines.append(f"- `{src}`: {count} listings")
        body = "\n".join(body_lines)
    else:
        ai_enriched = data.get("ai_enriched", False)
        marker = "AI-ranked" if ai_enriched else "auto-ranked"
        title = f"[DACH Jobs] Top Exec Roles ({marker}) — {today}"
        body = format_body(data)

    # Pass labels only if they were successfully created
    labels = ["job-digest"] if label_created else []
    success = create_issue(title, body, labels)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
