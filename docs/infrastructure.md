# Infrastructure & external interfaces

This is a stateless batch pipeline. There is **no database, no message queue, no authentication
layer, and no IBM middleware**. All state is ephemeral JSON in `/tmp/jobs/` during a run; the only
persisted output is a GitHub issue. This file documents the external services the pipeline talks
to, the configuration surface, and the (absent) infra categories for completeness.

## Database

**None.** Intermediate state is held in files under `/tmp/jobs/`:

| File | Written by | Consumed by |
|---|---|---|
| `jobs_raw.json` | scrape_jobs.py / scrape_extra.py | ai_pre_rank_enrich.py, rank_jobs.py |
| `jobs_raw_ai.json` | ai_pre_rank_enrich.py | rank_jobs.py (preferred input) |
| `jobs_ranked.json` | rank_jobs.py | ai_suggest_sources.py, ai_enrich.py, create_issue.py |
| `extra_sources.json` | ai_suggest_sources.py | scrape_extra.py |
| `jobs_enriched.json` | ai_enrich.py | create_issue.py (preferred input) |

Workflow uploads `/tmp/jobs/` as a build artifact (`job-data-<run_id>`, 7-day retention) for debugging.

## Authentication

| Context | Mechanism | Config location |
|---|---|---|
| GitHub Models (AI stages) | `GITHUB_TOKEN` (no separate secret) | workflow `env:` → read by `github_models.py` |
| GitHub Issues (publish) | `GITHUB_TOKEN` via `gh` CLI | workflow `env:` on the Create-issue step |
| Local Atlassian/CIG MCP (dev only) | personal tokens via headers | [.mcp.json](../.mcp.json) — `CONFLUENCE_PERSONAL_TOKEN`, `JIRA_PERSONAL_TOKEN` |

No OIDC/OAuth2/mTLS, no role-based access. The pipeline only *reads* public job pages and *writes*
one issue to its own repo.

## External services

| Service | Type | Purpose | Config / env |
|---|---|---|---|
| Job boards (Stepstone, Karriere.at, jobs.ch, LinkedIn guest, Arbeitnow) | HTTP scrape / JSON API | Source listings | [sources.json](../.github/config/sources.json) |
| SearXNG | HTTP (local Docker) | Primary search provider for `google_proxy` | `SCRAPER_SEARXNG_URL`, `SCRAPER_SEARXNG_KEY` |
| Google Custom Search (CSE) | REST | Fallback search provider | `SCRAPER_GOOGLE_CSE_KEY`, `SCRAPER_GOOGLE_CSE_CX` |
| SerpAPI | REST | Fallback search provider | `SCRAPER_SERPAPI_KEY` |
| Zenserp | REST | Fallback search provider | `SCRAPER_ZENSERP_KEY` |
| Direct Google | HTTP scrape | Last-resort provider (throttled hard) | gated by `SCRAPER_GOOGLE_MODE` |
| GitHub Models | REST (OpenAI-compatible) | Optional AI enrich/suggest | `GITHUB_TOKEN`; base `models.inference.ai.azure.com` |
| OpenStreetMap Nominatim | REST | Geocode city/region strings → coordinates for distance ranking | `RANK_GEOCODE` (default on), `RANK_GEOCODE_MAX`, `RANK_GEOCODE_USER_AGENT`; cached in `/tmp/jobs/geocode_cache.json` |
| GitHub Issues | `gh` CLI | Publish daily digest | `GITHUB_TOKEN` |

### Provider fallback chain (`google_proxy` sources)

```
SearXNG (local) → Google CSE → SerpAPI → Zenserp → Direct Google
```

Resolved in `_fetch_google_proxy_via_provider()` (scrape_jobs.py:743). Prefer providers over direct
Google to avoid quota exhaustion. See [GOOGLE_PROVIDER_README.md](../GOOGLE_PROVIDER_README.md).

## Messaging

**None.** No Kafka/IBM MQ/RabbitMQ/Pulsar/SQS. The "second pass" is a file-merge, not a queue.

## Environment configuration

Credentials for local provider runs go in `.env.google-providers` (git-ignored; copy from
[.env.google-providers.example](../.env.google-providers.example)). Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `SCRAPER_GOOGLE_MODE` | `provider_only` | `provider_only` / `hybrid` / `direct` / `off` |
| `SCRAPER_AVOID_GOOGLE_FALLBACKS` | `true` | Skip Google URLs in fallback lists (avoid indirect 429s) |
| `SCRAPER_SKIP_SEARCH_PROXIES` | `true` | Skip unreliable Bing/DDG/Ecosia proxies |
| `SCRAPER_FAIL_ON_FETCH_ERROR` | `true` (CI sets `false`) | Keep run alive on individual source failures |
| `SCRAPER_CA_BUNDLE` | — | CA bundle path for corporate/Zscaler proxy (e.g. `/etc/pki/tls/certs/ca-bundle.crt`) |
| `SCRAPER_SEARXNG_URL` / `_KEY` | — | SearXNG endpoint / optional auth |
| `SCRAPER_GOOGLE_CSE_KEY` / `_CX` | — | Google CSE credentials |
| `SCRAPER_SERPAPI_KEY` / `SCRAPER_ZENSERP_KEY` | — | Managed provider keys |
| `SCRAPER_MIN_PER_SOURCE` | `2` | Min jobs/source before Arbeitnow backfill |
| `SCRAPER_ENRICH_MAX` | `2` | Max career-page enrichments for Google results |
| `RANK_TARGET_COUNT` | `20` | Target ranked pool size |
| `RANK_LANGUAGE_ONLY` | — | Optional language gate (`en`/`de`) |
| `TEST_MIN_RAW` / `TEST_MIN_RANKED` / `TEST_MIN_ACTIVE_SOURCES` / `TEST_ATTEMPTS` | 10 / 10 / 2 / 3 (CI) | Validation-loop thresholds |

### CI secrets (GitHub Actions)

Set as repository secrets, injected in [dach-exec-job-alerts.yml](../.github/workflows/dach-exec-job-alerts.yml):
`SCRAPER_SERPAPI_KEY`, `SCRAPER_ZENSERP_KEY`, `SCRAPER_GOOGLE_CSE_KEY`, `SCRAPER_GOOGLE_CSE_CX`.
`GITHUB_TOKEN` is provided automatically by Actions.

### Runtime stages (workflow)

CA setup → start SearXNG (Docker) → scrape → AI pre-rank (opt) → rank → validate loop →
MCP suggest (no-op) → AI suggest (opt) → scrape extra (opt) → re-rank → AI enrich (opt) →
create issue → upload artifact → stop SearXNG.

See [DACH_TIER2_QUICKSTART.md](../DACH_TIER2_QUICKSTART.md) and
[TIER2_IMPROVEMENTS.md](../TIER2_IMPROVEMENTS.md) for deployment history and source-expansion notes.
