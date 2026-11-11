## Source of truth

- **Ticket:** none — spec is the source of truth
- **Spec:** `.sdlc/tasks/rank-consume-coords-drop-geo/spec.md`
- **Plan:** `.sdlc/prds/standardized-job-location/plan.md` (Story 5, revised R1 — geocode-only)

## Changes

- `rank_jobs.py`:
  - **Score from `coords`** (`_location_distance_score`) when a row has them, else the location-string
    fallback; buckets extracted to `_distance_to_score` (ladder unchanged). Two spellings → same score.
  - **Re-normalize** (`_renormalize_rows`) rows where `display != location` (AI-rewritten / `scrape_extra`),
    leaving scrape-normalized rows untouched; wired into `main()` after the filters, before dedup.
  - **Drop out-of-geo** (`_row_out_of_geo`): known foreign country only; unknown/placeholder kept.
  - Guard `_distance_from_vienna_km` to not geocode the all-placeholder location.
- `test_rank_jobs.py`: 5 tests (`TestStandardizedLocationRanking`).

## Verification

**Evidence pack:** `.sdlc/tasks/rank-consume-coords-drop-geo/evidence.md` (revision `c0a0707`)

| Check | Result |
|---|---|
| Lint | pass (ruff, changed files) |
| Build | pass (`py_compile`; imports `locations`) |
| Tests | `TestStandardizedLocationRanking` 6 passed; full suite green |
| Coverage | no threshold enforced |
| Dependency CVE scan | not wired |
| Secret scan | gitleaks runs blocking in CI (`quality.yml`); no secrets added |

**Not verified:** SAST (not enabled), dependency CVE (not wired), coverage threshold (none), secret
scan locally (CI-only), the end-to-end rank / `TEST_MIN_*` loop (CI validation stage).

## Deviations from the spec

None.

## Review notes

- **Closes the Story-4 gap-critic carry-overs:** re-normalizes AI-rewritten + `scrape_extra` rows
  (BAC-8), consumes `coords` (no scrape/rank double-geocode), and skips geocoding the placeholder.
- **BAC-6 vs BAC-9:** the drop is narrow — known foreign country only; unresolved `—` rows are kept.
  Role #9 "United Kingdom" dropped; role #4 `—, —, —` kept.
- Re-normalize trigger `display != location` preserves already-resolved rows' scores (BAC-9), verified
  by a test that raises if `normalize_location` is called on such a row.
- **Review fixes (gap-critic):** out-of-geo drop moved **before** the relevance/relaxed-fallback (a late
  drop left the shortlist short of target); `_location_distance_score` hardened against malformed `coords`
  from `jobs_raw_ai.json` (degrades to the string path, never aborts the rank); two-spellings test made
  falsifiable; added a coords-absent/malformed fallback test.
