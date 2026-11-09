---
id: evidence-geocode-vienna-distance
spec: ./spec.md
status: pass
created: 2026-11-06
revision: ab4244b6714e82a32f05532e5c68de11d9f09f11
---

# Evidence Pack: Geocode arbitrary locations into the Vienna-distance score

## Verdict

**Overall:** pass — 2 of 6 standard lanes available (the other 4 are not wired into this repo).

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Build / compile | `python3 py_compile` | pass | `rank_jobs.py` compiles clean |
| Workflow YAML | `yaml.safe_load` | pass | `dach-exec-job-alerts.yml` parses after the env edits |
| Tests | `pytest` | pass | 242 passed, 0 failed, 0 skipped (225 prior + 17 geocoding) |

New tests (all `CT / Functional`), run explicitly and green via `pytest …::TestGeocoding`:
- `test_known_city_still_scores_from_table_without_geocoding` → BAC-6
- `test_arbitrary_city_geocoded_to_distance_bucket` → BAC-2
- `test_two_cities_same_country_score_by_distance` → BAC-2
- `test_region_only_uses_region_coords_over_country_fallback` → BAC-4
- `test_geocode_failure_falls_back_to_country_distance` → BAC-5
- `test_geocode_result_cached_second_call_not_refetched` → BAC-7
- `test_geocode_respects_per_run_budget` → BAC-2
- `test_geocoding_disabled_preserves_current_scores` → BAC-6

The 15 pre-existing `TestViennaDistanceScore` / `score_job` cases remain green unchanged, kept
hermetic by the module autouse fixture (geocoding disabled) plus the bare-country guard.

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| Lint | No linter configured (no ruff/flake8/black/pylint) | Nowhere — see `init` pipeline audit |
| Coverage threshold | No coverage gate (no pytest-cov / `fail_under`) | Nowhere |
| Dependency CVE | No scan wired (`pip-audit`/Trivy absent); `requests` now imported by the ranker too | Nowhere — a CVE in `requests` ships |
| Secret scan | No `gitleaks`/`detect-secrets` in repo | GitHub push protection may exist at the platform (unconfirmed) |
| SAST | CI-only concern; no CodeQL/Semgrep in `.github/workflows/` | Nowhere — see `init` pipeline audit |
| Live Nominatim network call | The HTTP branches of `_geocode_query` are now covered via mocked `requests` (success/non-200/empty/error/malformed); only the real outbound request runs live | First real workflow run |

## Review follow-up (PR #110)

Iteration-1 warnings addressed in `f0722ed`; iteration-2 warnings addressed in this revision:
- **Scope creep un-bundled:** `copilot-instructions.md` and `docs/infrastructure.md` (earlier
  docs-gen output) removed from this PR — only the geocoding-specific ADR remains.
- **Cross-run cache now real:** `geocode_cache.json` is restored across daily runs via `actions/cache`
  (not just uploaded as an artifact), so the persisted cache warms over time.
- **Persistence tested:** `_load_geocode_cache`/`_save_geocode_cache` covered by 4 tests — hit/miss
  round-trip, malformed-file tolerance, non-numeric-coord rejection, empty-path disable. This closes
  the BAC-7 traceability gap (the reproducibility BAC is now backed by a test of the persisted path).
- **Coord hardening:** `_load_geocode_cache` now rejects non-numeric coordinates (defence-in-depth).
- **Docs corrected:** the ADR's reproducibility wording now matches the code (reproducible *given a
  warm cache*; cold first run / budget-truncated early pass degrade to the deterministic fallback;
  the final re-rank pass builds the digest).
- `_geocode_query` live path covered by 5 mocked-HTTP tests; exceptions narrowed.
- CIG project ADR: `docs/adrs/ADR-0001-nominatim-geocoding.md`.

## For the reviewer

- The **live Nominatim request** (`_geocode_query`) is never exercised in CI — only the cache/budget/
  degradation logic around it is. Worth confirming the request shape (params, `User-Agent`, timeout)
  and the `≥1 req/s` `time.sleep` spacing by reading the code, since a unit test cannot.
- `rank_jobs.py` was previously offline; it now imports `requests`. Confirm the ranker still runs in
  any environment where `requests` might be absent (it is installed in CI and local dev).
- **Nominatim project ADR is still owed** (CIG `NO_COVERAGE`) — this PR activates Nominatim in
  production, so the ADR (public-endpoint rationale, data classification, resilience, caching) should
  land with/before merge. Deliberately not authored in this build per the spec's Out of Scope.
- This PR **deviates from the plan** by the user's decision: geocoding is on-by-default (disable flag)
  and activated in the workflow here, rather than shipped dark. Scope therefore includes the workflow.
