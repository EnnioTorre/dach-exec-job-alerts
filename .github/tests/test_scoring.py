"""
Unit tests for the shared deterministic scorer in `scoring.py`.

Focus areas:
  - Vienna-distance scoring by geolocation (origin + location geocoded)
  - language / IT-management focus scoring
  - the weighted `score_job` formula (distance 35% / language 35% /
    IT relevance 30%)
  - geocoding closure (budget, cache, HTTP path, retry + fail-loud, persistent cache)
  - formula determinism + source-blindness guards

Distance scoring is driven entirely by geolocation (no static coordinate
table). The autouse fixture keeps the suite hermetic by disabling live geocoding
and seeding the Vienna origin from the cache, mirroring the persistent cache in
production.
"""

import pytest
import scoring

# Vienna reference coordinates, used only to seed the geocode cache so the
# geolocated origin resolves offline in tests (the scorer holds no such constant).
_VIENNA_COORDS = (48.2082, 16.3738)


@pytest.fixture(autouse=True)
def _geocoding_off_by_default(monkeypatch):
    # Distance scoring geolocates both the origin and the location; keep tests
    # hermetic by disabling live geocoding and seeding the origin from cache.
    monkeypatch.setenv("RANK_GEOCODE", "false")
    monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", "")  # never touch disk in tests
    monkeypatch.setattr(scoring, "_geocode_used", 0)
    monkeypatch.setattr(scoring, "_last_geocode_ts", 0.0)
    monkeypatch.setattr(scoring, "_vienna_origin", None)
    scoring._GEOCODE_CACHE.clear()
    scoring._GEOCODE_CACHE[scoring._VIENNA_PLACE.strip().lower()] = _VIENNA_COORDS


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# vienna_distance_score
# ---------------------------------------------------------------------------

class TestViennaDistanceScore:
    @pytest.mark.parametrize(
        "location,coords,expected",
        [
            ("Vienna", (48.2082, 16.3738), 5.0),    # 0 km
            ("Graz", (47.0707, 15.4395), 4.2),       # ~145 km
            ("Munich", (48.1351, 11.5820), 3.5),     # ~356 km
            ("Zurich", (47.3769, 8.5417), 2.8),      # ~588 km
            ("Bolzano", (46.4983, 11.3548), 3.5),    # ~426 km (South Tyrol)
        ],
    )
    def test_geolocated_city_maps_to_expected_bucket(self, location, coords, expected):
        scoring._GEOCODE_CACHE[location.strip().lower()] = coords
        assert scoring.vienna_distance_score(location) == expected

    def test_empty_location_returns_neutral_default(self):
        assert scoring.vienna_distance_score("") == 2.4

    def test_placeholder_location_returns_neutral_default(self):
        assert scoring.vienna_distance_score("—, —, —") == 2.4

    def test_unresolvable_location_returns_neutral_default(self):
        # No cached geolocation + geocoding disabled → neutral.
        assert scoring.vienna_distance_score("Atlantis") == 2.4

    def test_no_static_table_city_without_geolocation_is_neutral(self):
        # "Graz" was a hardcoded table entry before the rework; with no
        # geolocation available it now degrades to neutral — proving the static
        # coordinate table is gone.
        assert scoring.vienna_distance_score("Graz") == 2.4


# ---------------------------------------------------------------------------
# language_score
# ---------------------------------------------------------------------------

class TestLanguageScore:
    def test_english_scores_high(self):
        assert scoring.language_score("en", "Acme") == 5.0

    def test_german_scores_medium(self):
        assert scoring.language_score("de", "Acme") == 3.0

    def test_other_language_scores_low(self):
        assert scoring.language_score("fr", "Acme") == 1.2

    def test_unknown_language_scores_low(self):
        assert scoring.language_score("", "Acme International GmbH") == 1.2
        assert scoring.language_score("", "Acme GmbH") == 1.2


# ---------------------------------------------------------------------------
# it_management_focus_score
# ---------------------------------------------------------------------------

