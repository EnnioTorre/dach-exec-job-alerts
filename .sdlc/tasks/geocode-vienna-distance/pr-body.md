## Source of truth

- **Ticket:** none — spec is the source of truth
- **Spec:** `.sdlc/tasks/geocode-vienna-distance/spec.md`
- **Plan:** `.sdlc/prds/precise-location-ranking/plan.md`

## Changes

- Resolve non-table city/region strings to coordinates via **OpenStreetMap Nominatim** and feed them into the existing Haversine distance-from-Vienna, so ranking reflects true proximity instead of a per-country constant.
- `_DACH_LOCATION_POINTS` stays the first-checked fast-path; a **bare-country guard** keeps country-only strings (`"Austria"`, `"remote"`) on the existing fallback and never geocodes them.
- Geocoding is **on by default** (`RANK_GEOCODE=false` disables for rollback), **bounded** (`RANK_GEOCODE_MAX`), **cached**, **≥1 req/s** per the OSM usage policy, sends a descriptive `User-Agent`, uses an explicit timeout, and **fully degrades** to the country fallback on any failure.
- Activated in the workflow `Rank jobs` / `Re-rank` steps (budget + `User-Agent`). No new Python library (`requests` is already a project dependency).

## Verification

**Evidence pack:** `.sdlc/tasks/geocode-vienna-distance/evidence.md` (revision `1f1135f79aa747cdabe4f68d9f7db8be897523e1`)

| Check | Result |
|---|---|
| Lint | **not run** (no linter configured) |
| Build | pass (`py_compile` + workflow YAML parse) |
| Tests | 233 passed, 0 failed (225 prior + 8 new) |
| Coverage | no threshold enforced |
| Dependency CVE scan | **not wired** |
| Secret scan | **not wired** |

**Not verified:** lint, coverage, dependency-CVE, secret-scan, SAST — this repo wires only `pytest`. The **live Nominatim request** is stubbed in tests and runs only in the scheduled workflow.

## Deviations from the spec

None from the spec. The spec itself records two deviations from the *plan*, made by user decision: geocoding is **on by default** (not dark-launched) and **activated in the workflow in this PR** (plan scoped to `rank_jobs.py` + test only). Rate limiting is **self-contained** in `rank_jobs.py` rather than reusing the scrape limiter (no BAC impact).

## Review notes

- The live `_geocode_query` HTTP path (params, `User-Agent`, timeout, `≥1 req/s` sleep) is not unit-testable — read it directly; CI only covers the cache/budget/degradation logic around it.
- `rank_jobs.py` was offline before this PR; it now imports `requests`.
- **Nominatim project ADR still owed** (CIG `NO_COVERAGE`) — should land with/before merge since this PR activates Nominatim in production.
