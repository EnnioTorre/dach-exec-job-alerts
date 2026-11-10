---
id: evidence-normalize-location-core
spec: ./spec.md
status: pass
created: 2026-11-10
revision: a79be4c
---

# Evidence Pack: `normalize_location()` — geocode resolution, structured fields, nearest-to-Vienna

## Verdict

**Overall: pass** — 3 of 6 lanes available (lint, tests, secret-scan-in-CI); the rest are not wired
in this repo (see Not verified). Purely additive: `normalize_location` is unused until Story 4, so no
runtime behaviour changes at merge.

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Lint | ruff (`ruff.toml`, py312) | pass | `All checks passed!` on the two changed files |
| Build / compile | `python3 -m py_compile` | pass | `locations.py` compiles |
| Tests | pytest | pass | full suite green; `test_locations.py` = 19 passed (6 S1 + 6 S2 + 7 S3), <0.5s |
| Secret scan | gitleaks | CI-only | runs blocking in `.github/workflows/quality.yml`; no secrets introduced |

Story-3 tests (resolver mocked via `monkeypatch.setattr(locations, "_geocode_address", …)`; no HTTP):
- `test_normalize_builds_place_region_country_display` → BAC-1
- `test_normalize_uses_placeholder_for_unknown_parts` → BAC-2
- `test_normalize_multi_place_picks_closest_to_vienna` → BAC-5
- `test_normalize_two_spellings_same_place_identical_output` → BAC-7
- `test_normalize_out_of_geo_place_flagged` → BAC-6
- `test_normalize_degrades_to_placeholder_when_nothing_resolves` → BAC-9 (added in review — verifies the never-raise degrade)
- `test_normalize_prefers_in_geo_over_closer_out_of_geo` → BAC-5 (added in review)

Two S2 record-shape tests updated for the new `place` key (resolve + cache round-trip).

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| SAST | Not enabled on this repo | Nowhere — see `init` pipeline audit |
| Dependency CVE | No scan wired into the build | Nowhere automatically |
| Coverage threshold | No coverage gate configured; pytest is the only enforced lane | Not enforced |
| Secret scan (local) | gitleaks runs in CI, not run locally here | CI `quality` workflow on push |

## For the reviewer

- **Resolver extension (confirmed design):** `_geocode_address` now returns a canonical `place`
  (Nominatim `address.city/town/village/municipality/suburb`, else `PLACEHOLDER`), so `"Wien,"` and
  `"Vienna, Austria"` normalize identically (BAC-7) and a region-only string yields `—, Steiermark,
  Österreich` (BAC-2). The per-place cache carries `place` and loads old entries (without it) as
  `PLACEHOLDER` — backward-compatible. The two S2 tests asserting the record shape were updated in
  lockstep.
- **Out-of-geography is not a separate flag.** The winner keeps its real `country`/`region`; the
  caller (Story 5) derives the drop via `_is_out_of_geo` — which is how `test_normalize_out_of_geo_place_flagged`
  asserts it. The return shape stays the plan's `{place, region, country, coords, display}`.
- **Degenerate degrade:** a filter/facet list (`clean_location_parts → []`, e.g. digest #121 role #4)
  or a fully-unresolvable string returns `—, —, —` / `coords None`. Recovering the real location from
  such a row (element-scoped extraction) is Story 4, out of scope here.
- `normalize_location` is unused until Story 4 wires it into `scrape_jobs.py`, so there is no runtime
  change at merge.

**Known limitations (for Story 4, flagged by the gap-critic):** `normalize_location` geocodes every
token the cleaner keeps — including deliberately-preserved non-place tokens (`"Engineering Jobs"`) — so
a junk token Nominatim resolves in-geo could win the nearest-Vienna pick; the designed mitigation is
Story 4's element-scoped extraction (the `countrycodes` bias already drops most junk). Each call also
consumes up to `MAX_LOCATION_PARTS` of the shared geocode budget, so Story 4 must size the budget / warm
the cache when wiring over a full scrape.
