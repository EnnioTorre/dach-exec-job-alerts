"""
Unit tests for the shared location primitives in `locations.py`.

Covers the Story 1 surface:
  - clean_location_parts: trailing-separator / empty-part handling, filter-list
    rejection, and UI/junk-token stripping (BAC-4)
  - _is_out_of_geo: DACH + South Tyrol are in-geography, others are not (BAC-6)

All functions are pure, so no mocking is needed.
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

    def test_cleaner_preserves_bare_job_and_search_tokens(self):
        # Only multi-word UI phrases are stripped; bare "Jobs"/"Suche" are kept
        # so legitimate place/title text is never corrupted (reconciled BAC-4).
        assert locations.clean_location_parts("Engineering Jobs, Wien") == ["Engineering Jobs", "Wien"]
        assert locations.clean_location_parts("Suche, Graz") == ["Suche", "Graz"]


class TestIsOutOfGeo:
    def test_is_out_of_geo_true_for_non_dach_country(self):
        assert locations._is_out_of_geo("USA") is True
        assert locations._is_out_of_geo("Italien") is True

    def test_is_out_of_geo_false_for_dach_and_south_tyrol(self):
        assert locations._is_out_of_geo("Österreich") is False
        assert locations._is_out_of_geo("Italien", "Südtirol") is False
