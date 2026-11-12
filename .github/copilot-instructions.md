# Project Guidelines

DACH (Austria/Germany/Switzerland) executive tech-job pipeline. A scheduled GitHub
Actions workflow scrapes CTO/VP-Engineering/Head-of-Engineering listings from ~39
sources, ranks them deterministically, and publishes a daily digest as a GitHub issue.

## Architecture

Four-stage pipeline (all Python scripts live in [.github/scripts/](.github/scripts/), orchestrated by [.github/workflows/dach-exec-job-alerts.yml](.github/workflows/dach-exec-job-alerts.yml)):

1. **Scrape** — [scrape_jobs.py](.github/scripts/scrape_jobs.py) reads [.github/config/sources.json](.github/config/sources.json), dispatches each source to a parser by its `type`, writes `/tmp/jobs/jobs_raw.json`.
2. **Rank** — [rank_jobs.py](.github/scripts/rank_jobs.py) dedups + filters + scores, writes `/tmp/jobs/jobs_ranked.json`.
3. **Validate** — [test_scraper_loop.py](.github/scripts/test_scraper_loop.py) re-runs the pipeline until minimum thresholds (raw count, ranked count, active sources) are met.
4. **Publish** — [create_issue.py](.github/scripts/create_issue.py) formats the digest and opens a GitHub issue via the `gh` CLI.

Optional AI stages ([ai_pre_rank_enrich.py](.github/scripts/ai_pre_rank_enrich.py), [ai_suggest_sources.py](.github/scripts/ai_suggest_sources.py), [discover_sources_mcp.py](discover_sources_mcp.py)) use GitHub Models via [github_models.py](.github/scripts/github_models.py) and require `GITHUB_TOKEN`. They are non-blocking: the deterministic pipeline works without them.

## Source registry ([sources.json](.github/config/sources.json))

Single source of truth for scrape targets. Each source has a `type` that maps to a
parser in `scrape_jobs.py` (`PARSER_MAP`). Required fields per type:
- `html` / `json_api` → explicit `url`
- `linkedin_api` → `location`, `start`, and a `keyword_set` (a named set in `position_filter.json` `search_keyword_sets`)
- `google_proxy` / `search_proxy` → `query` (+ optional `num`); the query may carry a `{roles}` slot filled from a referenced `keyword_set`

Config is validated at load time: duplicate `name`s or unknown `type`s raise `ValueError`. When adding a source, wire any new `type` into `PARSER_MAP` in Python — never add parsing logic to the JSON.

## Build and Test

```bash
pip install pytest requests beautifulsoup4 certifi langdetect  # certifi/langdetect are soft deps
pytest                                    # tests live in .github/tests/ (see pytest.ini)
python3 .github/scripts/scrape_jobs.py    # local scrape → /tmp/jobs/jobs_raw.json
python3 .github/scripts/rank_jobs.py      # local rank → /tmp/jobs/jobs_ranked.json
```

Tests mock HTTP with fixtures — never hit live sites in tests. CI ([tests.yml](.github/workflows/tests.yml)) runs `pytest` on changes to scripts, tests, or config.

## Conventions

- **Parsers never raise.** On any fetch/parse error, return an empty list and log to stdout; the pipeline degrades gracefully (e.g. backfills from the Arbeitnow API).
- **Ranking is deterministic given the geocode cache** (no AI): `score = 0.35*location + 0.35*language + 0.30*relevance`, with a hard exclude list (sales, industrial, etc.). The location term resolves city/region strings to coordinates via OpenStreetMap Nominatim — bounded (`RANK_GEOCODE_MAX`), persistently cached across passes (`/tmp/jobs/geocode_cache.json`). On a failed Nominatim response the geocoder retries then **fails the run** rather than fabricating a stand-in — no synthetic country constant ([ADR-0002](docs/adrs/ADR-0002-no-synthetic-fallback-external-resources.md)); a legitimate miss (`200`/no match) or `RANK_GEOCODE=false` leaves the distance *unknown* (neutral). See [rank_jobs.py](.github/scripts/rank_jobs.py) and [scoring.py](.github/scripts/scoring.py).
- **Never trust job-title language.** DACH boards often show English titles over German bodies. Detect German from body text, `<html lang>`, or audience markers `(m/w/d)`, `:in`, `:innen`, `*in`, and umlauts. See `detect_job_language()` in [scrape_jobs.py](.github/scripts/scrape_jobs.py).
- **Rate limiting is pre-emptive** (token bucket per domain in `_RATE_LIMIT_CONFIG`). Google is throttled extremely hard (~0.02 req/s); prefer provider sources over direct Google.
- **URLs:** normalize relative→absolute and unwrap search-engine redirects (Google `/url?q=`, Bing `?u=`, DDG `?uddg=`).

## Provider config & pitfalls

`google_proxy` sources resolve through a provider fallback chain: SearXNG (local Docker) → Google CSE → SerpAPI → Zenserp → direct Google. Credentials go in `.env.google-providers` (git-ignored; copy from [.env.google-providers.example](.env.google-providers.example)). Key env vars — see [GOOGLE_PROVIDER_README.md](GOOGLE_PROVIDER_README.md):
- `SCRAPER_GOOGLE_MODE=provider_only` + `SCRAPER_AVOID_GOOGLE_FALLBACKS=1` to avoid direct-Google quota exhaustion.
- `SCRAPER_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt` in corporate/Zscaler proxy environments.
- `SCRAPER_FAIL_ON_FETCH_ERROR=false` keeps the run alive on individual source failures.

See [DACH_TIER2_QUICKSTART.md](DACH_TIER2_QUICKSTART.md) and [TIER2_IMPROVEMENTS.md](TIER2_IMPROVEMENTS.md) for deployment and the source-expansion history.
