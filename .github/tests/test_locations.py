"""
Unit tests for the shared location primitives in `locations.py`.

Covers the Story 1 surface:
  - clean_location_parts: trailing-separator / empty-part handling, filter-list
    rejection, and UI/junk-token stripping (BAC-4)
  - load_gazetteer: known place resolves to region/country/coords (BAC-6)
  - _is_out_of_geo: DACH + South Tyrol are in-geography, others are not (BAC-6)

All functions are pure and offline, so no mocking is needed.
"""

import locations


class TestCleanLocationParts:
    def test_cleaner_strips_trailing_separators_and_empty_parts(self):
        assert locations.clean_location_parts("Wien,") == ["Wien"]
        assert locations.clean_location_parts("Graz,, ") == ["Graz"]

    def test_cleaner_rejects_filter_list_with_many_places(self):
        filter_list = (
            "Wien, Niederösterreich, Oberösterreich, Steiermark, Tirol, "
            "Kärnten, Salzburg, Vorarlberg, Burgenland, Berlin"
        )
        assert locations.clean_location_parts(filter_list) == []

    def test_cleaner_drops_ui_junk_tokens(self):
        parts = locations.clean_location_parts("San Francisco, CASuche erweitern")
        assert all("suche erweitern" not in p.lower() for p in parts)
        assert "CA" in parts


class TestGazetteer:
    def test_gazetteer_resolves_known_place_to_region_country_coords(self):
        entry = locations.load_gazetteer()["graz"]
        assert entry["region"] == "Steiermark"
        assert entry["country"] == "Österreich"
        assert entry["lat"] == 47.0707
        assert entry["lon"] == 15.4395


class TestIsOutOfGeo:
    def test_is_out_of_geo_true_for_non_dach_country(self):
        assert locations._is_out_of_geo("USA") is True
        assert locations._is_out_of_geo("Italien") is True

    def test_is_out_of_geo_false_for_dach_and_south_tyrol(self):
        assert locations._is_out_of_geo("Österreich") is False
        assert locations._is_out_of_geo("Italien", "Südtirol") is False
