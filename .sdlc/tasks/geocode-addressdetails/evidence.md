---
id: evidence-geocode-addressdetails
spec: ./spec.md
status: pass
created: 2026-11-09
revision: bf3e3af
---

# Evidence Pack: Nominatim `addressdetails` resolver — region/country/coords per place

## Verdict

**Overall: pass** — 3 of 6 lanes available (lint, tests, secret-scan-in-CI); the rest are
not wired in this repo (see Not verified). Purely additive: `_geocode_address` is unused until
Story 3, so no runtime behaviour changes at merge.

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Lint | ruff (`ruff.toml`, py312) | pass | `All checks passed!` on the two changed files |
| Build / compile | `python3 -m py_compile` | pass | `locations.py` compiles |
| Tests | pytest | pass | full suite green; `test_locations.py` = 12 passed (6 Story-1 + 6 Story-2), <0.5s |
| Secret scan | gitleaks | CI-only | runs blocking in `.github/workflows/quality.yml`; no secrets introduced (public endpoint, no key) |

Story-2 tests (all mock `locations.requests.get`; no live Nominatim call, no real sleep):
- `test_addressdetails_resolves_region_country_coords_for_place` → BAC-3
- `test_geocode_failure_degrades_to_none` → BAC-9
- `test_location_cache_round_trip_persists_hits_and_misses` → BAC-9
- `test_addressdetails_cached_place_makes_no_second_call` → BAC-9
- `test_addressdetails_respects_per_run_budget` → BAC-9
- `test_south_tyrol_maps_to_suedtirol` → BAC-6 (added in review)

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| SAST | Not enabled on this repo | Nowhere — see `init` pipeline audit |
| Dependency CVE | No scan wired into the build (no `pip-audit`/Trivy) | Nowhere automatically |
| Coverage threshold | No coverage gate configured; pytest is the only enforced lane | Not enforced |
| Secret scan (local) | gitleaks runs in CI, not run locally here | CI `quality` workflow on push |
## For the reviewer

- **Independent geocode state + dedicated budget.** `locations.py` keeps its own `_LOCATION_CACHE`,
  counter, timestamp, and a separate `location_cache.json`. After review it also has its own
  **`LOCATION_GEOCODE_MAX`** knob (falling back to `RANK_GEOCODE_MAX`, then 50) so capping this stage
  does **not** double the ranker's Nominatim volume (the shared-knob 2× surprise the gap-critic
  raised). The `RANK_GEOCODE` on/off switch and the User-Agent remain shared for operator parity.
- **`_to_german_market` South-Tyrol override** (country `Italien` + Bolzano/Bozen/Südtirol/IT-BZ
  marker ⇒ region `Südtirol`) is now covered by `test_south_tyrol_maps_to_suedtirol`.
- `load_location_cache` / `save_location_cache` are defined + unit-tested but **wired into no entry
  point yet** — the resolver and its persistence go on the hot path in Stories 3–4; inert at merge.
- ADR-0001 now records the `addressdetails=1` extension (plan External Action Item, done in this PR).