class TestItManagementFocusScore:
    def test_management_plus_engineering_is_top_score(self):
        assert scoring.it_management_focus_score("Head of Software Engineering") == 5.0

    def test_management_only_is_low(self):
        assert scoring.it_management_focus_score("Head of Marketing") == 1.5

    def test_it_individual_contributor_is_demoted_below_management(self):
        # An IT IC (Cloud Engineer) is demoted to 2.5 so every management role
        # (>= 4.0) outranks it on the focus axis.
        assert scoring.it_management_focus_score("Cloud Engineer") == 2.5

    def test_generic_engineering_ic_without_it_signal(self):
        assert scoring.it_management_focus_score("Development Engineer") == 2.0

    def test_generic_engineering_management_scores_high(self):
        # Generic "Head of Engineering" (no explicit software word) is a
        # leadership role → 4.0 (IT context is enforced by the relevance gate).
        assert scoring.it_management_focus_score("Head of Engineering") == 4.0

    def test_every_management_role_outranks_every_ic_role(self):
        mgmt = [
            "Head of Software Engineering", "Engineering Manager",
            "Director of Engineering", "VP Engineering", "CTO",
            "Head of Cloud", "Program Manager - Platform",
        ]
        ic = [
            "Senior Software Engineer", "Platform Engineer", "Cloud Engineer",
            "Staff Engineer", "Principal Engineer", "DevOps Engineer",
        ]
        worst_mgmt = min(scoring.it_management_focus_score(t) for t in mgmt)
        best_ic = max(scoring.it_management_focus_score(t) for t in ic)
        assert worst_mgmt > best_ic

    def test_excluded_keyword_forces_min(self):
        assert scoring.it_management_focus_score("Sales Engineer") == 1.0

    def test_non_it_industry_forces_min(self):
        assert scoring.it_management_focus_score("Head of Electrical Engineering") == 1.0

    def test_non_it_title(self):
        assert scoring.it_management_focus_score("Office Assistant") == 1.0


# ---------------------------------------------------------------------------
# score_job — re-weighted formula (no salary term)
# ---------------------------------------------------------------------------

class TestScoreJob:
    def test_perfect_job_scores_five(self):
        # Vienna coords (5.0)*.35 + en (5.0)*.35 + Head of Software Engineering (5.0)*.30 = 5.0
        job = {"title": "Head of Software Engineering", "coords": _VIENNA_COORDS, "language_hint": "en", "company": "Acme"}
        assert scoring.score_job(job) == 5.0

    def test_score_is_clamped_to_minimum_one(self):
        job = {"title": "Office Assistant", "location": "Atlantis", "language_hint": "de", "company": "Acme"}
        assert scoring.score_job(job) >= 1.0

    def test_score_never_exceeds_five(self):
        job = {"title": "Head of Software Engineering", "coords": _VIENNA_COORDS, "language_hint": "en", "company": "Acme International"}
        assert scoring.score_job(job) <= 5.0

    def test_salary_text_is_ignored(self):
        # Salary was removed from scoring; presence/absence must not change score.
        base = {"title": "Head of Engineering", "coords": _VIENNA_COORDS, "language_hint": "en", "company": "Acme"}
        with_salary = {**base, "salary_text": "EUR 200.000"}
        assert scoring.score_job(base) == scoring.score_job(with_salary)

    def test_english_role_outranks_identical_german_role(self):
        en = {"title": "Head of Engineering", "coords": _VIENNA_COORDS, "language_hint": "en", "company": "Acme"}
        de = {"title": "Head of Engineering", "coords": _VIENNA_COORDS, "language_hint": "de", "company": "Acme"}
        assert scoring.score_job(en) > scoring.score_job(de)


# ---------------------------------------------------------------------------
# geocoding closure
# ---------------------------------------------------------------------------

