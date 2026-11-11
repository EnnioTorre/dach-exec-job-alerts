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


def _source_perf_funnel_section(data: dict, stats: dict, jobs: list[dict],
                                digest_jobs: list[dict]) -> list[str] | None:
    """Render the honest Source-Performance section from the Story-8 per-source /
    per-family funnel (pool→reached→passed→shortlisted) in `jobs_ranked.json`.
    Returns None when no funnel is present (pre-Story-8 artifact) so the caller
    renders the legacy deduped-unique table instead."""
    funnel = data.get("ranked_funnel")
    funnel = funnel if isinstance(funnel, dict) else {}
    by_source = funnel.get("by_source", {})
    by_source = by_source if isinstance(by_source, dict) else {}
    by_family = funnel.get("by_family", {})
    by_family = by_family if isinstance(by_family, dict) else {}
    if not by_source:
        return None

    def _f(src: str, stage: str) -> int:
        row = by_source.get(src, {})
        return row.get(stage, 0) if isinstance(row, dict) else 0

    def _pct(num: int, den: int) -> str:
        return "—" if den <= 0 else f"{round(100 * num / den)}%"

    top15_by_source: dict = {}
    for j in digest_jobs:
        src = j.get("source_name", "unknown")
        top15_by_source[src] = top15_by_source.get(src, 0) + 1

    active_sources = sum(1 for c in stats.values() if c > 0)
    productive = sum(1 for s in stats if _f(s, "passed") > 0)
    dead_weight = [s for s, c in stats.items() if c > 0 and _f(s, "passed") == 0]
    total_scraped = sum(stats.values())
    total_reached = sum(_f(s, "reached") for s in stats)
    total_passed = sum(_f(s, "passed") for s in stats)

    out = [
        "## 📊 Source Performance",
        "",
        f"**{len(stats)}** sources configured · **{active_sources}** returned listings · "
        f"**{productive}** passed the role filter.",
        f"Funnel: **{total_scraped}** scraped → **{total_reached}** reached ranking → "
        f"**{total_passed}** passed the role filter → **{len(jobs)}** shortlisted → "
        f"**{len(digest_jobs)}** in the Top 15 digest.",
        "",
    ]

    fam_pool = sorted(
        ((f, (r.get("pool", 0) if isinstance(r, dict) else 0)) for f, r in by_family.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    if fam_pool:
        out += [
            "**Pool contribution by family:** "
            + " · ".join(f"{f} {p}" for f, p in fam_pool) + ".",
            "",
        ]

    out += [
        "_Pool = contributed to the merit pool · Reached = reached ranking · "
        "Passed = cleared the role filter (post-dedup) · Pass-share = Passed ÷ Reached. "
        "Sources are ranked by shortlist, then pass._",
        "",
    ]

    ordered = sorted(
        stats.items(),
        key=lambda x: (_f(x[0], "shortlisted"), _f(x[0], "passed"), _f(x[0], "reached")),
        reverse=True,
    )

    with_ai = bool(
        (data.get("ai_enrichment") or {}).get("source_analysis")
    )
    ai_src_map = {
        s["source"]: s
        for s in (data.get("ai_enrichment") or {}).get("source_analysis", [])
    }
    if with_ai:
        out += [
            "| Source | Scraped | Pool | Reached | Passed | Shortlist | Top 15 | Pass-share | AI Quality | Recommendation |",
            "|--------|--------:|-----:|--------:|-------:|----------:|-------:|-----------:|------------|----------------|",
        ]
    else:
        out += [
            "| Source | Scraped | Pool | Reached | Passed | Shortlist | Top 15 | Pass-share |",
            "|--------|--------:|-----:|--------:|-------:|----------:|-------:|-----------:|",
        ]
    for src, scraped in ordered:
        pool, reached = _f(src, "pool"), _f(src, "reached")
        passed, shortlist = _f(src, "passed"), _f(src, "shortlisted")
        top15 = top15_by_source.get(src, 0)
        flag = " 🏆" if top15 else (" ⚠️" if scraped > 0 and passed == 0 else "")
        row = (
            f"| `{src}`{flag} | {scraped} | {pool} | {reached} | {passed} | "
            f"{shortlist} | {top15} | {_pct(passed, reached)} |"
        )
        if with_ai:
            ai_info = ai_src_map.get(src, {})
            quality = ai_info.get("quality_rating", "—")
            rec = ai_info.get("recommendation", "—")
            note = ai_info.get("note", "")
            rec_str = f"{rec} — {note}" if note else rec
            row += f" {quality}/5 | {rec_str} |"
        out.append(row)

    if dead_weight:
        preview = ", ".join(f"`{s}`" for s in dead_weight[:12])
        more = f" (+{len(dead_weight) - 12} more)" if len(dead_weight) > 12 else ""
        out += [
            "",
            f"⚠️ **Dead weight** ({len(dead_weight)} scraped but 0 passed the role filter): "
            f"{preview}{more}",
        ]

    out += [
        "",
        "_🏆 = contributed at least one role to the Top 15 · "
        "⚠️ = scraped but 0 passed the role filter._",
    ]
    return out


def format_body(data: dict) -> str:
    today = data.get("date", str(date.today()))
    jobs: list[dict] = data.get("jobs", [])
    stats: dict = data.get("source_stats", {})
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    lines: list[str] = []
    lines += [
        f"**Date:** {today}",
        f"**Sources scraped:** {len(stats)}",
        f"**Total raw listings:** {data.get('total_raw', '?')}",
        f"**After filter + dedup:** {data.get('total_deduped', '?')}",
        "**Ranking:** weighted formula (distance 35% · language 35% · IT relevance 30%)",
        f"**MCP/AI source merge:** {'✅ Yes' if data.get('extra_sources_merged') else '⚠️ No — Python sources only'}",
        f"**AI pre-rank cleanup:** {'✅ Yes' if data.get('ai_pre_rank_enriched') else '⚠️ No'}",
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
    # Rank strictly by the weighted score (distance 35%, language 35%,
    # IT relevance 30%) computed in rank_jobs.py.
    digest_jobs = sorted(
        useful_jobs,
        key=lambda j: j.get("score", 0),
        reverse=True,
    )[:15]

    # ---- Top 15 ranked roles (weighted score) ----
    def _cell(text: str) -> str:
        # Escape pipe chars so titles like "... | Remote | Europe" don't break
        # the markdown table layout.
        return str(text).replace("|", "\\|")

    lines += [
        "## 🏆 Top 15 Ranked Roles",
        "",
        "_Weighted score = distance from Vienna 35% · language 35% · IT relevance 30% (0–5 scale)._",
        "",
        "| # | Role | Company | Location | Lang | Score |",
        "|---|------|---------|----------|------|-------|",
    ]
    for i, j in enumerate(digest_jobs, 1):
        title = _cell(j.get("title", "N/A"))
        company = _cell(j.get("company", "N/A"))
        # Prefer the normalized "place, region, country" display; fall back to the
        # raw location for rows that predate normalization.
        location = _cell(j.get("display") or j.get("location") or "N/A")
        lang = (j.get("language_hint") or "").lower() or "n/a"
        score = j.get("score", "")
        url = j.get("application_url") or j.get("source_url", "")
        title_md = f"[{title}]({url})" if url else title
        lines.append(f"| {i} | {title_md} | {company} | {location} | {lang} | {score} |")
    lines.append("")

    # ---- Source performance table ----
    # Prefer the Story-8 per-source/per-family funnel (pool→reached→passed→
    # shortlisted); fall back to the legacy deduped-unique table below when a
    # pre-Story-8 jobs_ranked.json has no ranked_funnel.
    funnel_section = _source_perf_funnel_section(data, stats, jobs, digest_jobs)
    if funnel_section is not None:
        lines += funnel_section
        lines += [
            "",
            "---",
            f"*Auto-generated by [dach-exec-job-alerts](https://github.com/{repo}/actions) · {today}*",
        ]
        return "\n".join(lines)

    # ---- Source performance table (legacy fallback) ----
    # Show the true funnel per source, not just the raw scrape count:
    #   Scraped    → what the source pulled in (raw, pre-dedup)
    #   Unique     → de-duplicated jobs it contributed to the pool (the real
    #                "what was useful" signal; from rank_jobs deduped_source_stats)
    #   Shortlist  → survived into the final ranked pool (data["jobs"], top-N capped)
    #   Top 15     → made the final digest above
    # A source that scrapes a lot but contributes 0 Unique is dead weight;
    # a source with high Unique but 0 Shortlist just ranked below the cutoff.
    ai_src_map = {
        s["source"]: s
        for s in (data.get("ai_enrichment") or {}).get("source_analysis", [])
    }

    def _count_by_source(items: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for j in items:
            src = j.get("source_name", "unknown")
            counts[src] = counts.get(src, 0) + 1
        return counts

    # Prefer the ranker's deduped per-source counts; fall back to shortlist if absent.
    unique_by_source: dict = data.get("deduped_source_stats", {}) or {}
    shortlist_by_source = _count_by_source(jobs)
    top15_by_source = _count_by_source(digest_jobs)
    has_unique = bool(unique_by_source)

    active_sources = sum(1 for c in stats.values() if c > 0)
    if has_unique:
        productive_sources = sum(1 for s in stats if unique_by_source.get(s, 0) > 0)
        dead_weight = [s for s, c in stats.items() if c > 0 and unique_by_source.get(s, 0) == 0]
    else:
        productive_sources = sum(1 for s in stats if shortlist_by_source.get(s, 0) > 0)
        dead_weight = [s for s, c in stats.items() if c > 0 and shortlist_by_source.get(s, 0) == 0]
    total_scraped = sum(stats.values())
    total_unique = sum(unique_by_source.values()) if has_unique else sum(shortlist_by_source.values())

    lines += [
        "## 📊 Source Performance",
        "",
        f"**{len(stats)}** sources configured · **{active_sources}** returned listings · "
        f"**{productive_sources}** contributed unique jobs.",
        f"Funnel: **{total_scraped}** scraped → **{total_unique}** unique (after filter + dedup) "
        f"→ **{len(jobs)}** shortlisted → **{len(digest_jobs)}** in the Top 15 digest.",
        "",
        "_Unique = de-duplicated jobs the source contributed (the real usefulness signal). "
        "Shortlist = made the final ranked pool. Yield = Unique ÷ Scraped. "
        "Sources are ranked by usefulness (Unique)._",
        "",
    ]

    # Order by usefulness: Unique desc, then Shortlist desc, then Scraped desc.
    ordered = sorted(
        stats.items(),
        key=lambda x: (
            unique_by_source.get(x[0], 0),
            shortlist_by_source.get(x[0], 0),
            x[1],
        ),
        reverse=True,
    )

    def _yield_str(scraped: int, useful: int) -> str:
        if scraped <= 0:
            return "—"
        return f"{round(100 * useful / scraped)}%"

    if ai_src_map:
        lines += [
            "| Source | Scraped | Unique | Shortlist | Top 15 | Yield | AI Quality | Recommendation |",
            "|--------|--------:|-------:|----------:|-------:|------:|------------|----------------|",
        ]
        for src, scraped in ordered:
            unique = unique_by_source.get(src, 0)
            shortlist = shortlist_by_source.get(src, 0)
            top15 = top15_by_source.get(src, 0)
            ai_info = ai_src_map.get(src, {})
            quality = ai_info.get("quality_rating", "—")
            rec = ai_info.get("recommendation", "—")
            note = ai_info.get("note", "")
            rec_str = f"{rec} — {note}" if note else rec
            flag = " 🏆" if top15 else (" ⚠️" if scraped > 0 and unique == 0 else "")
            lines.append(
                f"| `{src}`{flag} | {scraped} | {unique} | {shortlist} | {top15} | "
                f"{_yield_str(scraped, unique)} | {quality}/5 | {rec_str} |"
            )
    else:
        lines += [
            "| Source | Scraped | Unique | Shortlist | Top 15 | Yield |",
            "|--------|--------:|-------:|----------:|-------:|------:|",
        ]
        for src, scraped in ordered:
            unique = unique_by_source.get(src, 0)
            shortlist = shortlist_by_source.get(src, 0)
            top15 = top15_by_source.get(src, 0)
            flag = " 🏆" if top15 else (" ⚠️" if scraped > 0 and unique == 0 else "")
            lines.append(
                f"| `{src}`{flag} | {scraped} | {unique} | {shortlist} | {top15} | "
                f"{_yield_str(scraped, unique)} |"
            )

    if dead_weight:
        preview = ", ".join(f"`{s}`" for s in dead_weight[:12])
        more = f" (+{len(dead_weight) - 12} more)" if len(dead_weight) > 12 else ""
        lines += [
            "",
            f"⚠️ **Dead weight** ({len(dead_weight)} scraped but contributed 0 unique jobs): "
            f"{preview}{more}",
        ]

    lines += [
        "",
        "_🏆 = contributed at least one role to the Top 15 · "
        "⚠️ = scraped but contributed nothing unique (all duplicates/filtered)._",
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
        print("Label not found; retrying without labels...", file=sys.stderr)
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
        title = f"[DACH Jobs] Top Exec Roles (weighted-ranked) — {today}"
        body = format_body(data)

    # Pass labels only if they were successfully created
    labels = ["job-digest"] if label_created else []
    success = create_issue(title, body, labels)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
