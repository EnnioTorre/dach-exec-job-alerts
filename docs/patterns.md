# Patterns & conventions

## File location conventions

| What | Pattern | Examples |
|---|---|---|
| Pipeline stage scripts | one script per stage, flat in `.github/scripts/` | [scrape_jobs.py](../.github/scripts/scrape_jobs.py), [rank_jobs.py](../.github/scripts/rank_jobs.py), [create_issue.py](../.github/scripts/create_issue.py) |
| Site-specific HTML parsers | `parse_<site>(html, source_name)` inside `scrape_jobs.py` | `parse_stepstone` (L1827), `parse_karriere_at` (L1875), `parse_jobs_ch` (L1921) |
| Universal feed/API parsers | `parse_<format>(...)` inside `scrape_jobs.py` | `parse_rss` (L1053), `parse_json_jobs` (L1097), `parse_linkedin_guest_api` (L1975), `parse_google_jobs` (L1356) |
| Source→parser mapping | `PARSER_MAP` dict | `scrape_jobs.py` L2044 |
| Type→router mapping | `_parse_source_content()` | `scrape_jobs.py` L2063 |
| Shared AI client | single helper module | [github_models.py](../.github/scripts/github_models.py) |
| Source registry (data) | JSON only | [.github/config/sources.json](../.github/config/sources.json) |
| Tests | `test_*.py`, mirrors script name | [.github/tests/](../.github/tests/) e.g. `test_rank_jobs.py` |
| Fixtures / shared path setup | `conftest.py` adds `scripts/` to `sys.path` | [.github/tests/conftest.py](../.github/tests/conftest.py) |
| CI / orchestration | workflow YAML | [.github/workflows/dach-exec-job-alerts.yml](../.github/workflows/dach-exec-job-alerts.yml) |

## "Adding a new feature" checklist (derived from existing code)

1. Decide: is it **data** (a scrape target) or **code** (a parser / scoring rule)?
   - Data → edit [sources.json](../.github/config/sources.json), done (plus a config test).
   - Code → add a function in the relevant stage script.
2. Keep stage boundaries: a stage reads one JSON file and writes another. Do not merge stages.
3. If AI-related, call `complete_json()` from [github_models.py](../.github/scripts/github_models.py)
   — do not re-implement the OpenAI client. Wrap the stage as `continue-on-error` in the workflow.
4. Parsers and AI stages **must degrade gracefully** — return `[]` / skip on error, never raise.
5. Add a fixture-backed test. **Never hit live sites in tests.**

## Naming conventions

| Element | Convention | Example |
|---|---|---|
| Parser functions | `parse_<source_or_format>` | `parse_stepstone`, `parse_json_jobs` |
| Private helpers | leading underscore | `_load_source_config`, `_domain_family`, `_extract_search_result_url` |
| Scoring functions | `<dimension>_score` / `score_job` | `language_score`, `vienna_distance_score`, `score_job` |
| Module-level constants | UPPER_SNAKE | `PARSER_MAP`, `BAD_URL_PATTERNS`, `_RATE_LIMIT_CONFIG`, `MAX_TOKENS` |
| Env vars | `SCRAPER_*` (scrape), `TEST_*` (validation), `RANK_*` (ranking) | `SCRAPER_GOOGLE_MODE`, `TEST_MIN_RAW`, `RANK_TARGET_COUNT` |
| Source names | `<board>_<region>_<role>_<page>` | `linkedin_at_leader_0`, `google_stepstone_de` |
| Tests | `test_<unit>` in `test_<script>.py` | `test_rank_jobs.py::test_score_job` |
| Intermediate files | `/tmp/jobs/jobs_<stage>.json` | `jobs_raw.json`, `jobs_raw_ai.json`, `jobs_ranked.json`, `jobs_enriched.json` |

## Error-handling pattern — "parsers never raise"

The core convention: on any fetch or parse error, **return an empty list and log to stdout**. The
pipeline then backfills (Arbeitnow API) or degrades. Example shape:

```python
def parse_stepstone(html, source_name):
    jobs = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select('[data-at="job-item"]'):
            ...
            jobs.append({...})
    except Exception as exc:
        print(f"parse_stepstone failed for {source_name}: {exc}")
        return []          # never propagate
    return jobs
```

- Network layer: `fetch()` (L467) applies pre-emptive rate limiting and returns `None` on error
  (including after a 429) instead of raising.
- AI layer: `complete_json()` (L19 in `github_models.py`) returns `None` when `GITHUB_TOKEN` is
  missing or the API raises; callers handle `None` and skip.
- Workflow layer: AI stages use `continue-on-error: true`; `SCRAPER_FAIL_ON_FETCH_ERROR=false`
  keeps the run alive on individual source failures.

There is **no** central exception mapper / middleware — graceful degradation is enforced locally
at each boundary.

## Language-detection convention — never trust the title

DACH boards routinely show English titles over German bodies. Language is inferred, in priority
order (`detect_job_language()` L1572):

1. explicit tag (`inLanguage`, `<html lang>`, locale) — `_normalize_lang_tag` L1523
2. HTML language markers — `_language_tag_from_html` L1531
3. body text (needs German audience marker or ≥40 chars) — `_body_language` L1560
4. audience markers `(m/w/d)`, `:in`, `:innen`, `*in`, umlauts — `_audience_marker_lang` L1547
5. bounded page/LinkedIn fetch (caps: 80 pages, 60 LinkedIn per run)
6. German-market domain default (`_GERMAN_MARKET_HOST_RE` L1821)
7. unknown (`""`) — left for the AI pre-rank step to fill; never a title-prose guess

## Rate-limiting convention — pre-emptive, not reactive

Every outbound request passes through a per-domain `TokenBucket` (L301) keyed by `_domain_family`
(L428), configured in `_RATE_LIMIT_CONFIG` (L383). Google is throttled hardest (~0.02 req/s ≈ 1
req/50s); SearXNG has an extra 5s global spacing. Prefer provider sources over direct Google.

## URL normalization convention

All scraped links pass through `_extract_search_result_url()` (L1250): unwrap Google `/url?q=`,
Bing `/ck/?u=` (base64), DuckDuckGo `?uddg=`, fix protocol-relative `//host`, and make relative
URLs absolute.

## Testing patterns

- **Where:** [.github/tests/](../.github/tests/), one `test_<script>.py` per script.
  Config: [pytest.ini](../pytest.ini) (`testpaths = .github/tests`).
- **Imports:** [conftest.py](../.github/tests/conftest.py) injects `.github/scripts` into
  `sys.path` so stage modules import directly (they are not a package).
- **Mock HTTP, never live:** parsers are tested against saved HTML/JSON fixtures. CI
  ([tests.yml](../.github/workflows/tests.yml)) runs `pytest` on changes to scripts, tests, or config.
- **What to mock:** `requests`/`fetch` for parsers, `complete_json` for AI stages, `subprocess`/`gh`
  for `create_issue.py`.
- **Install deps locally:** `pip install pytest requests beautifulsoup4 certifi langdetect`.

See [data-flows.md](data-flows.md#change-impact-map) for ordered change checklists.
