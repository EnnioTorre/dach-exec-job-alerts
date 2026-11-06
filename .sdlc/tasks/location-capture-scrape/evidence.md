---
id: evidence-location-capture-scrape
spec: ./spec.md
status: pass
created: 2026-11-06
revision: 7dcdd896877a1c182bb71d914f9a86758fe331b4
---

# Evidence Pack: Capture a specific city at scrape time

## Verdict

**Overall:** pass — 2 of 6 standard lanes available (the other 4 are not wired into this repo).

## Checks that ran

| Check | Tool | Result | Detail |
|-------|------|--------|--------|
| Build / compile | `python3 py_compile` | pass | `scrape_jobs.py` compiles clean |
| Tests | `pytest` | pass | 225 passed, 0 failed, 0 skipped (220 pre-existing + 5 new) |

New tests (all `CT / Functional`), run explicitly and green:
- `test_parse_karriere_at_uses_card_city_when_present` → BAC-1
- `test_parse_karriere_at_falls_back_to_austria_when_no_city` → BAC-1
- `test_location_enrichment_recovers_city_for_country_only_row` → BAC-3
- `test_location_enrichment_respects_per_run_budget` → BAC-3
- `test_location_enrichment_returns_none_on_fetch_failure` → BAC-5 (fetch side)

## Not verified

| Lane | Why | Where it would be caught instead |
|------|-----|--------------------------------|
| Lint | No linter configured (no ruff/flake8/black/pylint, no config file) | Nowhere — see `init` pipeline audit |
| Coverage threshold | Project enforces no coverage gate (no pytest-cov / `fail_under`) | Nowhere |
| Dependency CVE | No scan wired (`pip-audit`/`safety`/Trivy absent; deps `pip install`'d unpinned) | Nowhere — a CVE introduced today ships |
| Secret scan | No `gitleaks`/`detect-secrets`/`trufflehog` in repo | GitHub push protection may exist at the platform (unconfirmed) |
| SAST | CI-only concern; no CodeQL/Semgrep in `.github/workflows/` | Nowhere — see `init` pipeline audit |

This matches the `init` pipeline audit: the only enforced lane in this repo is `pytest`
(path-filtered to `.github/scripts/**`, `.github/tests/**`, `pytest.ini` in
[tests.yml](../../../.github/workflows/tests.yml)).

## For the reviewer

- The enrichment runs as a post-dedup pass in `main()`; it only touches rows where
  `_is_generic_location(...)` is true, so already-specific locations (and Story 2's table
  cities) are never rewritten — worth a sanity check that `_GENERIC_LOCATIONS` is neither
  too broad (would overwrite real places) nor too narrow (would skip a country spelling).
- Network is never hit in tests: `_fetch_job_page_location` and `fetch` are monkeypatched,
  matching the suite's precedent. The real fetch path (JSON-LD `jobLocation.address` →
  visible element) is exercised only via the budget test's fake HTML — a live-page shape
  change is not covered here by design (same limitation as the existing language enrichment).
