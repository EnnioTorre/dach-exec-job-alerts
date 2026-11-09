# feat(locations): location cleaner + geography predicate (geocode-only foundation)

Story 1 of PRD **standardized-job-location** — the shared, pure foundation the rest of the
location-standardization stack builds on. **Geocode-only** (no gazetteer): region/country/coords
are resolved by Nominatim `addressdetails` in Stories 2–3, not a static table.

## Source of truth

- Spec: `.sdlc/tasks/locations-gazetteer-cleaner/spec.md`
- Plan: `.sdlc/prds/standardized-job-location/plan.md` (Story 1 of 6, stacked; revised R1 — geocode-only)
- PRD: `.sdlc/prds/standardized-job-location/prd.md`

## Changes

New, standalone, pure module — **no existing file modified, nothing imports it yet** (wiring
happens in later stories), so there is no runtime change to the pipeline.

- `.github/scripts/locations.py`
  - `clean_location_parts(raw) -> list[str]` — strips multi-word UI/junk phrases
    (`Suche erweitern`, …), splits on commas, drops empties, and rejects filter/facet
    lists longer than `MAX_LOCATION_PARTS` (returns `[]`). Never raises. Bare `Jobs`/`Suche`
    are deliberately kept.
  - `_haversine_km(...)` — great-circle km, copied from `rank_jobs.py` (used by Story 3).
  - `_is_out_of_geo(country, region=None)` — geography predicate; South Tyrol is in-geography
    via its `region`. The `country`/`region` it judges come from geocoding downstream.
  - `PLACEHOLDER = "—"`.
- `.github/tests/test_locations.py` — 6 pure unit tests (4 cleaner → BAC-4, 2 geography → BAC-6).

## Verification

| Check | Result |
|---|---|
| `python3 -m pytest .github/tests/test_locations.py -q` | 6 passed |
| `python3 -m pytest -q` (full suite) | green |
| byte-compile `locations.py` | pass |
| ruff / gitleaks | run in CI (ruff advisory, gitleaks blocking); no secrets, config-conformant |

## Deviations

- **Dropped the bundled gazetteer** (reviewer decision on this PR): `dach_gazetteer.json`,
  `load_gazetteer()`, and the 3 gazetteer tests were removed; region/country/coords move to
  geocoding (Story 3). The plan was revised accordingly (R1). 2 code files, within the
  ≤200-line / ≤3-file small-PR rule (the `.sdlc/` trail is excluded from code thresholds).

## Review notes

- `clean_location_parts` is string-level only; element-scoped extraction is Story 4.
- `_haversine_km` is copied from `rank_jobs.py`; de-duplication across modules is deferred.
- `normalize_location` (S3) and the `_geocode_address` resolver (S2) are intentionally **not** here.
