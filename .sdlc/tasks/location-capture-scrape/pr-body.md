## Source of truth

- **Ticket:** none — team-process convention file
- **Spec:** `.sdlc/tasks/location-capture-scrape/spec.md`
- **Plan:** `.sdlc/prds/precise-location-ranking/plan.md`

## Changes

- Fix `parse_karriere_at` to read the listing card's location element (the lookup `parse_stepstone`/`parse_jobs_ch` already have) instead of hardcoding `"Austria"`; fall back to `"Austria"` only when no element is present.
- Add bounded, cached job-page location enrichment (`_fetch_job_page_location`) that recovers a specific city/region for country-only rows before `jobs_raw.json` is written — mirrors the existing `_fetch_job_page_language` pattern (per-URL cache, per-run budget `SCRAPER_PAGE_LOCATION_ENRICH_MAX`, graceful degradation).
- Add `_is_generic_location` and an in-place `_enrich_locations` pass wired into `main()` after dedup; only country-only/empty rows are touched, so already-specific locations never regress.
- No change to `create_issue.py` (renders `location` verbatim) or `rank_jobs.py`.

## Verification

**Evidence pack:** `.sdlc/tasks/location-capture-scrape/evidence.md` (revision `7dcdd896877a1c182bb71d914f9a86758fe331b4`)

| Check | Result |
|---|---|
| Lint | **not run** (no linter configured) |
| Build | pass (`py_compile`) |
| Tests | 225 passed, 0 failed (220 prior + 5 new) |
| Coverage | no threshold enforced |
| Dependency CVE scan | **not wired** |
| Secret scan | **not wired** |

**Not verified:** lint, coverage, dependency-CVE, secret-scan, and SAST — this repo wires only `pytest`. Consistent with the `init` pipeline audit.

## Review notes

- Implements Story 1 (`location-capture-scrape`) of PRD `precise-location-ranking`; covers BAC-1, BAC-3, and the fetch side of BAC-5. Story 2 (`geocode-vienna-distance`) is independent and not in this PR.
- Worth a look at `_GENERIC_LOCATIONS`: it must be broad enough to catch country spellings but never match a real place (which would get overwritten). Network is mocked in all tests via `monkeypatch`, matching the suite precedent.
