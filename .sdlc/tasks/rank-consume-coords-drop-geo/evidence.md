---
id: evidence-rank-consume-coords-drop-geo
spec: ./spec.md
status: pass
created: 2026-11-10
revision: 7e2c937
---

# Evidence Pack: Rank consumes coords, re-normalizes after AI, drops out-of-geography

## Verdict

**Overall: pass** — 3 of 6 lanes available (lint, tests, secret-scan-in-CI); the rest are not wired in
this repo (see Not verified). This is the **authoritative** location pass and the runtime-affecting
companion to Story 4: it standardizes AI-rewritten / `scrape_extra` rows, scores from `coords`, and drops
known out-of-geography rows from the shortlist.

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Lint | ruff (`ruff.toml`, py312) | pass | `All checks passed!` on the two changed files |
| Build / compile | `python3 -m py_compile` | pass | `rank_jobs.py` compiles; imports `locations` cleanly |
| Tests | pytest | pass | full suite green; `test_rank_jobs.py::TestStandardizedLocationRanking` = 5 passed |
| Secret scan | gitleaks | CI-only | runs blocking in `.github/workflows/quality.yml`; no secrets introduced |

Story-5 tests (`rank_jobs.normalize_location` mocked for the re-normalize cases; no HTTP):
- `test_rank_uses_row_coords_for_distance_when_present` → BAC-5
- `test_rank_drops_out_of_geography_rows_from_shortlist` → BAC-6
- `test_rank_two_spellings_same_place_same_score` → BAC-7
- `test_rank_renormalizes_ai_rewritten_location` → BAC-8
- `test_rank_preserves_existing_resolved_place_scores` → BAC-9

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| SAST | Not enabled on this repo | Nowhere — see `init` pipeline audit |
| Dependency CVE | No scan wired into the build | Nowhere automatically |
| Coverage threshold | No coverage gate configured; pytest is the only enforced lane | Not enforced |
| Secret scan (local) | gitleaks runs in CI, not run locally here | CI `quality` workflow on push |
| End-to-end rank / `TEST_MIN_*` loop | Not run here | The CI `test_scraper_loop` validation stage; out-of-geo rows were not DACH-relevant, so the ranked count is not expected to drop below thresholds |

## For the reviewer

- **Re-normalize is guarded by `display != location`.** Scrape-normalized first-pass rows
  (`display == location`) are **not** re-normalized — so their `coords`/score are preserved (BAC-9,
  pinned by `test_rank_preserves_existing_resolved_place_scores`, which raises if normalize is called).
  AI-rewritten rows (AI overwrote `location`, `display` stale) and `scrape_extra` rows (no `display`)
  are caught and re-standardized (BAC-8).
- **The drop is deliberately narrow (BAC-6 vs BAC-9).** `_row_out_of_geo` returns `True` only for a
  **known foreign** country; an empty/`—` country (unresolved, e.g. digest #121 role #4) is **kept**
  with the placeholder — unknown is not out-of-geography. Role #9 "United Kingdom" is dropped.
- **Distance now comes from `coords`** (tolerating the JSON list form) when present, via the same bucket
  ladder (`_distance_to_score`, extracted verbatim), falling back to the string path otherwise — so no
  regression for rows lacking coords and no scrape/rank double-geocode. The all-placeholder location is
  never geocoded (`_distance_from_vienna_km` guard).
