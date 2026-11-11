# Scenarios

The only "user" is the **recruiter/reader** who receives the daily digest issue, plus the
**maintainer** who tunes sources and scoring. Scenarios below trace the flow through stages and
pin each business rule to code. Input-parsing details live in [patterns.md](patterns.md); the
change checklists live in [data-flows.md](data-flows.md#change-impact-map).

## Actor: Scheduler (automated daily run)

### Scenario 1 — Produce the daily digest

**Trigger:** cron `7 4 * * *` or `workflow_dispatch` in
[dach-exec-job-alerts.yml](../.github/workflows/dach-exec-job-alerts.yml).

**Flow:**
1. Start local SearXNG container (primary search provider).
2. `scrape_jobs.py main()` (L2160) loads + validates `sources.json`, fetches each source through
   the rate-limited `fetch()` / provider chain, parses to job dicts, backfills, dedups per family,
   writes `jobs_raw.json`.
3. (opt) `ai_pre_rank_enrich.py` corrects metadata → `jobs_raw_ai.json`.
4. `rank_jobs.py main()` (L637) filters, dedups, scores, diversifies, caps → `jobs_ranked.json`.
5. `test_scraper_loop.py` re-runs scrape+rank until `TEST_MIN_*` thresholds pass.
6. (opt) `ai_suggest_sources.py` → `scrape_extra.py` → re-rank; (opt) `ai_enrich.py` → `jobs_enriched.json`.
7. `create_issue.py main()` (L258) formats the digest and opens the GitHub issue.

**Business rules:**

| Rule | Code location |
|---|---|
| Duplicate source name or unknown type aborts the load | `_load_source_config` scrape_jobs.py:159 |
| One source failing must not abort the run (return `[]`) | all `parse_*` + `fetch` scrape_jobs.py:467 |
| Backfill from Arbeitnow when a source yields `< SCRAPER_MIN_PER_SOURCE` | `scrape_jobs.py main()` |
| Google throttled to ~0.02 req/s; SearXNG ≥5s apart | `_RATE_LIMIT_CONFIG` scrape_jobs.py:383 |
| Raw output capped at 200 rows | `scrape_jobs.py main()` |
| Re-run until min raw/ranked/active-source thresholds met | `test_scraper_loop.py` |

**Error handling:**

| Error | Response | Severity |
|---|---|---|
| Source fetch fails / 429 | `fetch` returns `None`; source contributes `[]`; backfill may cover | low |
| Parser exception | caught, logs to stdout, returns `[]` | low |
| SearXNG never ready | workflow logs + continues; provider chain falls through to CSE | low |
| AI stage fails / no `GITHUB_TOKEN` | `continue-on-error`; deterministic output used | low |
| Thresholds unmet after `TEST_ATTEMPTS` | `test_scraper_loop.py` exits non-zero → workflow fails | medium |

## Actor: Recruiter / reader

### Scenario 2 — Read the ranked shortlist

**Trigger:** GitHub issue created by `create_issue.py` (title `[DACH Jobs] Top Exec Roles
(weighted-ranked) — <date>`, label `job-digest`).

**Flow:** `format_body()` (create_issue.py:143) renders a header (counts + formula), a **Top-15
table** (`# | Role | Company | Location | Lang | Score`, title links to the job), and a
**source-performance table**. With the Story-8 funnel present it reports the honest per-source funnel
(`Scraped / Pool / Reached / Passed / Shortlist / Top-15 / Pass-share`) plus each family's **pool
contribution**; a source whose rows never reached ranking shows pass-share `—`, not a false 100%
(`_source_perf_funnel_section`). An older `jobs_ranked.json` with no `ranked_funnel` falls back to the
legacy `Scraped / Unique / Shortlist / Top-15 / Yield` table.

**Business rules:**

| Rule | Code location |
|---|---|
| Score = `0.35·location + 0.35·language + 0.30·IT-relevance`, clamped [1.0, 5.0] | `score_job` scoring.py |
| Location score by distance from a geolocated Vienna origin (≤25km→5.0 … >900km→1.8) | `vienna_distance_score` scoring.py |
| Language: en=5.0, de=3.0, other=1.2 | `language_score` scoring.py |
| Role fit: mgmt+IT=5.0 … non-IT=1.0 | `it_management_focus_score` scoring.py |
| English roles preferred on ties / reorder | `rank_jobs.py main()` L694 |
| Per-source cap ≤50% of list (min 10) for diversity | `rank_jobs.py main()` L713 |
| Digest shows Top 15 | `format_body` create_issue.py:185 |
| Empty run → "No Listings Retrieved" title | `create_issue.py main()` L275 |

**Error handling:**

| Error | Response | Severity |
|---|---|---|
| Label missing | `ensure_label` creates it; else retry issue w/o labels | low |
| Neither ranked nor enriched file present | `load_data` raises `FileNotFoundError` → exit 1 | high |
| `gh` not authenticated | `gh issue create` fails → exit 1 | high |

## Actor: Maintainer

### Scenario 3 — Exclude irrelevant roles

**Trigger:** tuning the filters in [rank_jobs.py](../.github/scripts/rank_jobs.py).

**Business rules:**

| Rule | Code location |
|---|---|
| Hard exclude sales/industrial terms | `excluded_areas` in [position_filter.json](../.github/config/position_filter.json) |
| Exclude non-IT engineering (mechanical/electrical/…) | `non_it_excluded` in [position_filter.json](../.github/config/position_filter.json) |
| Reject search/listing URLs & non-job titles | `BAD_URL_PATTERNS` L209 / `NON_JOB_TITLE_PATTERNS` L204 |
| Require an explicit IT signal | `_has_it_signal` rank_jobs.py:182 |
| URL must match the source platform domain | `_source_domain_ok` rank_jobs.py:239 |
| Relax filter to reach `RANK_TARGET_COUNT` | `is_relevant_relaxed` rank_jobs.py:320 |

### Scenario 4 — Correct mislabeled language

**Trigger:** DACH board shows an English title over a German body.

**Business rule:** never trust the title; infer language via the priority chain in
`detect_job_language` (scrape_jobs.py:1572) — see [patterns.md](patterns.md#language-detection-convention--never-trust-the-title).
Unknown stays `""` for the AI pre-rank step to fill.

## Log / event codes

There is **no structured `APP-NNNN` code scheme.** Observability is plain stdout logging (rate-limit
waits, parser failures, provider fallthroughs, AI skips) captured in the Actions run log, plus the
`/tmp/jobs/` artifact upload. Diagnose a thin digest by reading the per-source `stats` /
`deduped_source_stats` in `jobs_raw.json` and the source-performance table in the published issue.
