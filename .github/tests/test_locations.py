"""
Unit tests for the shared location primitives in `locations.py`.

Covers the Story 1 surface:
  - clean_location_parts: trailing-separator / empty-part handling, filter-list
    rejection, and UI/junk-token stripping (BAC-4)
  - _is_out_of_geo: DACH + South Tyrol are in-geography, others are not (BAC-6)

and the Story 2 surface:
  - _geocode_address: Nominatim addressdetails resolution, graceful degradation,
    per-place cache, and per-run budget (BAC-3, BAC-9)

Pure functions need no mocking; the geocode tests mock `locations.requests.get`.
"""

import locations
import pytest


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _reset_location_geocode_state(monkeypatch):
    # Keep the geocode tests hermetic: geocoding on, no disk, fresh budget/cache.
    monkeypatch.setenv("RANK_GEOCODE", "true")
    monkeypatch.setenv("LOCATION_CACHE_PATH", "")  # never touch disk unless a test opts in
    monkeypatch.setattr(locations, "_loc_geocode_used", 0)
    monkeypatch.setattr(locations, "_last_loc_geocode_ts", 0.0)
    locations._LOCATION_CACHE.clear()


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


class TestGeocodeAddressDetails:
    def test_addressdetails_resolves_region_country_coords_for_place(self, monkeypatch):
        payload = [{
            "lat": "47.0707", "lon": "15.4395",
            "address": {"state": "Steiermark", "country": "Österreich"},
        }]
        monkeypatch.setattr(locations.requests, "get", lambda *a, **k: _FakeResp(200, payload))
        assert locations._geocode_address("Graz") == {
            "region": "Steiermark", "country": "Österreich", "coords": (47.0707, 15.4395),
        }

    def test_geocode_failure_degrades_to_none(self, monkeypatch):
        def _raise(*a, **k):
            raise locations.requests.RequestException("boom")

        monkeypatch.setattr(locations.requests, "get", _raise)
        assert locations._geocode_address("Nowhere") is None

        monkeypatch.setattr(locations.requests, "get", lambda *a, **k: _FakeResp(500, []))
        assert locations._geocode_address("Elsewhere") is None

        monkeypatch.setattr(locations.requests, "get", lambda *a, **k: _FakeResp(200, []))
        assert locations._geocode_address("Empty") is None

    def test_location_cache_round_trip_persists_hits_and_misses(self, monkeypatch, tmp_path):
        path = tmp_path / "location_cache.json"
        monkeypatch.setenv("LOCATION_CACHE_PATH", str(path))
        locations._LOCATION_CACHE.clear()
        hit = {"region": "Steiermark", "country": "Österreich", "coords": (47.0707, 15.4395)}
        locations._LOCATION_CACHE["graz"] = hit
        locations._LOCATION_CACHE["nowhere"] = None  # negative cache entry
        locations.save_location_cache()

        locations._LOCATION_CACHE.clear()
        locations.load_location_cache()
        assert locations._LOCATION_CACHE["graz"] == hit
        assert locations._LOCATION_CACHE["nowhere"] is None  # miss survives the round-trip

    def test_addressdetails_cached_place_makes_no_second_call(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("network called for a cached place")

        cached = {"region": "Steiermark", "country": "Österreich", "coords": (47.0707, 15.4395)}
        locations._LOCATION_CACHE["graz"] = cached
        monkeypatch.setattr(locations.requests, "get", _boom)
        assert locations._geocode_address("Graz") == cached

    def test_addressdetails_respects_per_run_budget(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE_MAX", "1")
        payload = [{"lat": "48.2082", "lon": "16.3738", "address": {"state": "Wien", "country": "Österreich"}}]
        calls = {"n": 0}

        def _get(*a, **k):
            calls["n"] += 1
            return _FakeResp(200, payload)

        monkeypatch.setattr(locations.requests, "get", _get)
        first = locations._geocode_address("CityA")
        second = locations._geocode_address("CityB")
        assert first is not None
        assert second is None  # budget of 1 exhausted
        assert calls["n"] == 1  # no second network call

