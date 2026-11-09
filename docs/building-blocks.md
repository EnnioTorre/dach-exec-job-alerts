# Building blocks

Grouped by pipeline capability. Line numbers are approximate anchors into the source files.

## Capability: Source configuration & validation

**Module:** [.github/scripts/scrape_jobs.py](../.github/scripts/scrape_jobs.py) (config section) ·
[.github/config/sources.json](../.github/config/sources.json)

| Symbol | File:line | Responsibility | Key signature | Depends on |
|---|---|---|---|---|
| `_load_source_config` | scrape_jobs.py:159 | Load + validate registry; raise `ValueError` on duplicate name / unknown type | `(path) -> (sources, fallbacks)` | `_KNOWN_SOURCE_TYPES`, `_build_source_url` |
| `_KNOWN_SOURCE_TYPES` | scrape_jobs.py:76 | Allowed `type` set: `html, rss, google_proxy, search_proxy, json_api, linkedin_api` | const set | — |
| `_build_source_url` | scrape_jobs.py:96 | Template URL from entry (linkedin_api / google_proxy) unless explicit `url` | `(entry, keyword_sets) -> url` | — |
| `sources.json` | config | Single source of truth for scrape targets + `keyword_sets` + `fallbacks` | JSON | consumed by loader |

## Capability: Fetch, rate-limit & provider fallback

**Module:** [.github/scripts/scrape_jobs.py](../.github/scripts/scrape_jobs.py) (network section)

| Symbol | File:line | Responsibility | Key signature | Depends on |
|---|---|---|---|---|
| `fetch` | scrape_jobs.py:467 | HTTP GET with pre-emptive rate limiting; returns `None` on error/429 | `(url, ...) -> str \| None` | `TokenBucket`, `_ssl_verify_option` |
| `TokenBucket` | scrape_jobs.py:301 | Pre-emptive per-domain rate limiter (prevents 429s) | `.consume(tokens) -> float` | — |
| `_RATE_LIMIT_CONFIG` | scrape_jobs.py:383 | Per-domain refill rates (google 0.02 … default 0.5) | const dict | — |
| `_domain_family` | scrape_jobs.py:428 | Canonical domain key for limiter selection | `(url) -> str` | — |
| `_fetch_google_proxy_via_provider` | scrape_jobs.py:743 | Provider chain: SearXNG → Google CSE → SerpAPI → Zenserp | `(url, name) -> results` | `_parse_searxng_results`, `_parse_google_cse_results`, `_parse_serp_provider_results` |
| `_ssl_verify_option` | scrape_jobs.py:411 | Resolve CA bundle / insecure-fallback for requests | `() -> verify` | env: `SCRAPER_CA_BUNDLE` |

## Capability: Parsing (source content → job dicts)

**Module:** [.github/scripts/scrape_jobs.py](../.github/scripts/scrape_jobs.py) (parser section)

| Symbol | File:line | Responsibility | Returns |
|---|---|---|---|
| `_parse_source_content` | scrape_jobs.py:2063 | Router: dispatch by `type` to the right parser | `list[job]` |
| `PARSER_MAP` | scrape_jobs.py:2044 | `html` source `name` → site-specific parser | dict |
| `parse_stepstone` | scrape_jobs.py:1827 | Stepstone.at/.de HTML cards | `list[job]` |
| `parse_karriere_at` | scrape_jobs.py:1875 | Karriere.at HTML cards (location → Austria) | `list[job]` |
| `parse_jobs_ch` | scrape_jobs.py:1921 | jobs.ch HTML cards (location → Switzerland) | `list[job]` |
| `parse_rss` | scrape_jobs.py:1053 | RSS 2.0 / Atom + proxy-feed redirect unwrap | `list[job]` |
| `parse_json_jobs` | scrape_jobs.py:1097 | JSON APIs (Arbeitnow backfill); DACH-filter | `list[job]` |
| `parse_linkedin_guest_api` | scrape_jobs.py:1975 | LinkedIn guest `seeMoreJobPostings` endpoint | `list[job]` |
| `parse_google_jobs` | scrape_jobs.py:1356 | SERP HTML (JSON-LD → cards → `<a>` fallback) | `list[job]` |
| `extract_jsonld_jobs` / `normalize_jsonld` | scrape_jobs.py:932 / 955 | JSON-LD `JobPosting` extraction + normalization | raw / job |
| `_extract_search_result_url` | scrape_jobs.py:1250 | Unwrap Google/Bing/DDG redirect wrappers | `(href) -> url` |

## Capability: Language detection

**Module:** [.github/scripts/scrape_jobs.py](../.github/scripts/scrape_jobs.py) (language section)

