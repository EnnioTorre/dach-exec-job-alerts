# Documentation index — task routing

Find the right code fast. Pick the task closest to yours.

> **No `api-surface.md`:** this project exposes no REST endpoints and no event bus — it is a
> scheduled batch pipeline. External interfaces (job boards, search providers, GitHub Models,
> GitHub Issues) are documented in [infrastructure.md](infrastructure.md#external-services).

| I need to... | Doc | Source path |
|---|---|---|
| Add / remove a scrape source | [data-flows.md](data-flows.md#adding-a-new-scrape-source-the-most-common-change) | [.github/config/sources.json](../.github/config/sources.json) |
| Add a new source *type* / parser | [data-flows.md](data-flows.md#adding-a-new-parser-type) + [patterns.md](patterns.md#file-location-conventions) | [.github/scripts/scrape_jobs.py](../.github/scripts/scrape_jobs.py) (`PARSER_MAP` L2044, router L2063) |
| Fix how a site is parsed | [building-blocks.md](building-blocks.md#capability-parsing-source-content--job-dicts) | `scrape_jobs.py` `parse_stepstone` L1827 / `parse_karriere_at` L1875 / `parse_jobs_ch` L1921 |
| Fix wrong language on a job | [patterns.md](patterns.md#language-detection-convention--never-trust-the-title) + [scenarios.md](scenarios.md#scenario-4--correct-mislabeled-language) | `scrape_jobs.py` `detect_job_language` L1572 |
| Change the ranking score / weights | [data-flows.md](data-flows.md#changing-the-ranking-formula-or-filters) + [scenarios.md](scenarios.md#scenario-2--read-the-ranked-shortlist) | [.github/scripts/rank_jobs.py](../.github/scripts/rank_jobs.py) `score_job` L600 |
| Exclude irrelevant roles (filters) | [scenarios.md](scenarios.md#scenario-3--exclude-irrelevant-roles) | `rank_jobs.py` `EXCLUDE_KEYWORDS` L92, `is_relevant` L269 |
| Fix dedup (duplicate roles in digest) | [building-blocks.md](building-blocks.md#capability-ranking-filter--dedup--score) | `rank_jobs.py` `fingerprint` L609 |
| Change the digest format / tables | [scenarios.md](scenarios.md#scenario-2--read-the-ranked-shortlist) | [.github/scripts/create_issue.py](../.github/scripts/create_issue.py) `format_body` L143 |
| Fix issue creation / labels | [building-blocks.md](building-blocks.md#capability-publish-digest) | `create_issue.py` `create_issue` L40 |
| Tune rate limiting / fix 429s | [patterns.md](patterns.md#rate-limiting-convention--pre-emptive-not-reactive) | `scrape_jobs.py` `_RATE_LIMIT_CONFIG` L383, `TokenBucket` L301 |
| Change search provider / fallback chain | [infrastructure.md](infrastructure.md#provider-fallback-chain-google_proxy-sources) | `scrape_jobs.py` `_fetch_google_proxy_via_provider` L743 |
| Fix broken scraped URLs (redirects) | [patterns.md](patterns.md#url-normalization-convention) | `scrape_jobs.py` `_extract_search_result_url` L1250 |
| Work on an AI stage | [building-blocks.md](building-blocks.md#capability-ai-stages-optional--shared-client) | [.github/scripts/ai_pre_rank_enrich.py](../.github/scripts/ai_pre_rank_enrich.py), [ai_enrich.py](../.github/scripts/ai_enrich.py), [ai_suggest_sources.py](../.github/scripts/ai_suggest_sources.py), [github_models.py](../.github/scripts/github_models.py) |
| Change validation thresholds | [infrastructure.md](infrastructure.md#environment-configuration) | [.github/scripts/test_scraper_loop.py](../.github/scripts/test_scraper_loop.py) + workflow `TEST_MIN_*` |
| Change CI / scheduling / stages | [infrastructure.md](infrastructure.md#runtime-stages-workflow) | [.github/workflows/dach-exec-job-alerts.yml](../.github/workflows/dach-exec-job-alerts.yml) |
| Configure provider credentials | [infrastructure.md](infrastructure.md#environment-configuration) | [.env.google-providers.example](../.env.google-providers.example), [GOOGLE_PROVIDER_README.md](../GOOGLE_PROVIDER_README.md) |
| Add or run tests | [patterns.md](patterns.md#testing-patterns) | [.github/tests/](../.github/tests/), [pytest.ini](../pytest.ini) |
| Understand a data flow end-to-end | [data-flows.md](data-flows.md) | — |
| Understand the architecture / why it's staged | [architecture.md](architecture.md#architectural-style) | — |

## All docs

| Doc | Covers |
|---|---|
| [overview.md](overview.md) | Purpose, tech stack, high-level diagram, entry points |
| [architecture.md](architecture.md) | Staged-pipeline style, stage diagram, component relationships, design decisions |
| [data-flows.md](data-flows.md) | Write/read/second-pass paths + change-impact checklists |
| [patterns.md](patterns.md) | Conventions: naming, error handling, language detection, rate limiting, testing |
| [building-blocks.md](building-blocks.md) | Per-capability symbol tables with file:line anchors |
| [infrastructure.md](infrastructure.md) | External services, env config, CI stages (no DB/queue/auth) |
| [scenarios.md](scenarios.md) | Actor flows + business-rule → code-location tables |
