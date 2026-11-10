---
id: evidence-scrape-normalize-pass
spec: ./spec.md
status: pass
created: 2026-11-10
revision: 162f4d5
---

# Evidence Pack: Apply normalization post-scrape (clean `jobs_raw.json`)

## Verdict

**Overall: pass** — 3 of 6 lanes available (lint, tests, secret-scan-in-CI); the rest are not wired
in this repo (see Not verified). This is the first story that changes **runtime** behaviour: it rewrites
each written row's `location` and attaches structured fields, so the next live digest shows standardized
locations (and digest #121 role #4's filter list becomes `—, —, —`).

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Lint | ruff (`ruff.toml`, py312) | pass | `All checks passed!` on the two changed `.py` files |
| Build / compile | pytest import of `scrape_jobs` | pass | imports `locations` cleanly; module loads |
| Tests | pytest | pass | full suite green; `test_scrape_jobs.py::TestNormalizeLocations` = 4 passed |
| Secret scan | gitleaks | CI-only | runs blocking in `.github/workflows/quality.yml`; no secrets introduced |

Story-4 tests (good-location cases mock `scrape_jobs.normalize_location`; filter-list / never-raises use
the real `normalize_location` on no-geocode paths with `RANK_GEOCODE=false`):
- `test_scrape_normalizes_messy_location_to_display_and_fields` → BAC-1
- `test_scrape_normalization_attaches_structured_fields` → BAC-3
- `test_scrape_normalization_rejects_filter_list_text` → BAC-4 (digest #121 role #4 string → `—, —, —`)
- `test_scrape_normalization_never_raises_on_bad_location` → BAC-4

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| SAST | Not enabled on this repo | Nowhere — see `init` pipeline audit |
| Dependency CVE | No scan wired into the build | Nowhere automatically |
| Coverage threshold | No coverage gate configured; pytest is the only enforced lane | Not enforced |
| Secret scan (local) | gitleaks runs in CI, not run locally here | CI `quality` workflow on push |
| End-to-end scrape / `TEST_MIN_*` loop | Not run here (would hit live sources) | The existing `test_scraper_loop` validation stage in CI; stats are computed pre-pass so thresholds are unaffected |
| Workflow YAML (the `actions/cache` change) | Only runs in GitHub Actions | The scheduled workflow run |

## For the reviewer

- **This rewrites `location`.** After the pass each written row's `location` is the standard `display`
  string and carries `place/region/country/coords/display`. Downstream on `main`: `rank_jobs` still
  reads `location` (now a cleaner string — geocodes it as before, no regression; Story 5 switches to
  `coords`); `create_issue` renders `location` (so the digest improves immediately; Story 6 switches to
  the explicit `display` field). `_enrich_locations` runs **before** the pass, so its recovery is kept.
- **Budget / cache.** The pass normalizes the written slice (`all_jobs[:200]`); `LOCATION_GEOCODE_MAX`
  bounds Nominatim calls, over-budget rows degrade to placeholder, and `load/save_location_cache` +
  the workflow `actions/cache` (now covering `location_cache.json`) warm the mapping across runs. On a
  cold cache the first run normalizes ~budget rows and placeholders the rest.
- **Element-scoped extraction is out of scope** (stakeholder decision): role #4's filter list is
  rejected to `—, —, —` (junk gone, BAC-4); recovering its real location via parser changes is a later
  story (PRD open-decision #9, string-level for v1).
