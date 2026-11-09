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
- Cached / budgeted (`RANK_GEOCODE_MAX`) / ≥1 req/s / fully degrading, mirroring
  `rank_jobs._geocode` (ADR-0001), with **independent** cache + budget + timestamp.
- 5 unit tests in `.github/tests/test_locations.py` (`TestGeocodeAddressDetails`), network mocked.

## Verification

**Evidence pack:** `.sdlc/tasks/geocode-addressdetails/evidence.md` (revision `f2cc63b`)

| Check | Result |
|---|---|
| Lint | pass (ruff, changed files; 1 import-order auto-fixed) |
| Build | pass (`py_compile`) |
| Tests | 11 passed in `test_locations.py` (6 Story-1 + 5 Story-2); full suite green |
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
- **Independent geocode state** (own cache/budget/`location_cache.json`, not shared with the ranker)
  is deliberate — flagged in the spec/evidence if a single shared budget is preferred instead.
- `_geocode_address` is unused until Story 3 imports it, so there is no runtime change at merge.
- ADR-0001 should get a one-line note about the `addressdetails=1` extension (plan External Action
  Item — doc task, lands with or after this story).