| Symbol | File:line | Responsibility |
|---|---|---|
| `detect_job_language` | scrape_jobs.py:1572 | Main entry: tag → html markers → body → audience markers → fetch → domain default → `""` |
| `_body_language` | scrape_jobs.py:1560 | Body-text detection (German marker or ≥40 chars) |
| `_audience_marker_lang` | scrape_jobs.py:1547 | High-precision German signals `(m/w/d)`, `:in`, Deutschkenntnisse |
| `_infer_language_hint` | scrape_jobs.py:1668 | Dependency-free fallback scoring (umlauts, function words, job stems) + optional `langdetect` |
| `_serp_lang_hint` | scrape_jobs.py:1831 | Language for proxy/SERP rows (never trusts title prose) |

## Capability: Ranking (filter + dedup + score)

**Module:** [.github/scripts/rank_jobs.py](../.github/scripts/rank_jobs.py)

| Symbol | File:line | Responsibility | Key signature |
|---|---|---|---|
| `main` | rank_jobs.py:637 | Orchestrate filter→dedup→score→diversify→cap→write | `() -> None` |
| `is_relevant` | rank_jobs.py:269 | Strict relevance gate (role + IT signal + domain) | `(job) -> bool` |
| `is_relevant_relaxed` | rank_jobs.py:320 | Looser gate to backfill to target count | `(job) -> bool` |
| `_hard_reject_common` | rank_jobs.py:225 | Shared hard-reject (bad URL/title/AI reject) | `(job) -> bool` |
| `_has_it_signal` | rank_jobs.py:182 | Require IT keyword in title/company | `(job) -> bool` |
| `_source_domain_ok` | rank_jobs.py:239 | URL must match source platform domain | `(job) -> bool` |
| `fingerprint` | rank_jobs.py:609 | Dedup key `title|company` (normalized) | `(job) -> str` |
| `score_job` | rank_jobs.py:600 | `0.35·loc + 0.35·lang + 0.30·relevance`, clamp [1,5] | `(job) -> float` |
| `vienna_distance_score` | rank_jobs.py:522 | Location score by Haversine distance from Vienna | `(location) -> float` |
| `language_score` | rank_jobs.py:539 | en=5.0, de=3.0, other=1.2 | `(hint, company) -> float` |
| `it_management_focus_score` | rank_jobs.py:559 | Role-fit tiers (mgmt+IT=5.0 … non-IT=1.0) | `(title) -> float` |
| `EXCLUDE_KEYWORDS` / `NON_IT_EXCLUDE_KEYWORDS` | rank_jobs.py:92 / 111 | Hard exclude lists (sales, industrial, mechanical…) | const lists |
| `BAD_URL_PATTERNS` / `NON_JOB_TITLE_PATTERNS` | rank_jobs.py:209 / 204 | Reject listing/search pages & non-job titles | const |

## Capability: Validation loop

**Module:** [.github/scripts/test_scraper_loop.py](../.github/scripts/test_scraper_loop.py)

| Symbol | File:line | Responsibility |
|---|---|---|
| `_run` | test_scraper_loop.py:~43 | Run a stage as subprocess, capture output |
| (main loop) | test_scraper_loop.py | Re-run scrape+rank up to `TEST_ATTEMPTS`, assert `TEST_MIN_*` thresholds, optional insecure-SSL retry |

## Capability: Publish digest

**Module:** [.github/scripts/create_issue.py](../.github/scripts/create_issue.py)

| Symbol | File:line | Responsibility | Key signature |
|---|---|---|---|
| `main` | create_issue.py:258 | Load data, build title/body, ensure label, create issue | `() -> None` |
| `load_data` | create_issue.py:13 | `jobs_enriched.json` preferred, else `jobs_ranked.json` | `() -> dict` |
| `format_body` | create_issue.py:143 | Digest markdown: Top-15 table + source-performance table | `(data) -> str` |
| `create_issue` | create_issue.py:40 | `gh issue create --title --body --label`, retry w/o labels | `(title, body, labels) -> bool` |
| `ensure_label` | create_issue.py:23 | Ensure `job-digest` label exists | — |

## Capability: AI stages (optional) & shared client

| Symbol | File:line | Responsibility |
|---|---|---|
| `complete_json` | [github_models.py](../.github/scripts/github_models.py):19 | One JSON-mode chat completion via GitHub Models; `None` on missing token/error |
| `ai_pre_rank_enrich.py` | module | Correct per-job language/location/salary/relevance/url-quality before ranking |
| `ai_suggest_sources.py` | module | Propose 3–5 new source URLs when results are thin |
| `ai_enrich.py` | module | Qualitative re-rank + per-source keep/boost/replace notes (always writes output) |

## Capability: Local tooling (not in the scheduled run)

| Symbol | File | Responsibility |
|---|---|---|
| `discover_sources_mcp.py` | [discover_sources_mcp.py](../discover_sources_mcp.py) | Local MCP/LLM-powered source discovery |
| `probe_searxng_queries.py` | [.github/scripts/probe_searxng_queries.py](../.github/scripts/probe_searxng_queries.py) | Measure per-query SearXNG job-URL yield to design sources |

See [api-surface — N/A](#) note in [INDEX.md](INDEX.md): this project exposes no REST/event API.
External interfaces are documented in [infrastructure.md](infrastructure.md).
