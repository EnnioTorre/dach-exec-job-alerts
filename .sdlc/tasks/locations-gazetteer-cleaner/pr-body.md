# feat(locations): DACH gazetteer + location cleaner + geography predicate

Story 1 of PRD **standardized-job-location** — the shared foundation the rest of the
location-standardization stack builds on.

## Source of truth

- Spec: `.sdlc/tasks/locations-gazetteer-cleaner/spec.md`
- Plan: `.sdlc/prds/standardized-job-location/plan.md` (Story 1 of 6, stacked)
- PRD: `.sdlc/prds/standardized-job-location/prd.md`

## Changes

New, standalone, pure/offline module — **no existing file modified, nothing imports it
yet** (wiring happens in later stories), so there is no runtime change to the pipeline.

- `.github/scripts/locations.py`
  - `load_gazetteer() -> dict[str, dict]` — bundled DACH + South-Tyrol gazetteer, module
    cached, `GAZETTEER_PATH` override, returns `{}` on error (never raises).
  - `clean_location_parts(raw) -> list[str]` — strips UI/junk phrases
    (`Suche erweitern`, …), splits on commas, drops empties, and rejects filter/facet
    lists longer than `MAX_LOCATION_PARTS` (returns `[]`).
  - `_haversine_km(...)` and `_is_out_of_geo(country, region=None)` — geography
    primitives; South Tyrol is in-geography via its region.
- `.github/config/dach_gazetteer.json` — data: lowercased place → `{region, country,
  lat, lon}` in German-market local form; AT Bundesländer, DE Bundesländer, CH Kantone,
  major cities with spelling variants, and `Südtirol`.
- `.github/tests/test_locations.py` — 6 pure unit tests (BAC-4, BAC-6).

## Verification

| Check | Result |
|---|---|
| `python3 -m pytest .github/tests/test_locations.py -q` | 6 passed |
| `python3 -m pytest -q` (full suite) | 248 passed |
| byte-compile `locations.py` | pass |
| ruff / gitleaks | run in CI (ruff advisory, gitleaks blocking); no secrets, config-conformant |

## Deviations

None. 3 new files, well within the ≤200-line / ≤3-file small-PR rule (the gazetteer is
data; the `.sdlc/` trail is excluded from code thresholds).

## Review notes

- Spelling variants map to one record; city coords reuse `_DACH_LOCATION_POINTS` from
  `rank_jobs.py`.
- `clean_location_parts` is string-level only; element-scoped extraction is Story 4.
- `_haversine_km` is duplicated from `rank_jobs.py` for now; de-duplication is deferred.
