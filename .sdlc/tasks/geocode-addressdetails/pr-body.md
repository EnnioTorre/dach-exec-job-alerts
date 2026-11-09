## Source of truth

- **Ticket:** none — spec is the source of truth
- **Spec:** `.sdlc/tasks/geocode-addressdetails/spec.md`
- **Plan:** `.sdlc/prds/standardized-job-location/plan.md` (Story 2, revised R1 — geocode-only)

## Changes

- Add `_geocode_address(place) -> {region, country, coords} | None` to `.github/scripts/locations.py`
  — the geocode-only feature's sole region/country/coords resolver, via Nominatim `addressdetails=1`
  (`accept-language=de`, DACH `countrycodes`), degrading to `None` on any failure.
- Add `_to_german_market(address)` (German-market mapping with the South-Tyrol → `Südtirol` override),
  `_geocode_address_query`, and `load_location_cache` / `save_location_cache` (persisted per-place
  hit/miss cache at `LOCATION_CACHE_PATH`).
- Cached / budgeted (own `LOCATION_GEOCODE_MAX` knob, falls back to `RANK_GEOCODE_MAX`) / ≥1 req/s /
  fully degrading, mirroring `rank_jobs._geocode` (ADR-0001), with **independent** cache + budget +
  timestamp so capping this stage does not double the ranker's Nominatim volume.
- 6 unit tests in `.github/tests/test_locations.py` (`TestGeocodeAddressDetails`), network mocked.
- Note the `addressdetails=1` extension in `docs/adrs/ADR-0001-nominatim-geocoding.md`.

## Verification

**Evidence pack:** `.sdlc/tasks/geocode-addressdetails/evidence.md` (revision `bf3e3af`)

| Check | Result |
|---|---|
| Lint | pass (ruff, changed files) |
| Build | pass (`py_compile`) |
| Tests | 12 passed in `test_locations.py` (6 Story-1 + 6 Story-2), <0.5s; full suite green |
| Coverage | no threshold enforced |
| Dependency CVE scan | not wired |
| Secret scan | gitleaks runs blocking in CI (`quality.yml`); no secrets added |

**Not verified:** SAST (not enabled), dependency CVE (not wired), coverage threshold (none),
secret scan locally (CI-only), and a live South-Tyrol override test (logic is unit-inspectable;
real geocode results feed it in Stories 3–4).

## Deviations from the spec

None.

## Review notes

- **Stacked on PR #117** (Story 1): this branch is cut from `feat/locations-gazetteer-cleaner`, not
  `main`, because both edit `locations.py`. Merge #117 first, then rebase/merge this.
- **Dedicated budget knob** (`LOCATION_GEOCODE_MAX`, review fix): capping this stage no longer doubles
  the ranker's Nominatim volume; `RANK_GEOCODE` on/off + the User-Agent stay shared for operator parity.
- `_geocode_address` + its persistence are unused until Story 3/4 import/wire them, so there is no
  runtime change at merge (the cache is inert until then).
- ADR-0001 updated with the `addressdetails=1` extension (plan External Action Item, done here).
