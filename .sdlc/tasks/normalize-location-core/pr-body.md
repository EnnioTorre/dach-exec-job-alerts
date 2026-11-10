## Source of truth

- **Ticket:** none — spec is the source of truth
- **Spec:** `.sdlc/tasks/normalize-location-core/spec.md`
- **Plan:** `.sdlc/prds/standardized-job-location/plan.md` (Story 3, revised R1 — geocode-only)

## Changes

- Add `normalize_location(raw) -> {place, region, country, coords, display}` to
  `.github/scripts/locations.py` — composes `clean_location_parts` → `_geocode_address` →
  `_haversine_km` → `_is_out_of_geo` into one standard location in German-market form; picks the
  in-geo candidate nearest Vienna; degrades to `—, —, —` (never raises).
- Extend `_geocode_address` record (and the persisted per-place cache) with a canonical `place`
  from the Nominatim `address` (`_to_place`), so two spellings of the same place normalize
  identically (BAC-7) and region-only strings yield `—, Region, Country` (BAC-2). Backward-compatible
  cache (old entries load `place=—`).
- Add the `_VIENNA` reference constant.
- 5 normalize tests (`TestNormalizeLocation`) + update the 2 S2 record-shape tests for the `place` key.

## Verification

**Evidence pack:** `.sdlc/tasks/normalize-location-core/evidence.md` (revision `dab585c`)

| Check | Result |
|---|---|
| Lint | pass (ruff, changed files) |
| Build | pass (`py_compile`) |
| Tests | 17 passed in `test_locations.py` (6 S1 + 6 S2 + 5 S3); full suite green |
| Coverage | no threshold enforced |
| Dependency CVE scan | not wired |
| Secret scan | gitleaks runs blocking in CI (`quality.yml`); no secrets added |

**Not verified:** SAST (not enabled), dependency CVE (not wired), coverage threshold (none),
secret scan locally (CI-only).

## Deviations from the spec

None. (The resolver `place` extension was specified and confirmed before build.)

## Review notes

- Branches off `main` (S1+S2 merged) — a normal, non-stacked PR.
- Out-of-geography is derivable via `_is_out_of_geo`, not a separate return key — keeps the plan's
  5-key shape; Story 5 does the rank-time drop.
- `normalize_location` is unused until Story 4 wires it into the scrape pass, so no runtime change at
  merge. Element-scoped recovery of filter-list rows (digest #121 role #4) is Story 4.