class TestGeocoding:
    def test_arbitrary_city_geocoded_to_distance_bucket(self, monkeypatch):
        # Any city is resolved by geolocation; stub Vienna's own coords → 0 km → 5.0.
        monkeypatch.setattr(scoring, "_vienna_origin", _VIENNA_COORDS)
        monkeypatch.setattr(scoring, "_geocode", lambda loc: _VIENNA_COORDS)
        assert scoring.vienna_distance_score("Bregenz") == 5.0

    def test_two_cities_score_by_geolocated_distance(self, monkeypatch):
        coords = {"nearcity": (48.2082, 16.3738), "farcity": (52.5200, 13.4050)}  # Vienna vs Berlin
        monkeypatch.setattr(scoring, "_vienna_origin", _VIENNA_COORDS)
        monkeypatch.setattr(scoring, "_geocode", lambda loc: coords[loc.strip().lower()])
        near = scoring.vienna_distance_score("NearCity")
        far = scoring.vienna_distance_score("FarCity")
        assert near == 5.0 and far == 2.8   # 0 km vs ~524 km
        assert near > far

    def test_region_geolocates_to_its_own_coords(self, monkeypatch):
        # "Lower Austria" geolocates ~60 km from Vienna (St. Pölten) → bucket 4.7.
        monkeypatch.setattr(scoring, "_vienna_origin", _VIENNA_COORDS)
        monkeypatch.setattr(scoring, "_geocode", lambda loc: (48.2000, 15.6250))
        assert scoring.vienna_distance_score("Lower Austria") == 4.7

    def test_geocode_miss_degrades_to_neutral(self, monkeypatch):
        monkeypatch.setattr(scoring, "_vienna_origin", _VIENNA_COORDS)
        monkeypatch.setattr(scoring, "_geocode", lambda loc: None)
        # A legitimate miss (resolver responded, no match) → unknown → neutral 2.4.
        assert scoring.vienna_distance_score("Hintertupfing, Austria") == 2.4

    def test_geocode_outage_raises_rather_than_fabricating(self, monkeypatch):
        # A geolocation OUTAGE must fail the run, never fabricate a value (ADR-0002).
        monkeypatch.setattr(scoring, "_vienna_origin", _VIENNA_COORDS)

        def _down(loc):
            raise scoring.GeocodeUnavailable("nominatim down")

        monkeypatch.setattr(scoring, "_geocode", _down)
        with pytest.raises(scoring.GeocodeUnavailable):
            scoring.vienna_distance_score("Somewhere")

    def test_geocode_result_cached_second_call_not_refetched(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "true")
        calls: list[str] = []

        def fake_query(loc):
            calls.append(loc)
            return (48.0, 16.0)

        monkeypatch.setattr(scoring, "_geocode_query", fake_query)
        a = scoring._geocode("Somewhere")
        b = scoring._geocode("Somewhere")
        assert a == b == (48.0, 16.0)
        assert len(calls) == 1          # second call served from cache

    def test_geocode_respects_per_run_budget(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "true")
        monkeypatch.setenv("RANK_GEOCODE_MAX", "1")
        calls: list[str] = []

        def fake_query(loc):
            calls.append(loc)
            return (48.0, 16.0)

        monkeypatch.setattr(scoring, "_geocode_query", fake_query)
        first = scoring._geocode("CityA")
        second = scoring._geocode("CityB")
        assert first == (48.0, 16.0)
        assert second is None           # budget exhausted after one query
        assert calls == ["CityA"]

    def test_geocoding_disabled_degrades_to_neutral(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "false")
        # Origin is cache-seeded, but an uncached city cannot geolocate → neutral.
        assert scoring.vienna_distance_score("Bregenz") == 2.4

    # --- live Nominatim HTTP path (_geocode_query) with mocked requests ---

    def test_geocode_query_success_parses_lat_lon(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests, "get",
            lambda *a, **k: _FakeResp(200, [{"lat": "48.2082", "lon": "16.3738"}]),
        )
        assert scoring._geocode_query("Vienna") == (48.2082, 16.3738)

    def test_geocode_query_5xx_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(scoring.requests, "get", lambda *a, **k: _FakeResp(500, []))
        with pytest.raises(scoring.GeocodeUnavailable):
            scoring._geocode_query("Vienna")

    def test_geocode_query_429_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(scoring.requests, "get", lambda *a, **k: _FakeResp(429, []))
        with pytest.raises(scoring.GeocodeUnavailable):
            scoring._geocode_query("Vienna")

    def test_geocode_query_4xx_returns_none(self, monkeypatch):
        # A non-429 client error is not a transient outage → treat as no result.
        monkeypatch.setattr(scoring.requests, "get", lambda *a, **k: _FakeResp(400, []))
        assert scoring._geocode_query("Vienna") is None

    def test_geocode_query_empty_result_returns_none(self, monkeypatch):
        monkeypatch.setattr(scoring.requests, "get", lambda *a, **k: _FakeResp(200, []))
        assert scoring._geocode_query("Nowhere") is None

    def test_geocode_query_request_exception_raises_unavailable(self, monkeypatch):
        def _raise(*a, **k):
            raise scoring.requests.RequestException("boom")

        monkeypatch.setattr(scoring.requests, "get", _raise)
        with pytest.raises(scoring.GeocodeUnavailable):
            scoring._geocode_query("Vienna")

    def test_geocode_query_malformed_payload_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests, "get",
            lambda *a, **k: _FakeResp(200, [{"lat": "not-a-number", "lon": "x"}]),
        )
        with pytest.raises(scoring.GeocodeUnavailable):
            scoring._geocode_query("Vienna")

    # --- retry + fail-loud (ADR-0002) ---

    def test_geocode_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "true")
        monkeypatch.setattr(scoring.time, "sleep", lambda *_: None)
        attempts: list[str] = []

        def flaky(loc):
            attempts.append(loc)
            if len(attempts) < 2:
                raise scoring.GeocodeUnavailable("transient")
            return (48.0, 16.0)

        monkeypatch.setattr(scoring, "_geocode_query", flaky)
        assert scoring._geocode("Somewhere") == (48.0, 16.0)
        assert len(attempts) == 2            # failed once, then succeeded on retry

    def test_geocode_raises_after_exhausting_retries(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "true")
        monkeypatch.setattr(scoring.time, "sleep", lambda *_: None)
        attempts: list[str] = []

        def always_down(loc):
            attempts.append(loc)
            raise scoring.GeocodeUnavailable("down")

        monkeypatch.setattr(scoring, "_geocode_query", always_down)
        with pytest.raises(scoring.GeocodeUnavailable):
            scoring._geocode("Somewhere")
        assert len(attempts) == scoring._GEOCODE_MAX_ATTEMPTS

    def test_origin_outage_fails_the_run(self, monkeypatch):
        # Cold cache + geocoding enabled + Nominatim down → the Vienna origin
        # itself cannot resolve, so the run fails loudly rather than scoring every
        # row neutral behind the scenes (ADR-0002; closes the pass-2 cold-cache gap).
        scoring._GEOCODE_CACHE.clear()               # drop the seeded origin
        monkeypatch.setattr(scoring, "_vienna_origin", None)
        monkeypatch.setenv("RANK_GEOCODE", "true")
        monkeypatch.setattr(scoring.time, "sleep", lambda *_: None)

        def always_down(loc):
            raise scoring.GeocodeUnavailable("down")

        monkeypatch.setattr(scoring, "_geocode_query", always_down)
        row = {"title": "Head of Engineering", "coords": _VIENNA_COORDS, "language_hint": "en"}
        with pytest.raises(scoring.GeocodeUnavailable):
            scoring.score_job(row)

    # --- persistent cache (_load_geocode_cache / _save_geocode_cache) ---

    def test_geocode_cache_round_trip_persists_hits_and_misses(self, monkeypatch, tmp_path):
        path = tmp_path / "geo.json"
        monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", str(path))
        scoring._GEOCODE_CACHE.clear()
        scoring._GEOCODE_CACHE["graz"] = (47.0707, 15.4395)
        scoring._GEOCODE_CACHE["nowhere"] = None          # negative cache entry
        scoring._save_geocode_cache()

        scoring._GEOCODE_CACHE.clear()
        scoring._load_geocode_cache()
        assert scoring._GEOCODE_CACHE["graz"] == (47.0707, 15.4395)
        assert scoring._GEOCODE_CACHE["nowhere"] is None  # miss survives the round-trip

    def test_geocode_cache_round_trips_umlaut_origin_key(self, monkeypatch, tmp_path):
        # The warm-restore design hinges on the geolocated Vienna origin key
        # ("wien, österreich") surviving the ensure_ascii JSON save/load.
        path = tmp_path / "geo.json"
        monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", str(path))
        scoring._GEOCODE_CACHE.clear()
        key = scoring._VIENNA_PLACE.strip().lower()
        scoring._GEOCODE_CACHE[key] = (48.2082, 16.3738)
        scoring._save_geocode_cache()
        scoring._GEOCODE_CACHE.clear()
        scoring._load_geocode_cache()
        assert scoring._GEOCODE_CACHE[key] == (48.2082, 16.3738)

    def test_geocode_cache_load_tolerates_malformed_file(self, monkeypatch, tmp_path):
        path = tmp_path / "geo.json"
        path.write_text("{ not valid json")
        monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", str(path))
        scoring._GEOCODE_CACHE.clear()
        scoring._load_geocode_cache()                     # must not raise
        assert scoring._GEOCODE_CACHE == {}

    def test_geocode_cache_load_rejects_nonnumeric_coords(self, monkeypatch, tmp_path):
        path = tmp_path / "geo.json"
        path.write_text('{"bad": ["a", "b"], "good": [1.0, 2.0]}')
        monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", str(path))
        scoring._GEOCODE_CACHE.clear()
        scoring._load_geocode_cache()
        assert scoring._GEOCODE_CACHE["bad"] is None      # non-numeric → dropped to None
        assert scoring._GEOCODE_CACHE["good"] == (1.0, 2.0)

    def test_geocode_cache_empty_path_disables_disk_io(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", "")
        scoring._GEOCODE_CACHE.clear()
        scoring._GEOCODE_CACHE["graz"] = (47.0, 15.4)
        scoring._save_geocode_cache()                     # no-op when path is empty
        assert not (tmp_path / "geo.json").exists()
        scoring._GEOCODE_CACHE.clear()
        scoring._load_geocode_cache()                     # no-op
        assert scoring._GEOCODE_CACHE == {}


# ---------------------------------------------------------------------------
# formula guards
# ---------------------------------------------------------------------------

class TestScorerGuards:
    def test_scorer_formula_is_deterministic_given_coords(self):
        # 0.35·distance + 0.35·language + 0.30·focus, clamped/rounded — pinned for
        # representative rows whose coords are the supplied geolocation result
        # (origin is the cache-seeded Vienna point).
        cases = [
            ({"title": "Head of Software Engineering", "coords": _VIENNA_COORDS, "language_hint": "en"}, 5.0),
            ({"title": "Head of Engineering", "coords": (47.0707, 15.4395), "language_hint": "de"}, 3.7),
            ({"title": "Office Assistant", "language_hint": "fr"}, 1.6),
        ]
        for job, expected in cases:
            assert scoring.score_job(job) == expected

    def test_score_has_no_source_term(self):
        # The scorer must judge a posting on its own merits only — never on where
        # it came from — so every source competes on a level field.
        base = {"title": "Head of Engineering", "coords": _VIENNA_COORDS, "language_hint": "en"}
        baseline = scoring.score_job(base)
        for field in ("source", "url", "company", "application_url", "provider"):
            for value in ("linkedin_api", "google_proxy", "stepstone", "", "karriere.at"):
                assert scoring.score_job({**base, field: value}) == baseline
