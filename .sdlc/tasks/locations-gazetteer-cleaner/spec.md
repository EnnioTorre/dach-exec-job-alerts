---
id: spec-locations-gazetteer-cleaner
status: built
created: 2026-11-09
parent: ../../prds/standardized-job-location/plan.md
story: "Story 1: Locations module foundation — gazetteer, cleaner, geography predicate"
---

# Specification: Locations module foundation — gazetteer, cleaner, geography predicate

## Context

Story 1 of PRD [standardized-job-location](../../prds/standardized-job-location/prd.md). The digest
([#109](https://github.com/EnnioTorre/dach-exec-job-alerts/issues/109)) shows malformed, non-uniform
locations (trailing commas, filter lists like all Austrian states, UI text such as `Suche erweitern`,
out-of-geography rows like San Francisco). The feature fixes this with a shared `normalize_location()`
(built in Stories 2–3) wired into scrape/rank/digest (Stories 4–6).

**This story builds only the offline foundation** — a new shared module `.github/scripts/locations.py`
plus a bundled DACH + South-Tyrol **gazetteer** data file. It provides the pure building blocks the
later stories compose: a gazetteer loader, a string **cleaner** (junk/UI/filter-list rejection), a
Haversine helper, an out-of-geography predicate, and the placeholder constant. **Nothing imports it
yet** — `normalize_location` (Story 2) and the geocode fallback (Story 3) are added to this same
module next; scrape/rank/digest wiring is Stories 4–6.

Confirmed ground truth (read from `main`):
- `_DACH_LOCATION_POINTS` ([rank_jobs.py](../../../.github/scripts/rank_jobs.py):420) maps
  `place → (lat, lon)` for 27 DACH + South-Tyrol places — **coords only, no region/country**. The new
  gazetteer is a superset that adds `region` + `country`.
- `_haversine_km` (rank_jobs.py:587) is the distance primitive.
- `_GENERIC_LOCATIONS`/`_is_generic_location` ([scrape_jobs.py](../../../.github/scripts/scrape_jobs.py):1816)
  and `_BARE_LOCATIONS`/`_is_bare_country_location` (rank_jobs.py:467) are the existing country-word
  sets — the cleaner reuses these words to recognise/drop bare country tokens.
- `conftest.py` puts `.github/scripts` on `sys.path`, so `import locations` works in tests; the
  project already loads JSON config from `.github/config/` (see `sources.json`).

## Dependencies

- **Blocked by:** none.
- **Blocks:** `normalize-location-core` (Story 2) and everything downstream — they import this module.

Independently deployable: pure new module + data; nothing references it yet, so merging it changes no
runtime behaviour.

## Architecture Approach

```mermaid
flowchart LR
    RAW["raw location string"] --> CLEAN["clean_location_parts()"]
    CLEAN -->|strip UI/junk phrases, split,<br/>drop empties, reject >N parts| PARTS["[place candidates] or [] (filter list)"]
    GAZ[".github/config/dach_gazetteer.json"] --> LOAD["load_gazetteer()"]
    LOAD --> MAP["{place → region, country, lat, lon}"]
    subgraph locations.py
        CLEAN
        LOAD
        HAV["_haversine_km()"]
        GEO["_is_out_of_geo(country, region)"]
        PH["PLACEHOLDER = '—'"]
    end
```

Story 2 will call `clean_location_parts` + `load_gazetteer` + `_haversine_km` + `_is_out_of_geo`
inside `normalize_location`. This story only ships those parts and their tests.

## Files to Create

| File Path | Purpose | Pattern Reference |
|-----------|---------|-------------------|
| `.github/config/dach_gazetteer.json` | `place → {region, country, lat, lon}` for DACH + South Tyrol, German-market local form | `.github/config/sources.json` (project JSON config shape + load convention) |
| `.github/scripts/locations.py` | Loader + cleaner + `_haversine_km` + `_is_out_of_geo` + `PLACEHOLDER` | `.github/scripts/rank_jobs.py` (`_DACH_LOCATION_POINTS`, `_haversine_km`, `_is_bare_country_location`) |
| `.github/tests/test_locations.py` | The 6 CT tests below | `.github/tests/test_rank_jobs.py` (class-based, pure, `rank_jobs` import) |

No existing file is modified.

### Behaviour detail — `.github/config/dach_gazetteer.json`

JSON object keyed by **lowercased place name** → `{"region": str, "country": str, "lat": float, "lon": float}`,
all in **German-market local form** (`Bozen`, `Genf`, `Schweiz`; region = AT/DE Bundesland, CH Kanton,
South Tyrol → `Südtirol`). Must include, at minimum:
- Every place already in `_DACH_LOCATION_POINTS` (reuse its coords), each with its region + country
  (e.g. `graz → {Steiermark, Österreich, 47.0707, 15.4395}`, `wels → {Oberösterreich, Österreich, …}`,
  `zürich → {Zürich, Schweiz, …}`, `bozen → {Südtirol, Italien, …}`).
- Every AT Bundesland (9), DE Bundesland (16), CH Kanton (**all 26**), and `Südtirol` as
  **self-referential** entries (region name → region=itself, country, centroid/capital coords) so a
  region string resolves too. A region-only Swiss posting (e.g. `Thurgau`) must resolve a country,
  otherwise `_is_out_of_geo`'s fail-closed default would silently exclude it downstream.
- Include both spellings already in the table (`wien`/`vienna`, `münchen`/`munich`, `zürich`/`zurich`,
  `bozen`/`bolzano`, `genf`/`geneva`) mapping to the same German-market record.

### Behaviour detail — `locations.py` public surface

| Symbol | Signature | Behaviour |
|---|---|---|
| `PLACEHOLDER` | `str = "—"` | Em dash used for unknown location parts. Never matched as a place. |
| `load_gazetteer()` | `() -> dict[str, dict]` | Load the JSON, cached **per resolved path**, keyed by lowercased place. Path resolved relative to the module: `Path(__file__).parent.parent / "config" / "dach_gazetteer.json"` (override via `GAZETTEER_PATH` env, mirroring `SOURCES_CONFIG_PATH`). Per-path caching keeps the `GAZETTEER_PATH` override honoured after first load and the error path reachable. On read/parse error → return `{}` (never raise). |
| `clean_location_parts(raw)` | `(str) -> list[str]` | Produce candidate place strings: (1) strip known **UI/junk phrases** as case-insensitive substrings. The stoplist is **multi-word phrases only** (`"Suche erweitern"`, `"Mehr anzeigen"`, `"Alle Jobs"`, `"Jobs anzeigen"`, `"Zur Suche"`) — bare `"Jobs"`/`"Suche"` are deliberately **not** stripped, because they recur inside legitimate place/title text and bare-token stripping corrupts real locations; (2) split on commas, `strip()` each, drop empty/whitespace-only parts; (3) if more than `MAX_LOCATION_PARTS` (default **5**) non-empty parts remain, treat as a filter/facet list and return `[]`; (4) else return the cleaned parts. Never raises. |
| `_haversine_km(lat1, lon1, lat2, lon2)` | `(float×4) -> float` | Great-circle km. Copy the implementation from `rank_jobs._haversine_km`. |
| `_is_out_of_geo(country, region=None)` | `(str, str \| None) -> bool` | `False` (in-geography) when `country` is one of `Österreich` / `Deutschland` / `Schweiz` **or** `region == "Südtirol"` (South Tyrol, in Italy, is accepted). `True` otherwise. Case-insensitive compare. |

**Business rules carried from the PRD (make explicit, don't infer):**
- German-market local form is the one standard (`Bozen`, `Genf`, `Schweiz`), decided in refinement.
- The filter-list rejection (open decision #9) is implemented here at the **string level** only
  (UI-phrase strip + `MAX_LOCATION_PARTS` cap). The complementary **element-scoped extraction** (only
  taking the posting's own location element, not page/title text) is Story 4's scrape-side concern —
  out of scope here.
- South Tyrol is in-geography even though its country is Italy; the predicate accepts it via the
  `Südtirol` region.
- Everything is pure and offline — no network, no geocoding (that is Story 3).

## Key Patterns to Follow

| Pattern | Reference Implementation |
|---------|------------------------|
| Module-level data table of DACH places + coords | `rank_jobs.py:_DACH_LOCATION_POINTS` (420) |
| Haversine distance | `rank_jobs.py:_haversine_km` (587) |
| Country/bare-location word set + predicate | `rank_jobs.py:_BARE_LOCATIONS`/`_is_bare_country_location` (467); `scrape_jobs.py:_GENERIC_LOCATIONS`/`_is_generic_location` (1816) |
| Loading JSON config from `.github/config/` with an env override | `scrape_jobs.py` `_load_source_config` / `SOURCES_CONFIG_PATH` |
| Pure, class-grouped pytest, no mocking | `.github/tests/test_rank_jobs.py` (`TestViennaDistanceScore`) |

## Existing Code to Reuse

| Existing Asset | Location | How to Reuse | Why Not Create New |
|---------------|----------|--------------|-------------------|
| `_DACH_LOCATION_POINTS` | `rank_jobs.py:420` | Seed the gazetteer's coords (place→lat/lon) and spelling variants | Hand-curated coords already exist; don't re-derive |
| `_haversine_km` | `rank_jobs.py:587` | Copy verbatim into `locations.py` | Identical maths; a later story can de-duplicate across modules |
| `_GENERIC_LOCATIONS` / `_BARE_LOCATIONS` | `scrape_jobs.py:1816` / `rank_jobs.py:467` | Country-word list for the cleaner to recognise/drop bare country tokens | The project's agreed country-marker vocabulary |
| `_AUSTRIA_HINTS` … `_ITALY_HINTS` | `rank_jobs.py:459` | Country detection/synonyms feeding the gazetteer's country forms | Established country synonyms |

*No existing module already provides a gazetteer with region/country, a junk/filter cleaner, or an
out-of-geography predicate — these are genuinely new. `locations.py` is a new file because it is the
shared home both `scrape_jobs.py` and `rank_jobs.py` will import (neither should import the other).*

## Verification Criteria

The six plan tests (Level `CT`, Type `Functional`), plus two added during review to pin canton
coverage and the `{}`-on-error path. Pure functions — **no mocking** except a `GAZETTEER_PATH`
env override for the error-path test. `test_locations.py` is new; follows `test_rank_jobs.py`
conventions (`import locations`, class-grouped).

### Component Tests (CT)

| Test Name | ITPF Test Type | What it verifies | Risk mitigated | BAC |
|-----------|----------------|------------------|----------------|-----|
| `test_cleaner_strips_trailing_separators_and_empty_parts` | Functional | `"Wien,"` → `["Wien"]`; `"Graz,, "` → `["Graz"]` (no empty parts) | Trailing-comma/empty junk reaches ranking and the digest | BAC-4 |
| `test_cleaner_rejects_filter_list_with_many_places` | Functional | The all-Austrian-states string (10 parts) → `[]` | A filter/facet list is shown as a posting's location (digest #109) | BAC-4 |
| `test_cleaner_drops_ui_junk_tokens` | Functional | `"San Francisco, CASuche erweitern"` has `"Suche erweitern"` stripped (part becomes `"CA"`) | Page UI text taken as a location | BAC-4 |
| `test_gazetteer_resolves_known_place_to_region_country_coords` | Functional | `load_gazetteer()["graz"]` == `{region: "Steiermark", country: "Österreich", lat, lon}` | Region/country can't be completed offline | BAC-6 |
| `test_is_out_of_geo_true_for_non_dach_country` | Functional | `_is_out_of_geo("USA")` is `True`; `_is_out_of_geo("Italien")` is `True` (no Südtirol region) | Out-of-geography postings slip into the shortlist | BAC-6 |
| `test_is_out_of_geo_false_for_dach_and_south_tyrol` | Functional | `_is_out_of_geo("Österreich")` is `False`; `_is_out_of_geo("Italien", "Südtirol")` is `False` | Valid South-Tyrol postings wrongly excluded | BAC-6 |
| `test_gazetteer_resolves_every_swiss_canton` | Functional | All 26 CH Kantone resolve to a record with `country == "Schweiz"` | Region-only Swiss postings silently excluded by the fail-closed predicate | BAC-6 |
| `test_load_gazetteer_returns_empty_on_missing_file` | Functional | `GAZETTEER_PATH` → nonexistent file ⇒ `load_gazetteer() == {}` (never raises) | A bad/missing gazetteer crashes the pipeline | BAC-6 |

### System / Integration Tests (ST · SIT)
None — pure module.

### Acceptance Verification (UAT)
None at this level.

**Manual check (optional):** `python3 -c "import sys; sys.path.insert(0,'.github/scripts'); import locations; print(locations.load_gazetteer()['graz']); print(locations.clean_location_parts('Wien,'))"`.

## Blast Radius

- **Files directly modified:** none — three new files only.
- **Dependent systems affected:** none yet; Stories 2–6 will import `locations.py`. No change to
  `scrape_jobs.py` / `rank_jobs.py` / `create_issue.py` runtime behaviour in this story.
- **Regression risk:** minimal — nothing imports the new module, so merging cannot change the current
  pipeline. The only risk is an invalid gazetteer JSON; `load_gazetteer()` returns `{}` on parse error
  rather than raising, and the CI `pytest` + `quality` (ruff advisory, gitleaks) workflows run on the
  new files.

## Test Data Requirements

- **Fixtures:** none on disk — tests load the committed `dach_gazetteer.json` and pass inline strings
  to `clean_location_parts`.
- **Edge-case data:** `"Wien,"`, `"Graz,, "`, the 10-part all-Austrian-states string from digest #109,
  `"San Francisco, CASuche erweitern"`, and the geography pairs (`"Österreich"`, `"USA"`,
  `"Italien"` with/without `"Südtirol"`).

## Out of Scope

- `normalize_location()` itself (compose place/region/country/display, nearest-to-Vienna) — Story 2.
- Any geocoding / Nominatim `addressdetails` — Story 3.
- Element-scoped extraction (reading the posting's own location element vs page/title text) — Story 4.
- Wiring into scrape / rank / digest — Stories 4–6.
- De-duplicating `_haversine_km` across `locations.py` and `rank_jobs.py` — a later cleanup.
