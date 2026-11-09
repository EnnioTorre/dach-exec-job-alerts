---
id: evidence-geocode-addressdetails
spec: ./spec.md
status: pass
created: 2026-11-09
revision: f2cc63b
---

# Evidence Pack: Nominatim `addressdetails` resolver — region/country/coords per place

## Verdict

**Overall: pass** — 3 of 6 lanes available (lint, tests, secret-scan-in-CI); the rest are
not wired in this repo (see Not verified). Purely additive: `_geocode_address` is unused until
Story 3, so no runtime behaviour changes at merge.

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Lint | ruff (`ruff.toml`, py312) | pass | `All checks passed!` on the two changed files (1 import-order issue auto-fixed) |
| Build / compile | `python3 -m py_compile` | pass | `locations.py` compiles |
| Tests | pytest | pass | full suite green; `test_locations.py` = 11 passed (6 Story-1 + 5 Story-2) |
| Secret scan | gitleaks | CI-only | runs blocking in `.github/workflows/quality.yml`; no secrets introduced (public endpoint, no key) |

Story-2 tests (all mock `locations.requests.get`; no live Nominatim call):
- `test_addressdetails_resolves_region_country_coords_for_place` → BAC-3
- `test_geocode_failure_degrades_to_none` → BAC-9
- `test_location_cache_round_trip_persists_hits_and_misses` → BAC-9
- `test_addressdetails_cached_place_makes_no_second_call` → BAC-9
- `test_addressdetails_respects_per_run_budget` → BAC-9

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| SAST | Not enabled on this repo | Nowhere — see `init` pipeline audit |
| Dependency CVE | No scan wired into the build (no `pip-audit`/Trivy) | Nowhere automatically |
| Coverage threshold | No coverage gate configured; pytest is the only enforced lane | Not enforced |
| Secret scan (local) | gitleaks runs in CI, not run locally here | CI `quality` workflow on push |
| South-Tyrol override (live) | Only exercised by the mapping logic, not by a dedicated test in this story | Covered when Story 3/4 feed real geocode results; `_to_german_market` logic is unit-inspectable |

## For the reviewer

- **Independent geocode state is deliberate.** `locations.py` keeps its own `_LOCATION_CACHE`,
  budget counter, timestamp, and a separate `location_cache.json` — it does **not** share
  `rank_jobs._GEOCODE_CACHE` or budget. Rationale in the spec: scrape/normalize and rank run as
  separate processes, so the ≥1 req/s policy holds per-process. Worth a second opinion if you'd
  prefer a single shared cross-module budget.
- **`_to_german_market` South-Tyrol override** is the one curated mapping (country `Italien` +
  Bolzano/Bozen/Südtirol/IT-BZ marker ⇒ region `Südtirol`). It relies on `accept-language=de`
  giving German country/region names for the rest; there is no dedicated live test for it (noted
  above) — the logic is small and unit-inspectable.
- `_geocode_address` mirrors `rank_jobs._geocode` line-for-line in control flow (cache → enabled →
  budget → spacing → query → cache); the HTTP call adds only `addressdetails=1` and the `address`
  parse. ADR-0001 governs the endpoint; the `addressdetails` extension should be noted in that ADR
  (plan External Action Item — doc task, not code).
