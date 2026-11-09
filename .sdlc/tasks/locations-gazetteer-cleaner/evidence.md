---
id: evidence-locations-gazetteer-cleaner
spec: ./spec.md
status: built
created: 2026-11-09
revision: 4750184
---

# Evidence: Locations module foundation — gazetteer, cleaner, geography predicate

## Verdict

**Built** (revised after review of PR #117). Three new files, zero existing files
modified. Full suite green. BAC-6 now covers **all 26 Swiss cantons** (a `review-pr`
blocker) and the gazetteer `{}`-on-error path is pinned by a test. The cleaner stoplist is
intentionally multi-word-only (bare `Jobs`/`Suche` would corrupt real place text) —
reconciled in the spec in this same PR per `shared.md`.

## Checks that ran

| Check | Command | Result |
|---|---|---|
| Byte-compile | `python3 -c "import py_compile; py_compile.compile('.github/scripts/locations.py', doraise=True)"` | pass |
| Story tests | `python3 -m pytest .github/tests/test_locations.py -q` | 8 passed |
| Full suite | `python3 -m pytest -q` | 250 passed |

The 8 tests map to acceptance criteria:
- `test_cleaner_strips_trailing_separators_and_empty_parts` → BAC-4
- `test_cleaner_rejects_filter_list_with_many_places` → BAC-4
- `test_cleaner_drops_ui_junk_tokens` → BAC-4
- `test_gazetteer_resolves_known_place_to_region_country_coords` → BAC-6
- `test_gazetteer_resolves_every_swiss_canton` → BAC-6 (all 26 CH Kantone resolve)
- `test_load_gazetteer_returns_empty_on_missing_file` → BAC-6 (graceful degradation)
- `test_is_out_of_geo_true_for_non_dach_country` → BAC-6
- `test_is_out_of_geo_false_for_dach_and_south_tyrol` → BAC-6

## Not verified (out of this story's scope)

- **ruff lint** — not installed locally; runs advisory (`continue-on-error: true`) in
  `.github/workflows/quality.yml` on push. Module was written to the project ruff config
  (py312, line-length 120, E/F/W/I/B/UP; no unused imports).
- **gitleaks** — runs blocking in CI; no secrets introduced (data + pure Python only).
- **Coverage threshold / CVE scan** — no such gate configured in this repo; pytest is the
  only enforced lane.
- `normalize_location()` (Story 2), geocoding/`addressdetails` (Story 3), element-scoped
  extraction and pipeline wiring (Stories 4–6), and de-duplicating `_haversine_km` across
  `locations.py` and `rank_jobs.py` (later) are intentionally out of scope.

## For the reviewer

- `locations.py` is a **new standalone module**; nothing imports it yet, so there is no
  runtime behaviour change to the pipeline in this PR. It is the foundation the next
  stacked stories build on.
- The gazetteer is **data** (`.github/config/dach_gazetteer.json`): lowercased place →
  `{region, country, lat, lon}` in German-market local form (Bozen/Genf/Schweiz, South
  Tyrol as `Südtirol`). Coordinates for overlapping cities are carried over from
  `_DACH_LOCATION_POINTS` in `rank_jobs.py`. Both spelling variants (vienna/wien,
  münchen/munich, zürich/zurich, bozen/bolzano, genf/geneva) map to the same record.
- `clean_location_parts()` does the **string-level** half of the filter-list rejection
  (UI-strip + `MAX_LOCATION_PARTS` cap). The element-scoped half (reading the posting's own
  location element) is Story 4, as the spec notes.
- `_is_out_of_geo()` treats South Tyrol as in-geography via its `region`, even though its
  `country` is `Italien`.
