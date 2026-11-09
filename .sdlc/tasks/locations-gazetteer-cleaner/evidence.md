---
id: evidence-locations-gazetteer-cleaner
spec: ./spec.md
status: built
created: 2026-11-09
revision: 48d658d
---

# Evidence: Locations module foundation — cleaner, geography predicate, Haversine

## Verdict

**Built — reworked to geocode-only.** On PR #117 the reviewer (`EnnioTorre`) rejected the
bundled gazetteer: region/country/coords will come from geocoding (Nominatim
`addressdetails`, Story 3), not a static table. `dach_gazetteer.json`, `load_gazetteer()`
and the gazetteer tests were removed. Two new files, zero existing files modified. Full
suite green. **This changes PRD Option C (gazetteer-first) → geocode-only, so the plan and
Stories 2–6 need `revise-plan`, and this PR needs a fresh `review-pr`.**

## Checks that ran

| Check | Command | Result |
|---|---|---|
| Byte-compile | `python3 -c "import py_compile; py_compile.compile('.github/scripts/locations.py', doraise=True)"` | pass |
| Story tests | `python3 -m pytest .github/tests/test_locations.py -q` | 6 passed |
| Full suite | `python3 -m pytest -q` | green |

The 6 tests map to acceptance criteria:
- `test_cleaner_strips_trailing_separators_and_empty_parts` → BAC-4
- `test_cleaner_rejects_filter_list_with_many_places` → BAC-4
- `test_cleaner_drops_ui_junk_tokens` → BAC-4
- `test_cleaner_preserves_bare_job_and_search_tokens` → BAC-4 (bare `Jobs`/`Suche` kept)
- `test_is_out_of_geo_true_for_non_dach_country` → BAC-6
- `test_is_out_of_geo_false_for_dach_and_south_tyrol` → BAC-6

## Not verified (out of this story's scope)

- **ruff lint** — not installed locally; runs advisory (`continue-on-error: true`) in
  `.github/workflows/quality.yml` on push. Module was written to the project ruff config
  (py312, line-length 120, E/F/W/I/B/UP; no unused imports).
- **gitleaks** — runs blocking in CI; no secrets introduced (data + pure Python only).
- **Coverage threshold / CVE scan** — no such gate configured in this repo; pytest is the
  only enforced lane.
- `normalize_location()` (Story 2), geocoding/`addressdetails` as the region/country/coords
  source (Story 3), element-scoped extraction and pipeline wiring (Stories 4–6), and
  de-duplicating `_haversine_km` across `locations.py` and `rank_jobs.py` (later) are out of scope.

## For the reviewer

- `locations.py` is a **new standalone module**; nothing imports it yet, so there is no
  runtime behaviour change in this PR. It is the pure foundation the next stories build on.
- Surface: `PLACEHOLDER`, `clean_location_parts()`, `_haversine_km()` (copied from
  `rank_jobs.py`), `_is_out_of_geo(country, region)`. **No gazetteer** — region/country/coords
  come from geocoding (Story 3).
- `clean_location_parts()` does the **string-level** half of the filter-list rejection
  (UI-strip + `MAX_LOCATION_PARTS` cap). The element-scoped half is Story 4.
- `_is_out_of_geo()` treats South Tyrol as in-geography via its `region`, even though its
  `country` is `Italien`. The `country`/`region` it judges are produced by geocoding downstream.
