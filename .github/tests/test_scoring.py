"""
Unit tests for the shared deterministic scorer in `scoring.py`.

Focus areas:
  - Vienna-distance scoring buckets (table, geocode, fallback)
  - language / IT-management focus scoring
  - the re-weighted `score_job` formula (distance 35% / language 35% /
    IT relevance 30% — salary was removed)
  - geocoding closure (budget, cache, HTTP path, persistent cache)
  - behaviour-preservation + source-blindness guards for the extraction

The scorer is pure and side-effect free apart from the geocoding closure, which
is kept hermetic by the autouse fixture below.
"""

import pytest
import scoring


@pytest.fixture(autouse=True)
def _geocoding_off_by_default(monkeypatch):
    # The scoring tests assert the offline table/fallback behaviour; keep them
    # hermetic. Geocoding tests re-enable it and stub the network explicitly.
    monkeypatch.setenv("RANK_GEOCODE", "false")
    monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", "")  # never touch disk in tests
    monkeypatch.setattr(scoring, "_geocode_used", 0)
    monkeypatch.setattr(scoring, "_last_geocode_ts", 0.0)
    scoring._GEOCODE_CACHE.clear()


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
        "location,expected",
        [
            ("Wien, Österreich", 5.0),   # 0 km
            ("Vienna", 5.0),
            ("Graz", 4.2),               # ~145 km
            ("Munich", 3.5),             # ~356 km
            ("Zurich", 2.8),             # ~588 km
            ("Bolzano", 3.5),            # ~426 km (South Tyrol)
            ("Bozen, Südtirol", 3.5),
        ],
    )
    def test_known_cities_map_to_expected_buckets(self, location, expected):
        assert scoring.vienna_distance_score(location) == expected

    def test_empty_location_returns_neutral_default(self):
        assert scoring.vienna_distance_score("") == 2.4

    def test_unknown_location_returns_neutral_default(self):
        assert scoring.vienna_distance_score("Atlantis") == 2.4

    def test_remote_uses_dach_fallback_distance(self):
        # "remote" → dach fallback (500 km) → 2.8 bucket
        assert scoring.vienna_distance_score("Remote (DACH)") == 2.8

    def test_country_hint_only_austria(self):
        # ".at" / "österreich" without a city → 280 km fallback → 3.5 bucket
        assert scoring.vienna_distance_score("Österreich") == 3.5

    def test_italy_hint_only_uses_fallback_distance(self):
        # "italy" / "south tyrol" without a city → 400 km fallback → 3.5 bucket
        assert scoring.vienna_distance_score("South Tyrol, Italy") == 3.5


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
        # Vienna (5.0)*.35 + en (5.0)*.35 + Head of Software Engineering (5.0)*.30 = 5.0
        job = {"title": "Head of Software Engineering", "location": "Wien", "language_hint": "en", "company": "Acme"}
        assert scoring.score_job(job) == 5.0

    def test_score_is_clamped_to_minimum_one(self):
        job = {"title": "Office Assistant", "location": "Atlantis", "language_hint": "de", "company": "Acme"}
        assert scoring.score_job(job) >= 1.0

    def test_score_never_exceeds_five(self):
        job = {"title": "Head of Software Engineering", "location": "Vienna", "language_hint": "en", "company": "Acme International"}
        assert scoring.score_job(job) <= 5.0

    def test_salary_text_is_ignored(self):
        # Salary was removed from scoring; presence/absence must not change score.
        base = {"title": "Head of Engineering", "location": "Wien", "language_hint": "en", "company": "Acme"}
        with_salary = {**base, "salary_text": "EUR 200.000"}
        assert scoring.score_job(base) == scoring.score_job(with_salary)

    def test_english_role_outranks_identical_german_role(self):
        en = {"title": "Head of Engineering", "location": "Wien", "language_hint": "en", "company": "Acme"}
        de = {"title": "Head of Engineering", "location": "Wien", "language_hint": "de", "company": "Acme"}
        assert scoring.score_job(en) > scoring.score_job(de)


# ---------------------------------------------------------------------------
# geocoding closure
# ---------------------------------------------------------------------------

class TestGeocoding:
    def test_known_city_still_scores_from_table_without_geocoding(self, monkeypatch):
        def _boom(loc):
            raise AssertionError("geocode must not be called for a table city")

        monkeypatch.setattr(scoring, "_geocode", _boom)
        assert scoring.vienna_distance_score("Graz") == 4.2

    def test_arbitrary_city_geocoded_to_distance_bucket(self, monkeypatch):
        # Bregenz is not in the table; stub Vienna's own coords → 0 km → 5.0.
        monkeypatch.setattr(scoring, "_geocode", lambda loc: (48.2082, 16.3738))
        assert scoring.vienna_distance_score("Bregenz") == 5.0

    def test_two_cities_same_country_score_by_distance(self, monkeypatch):
        coords = {"nearcity": (48.2082, 16.3738), "farcity": (52.5200, 13.4050)}  # Vienna vs Berlin
        monkeypatch.setattr(scoring, "_geocode", lambda loc: coords[loc.strip().lower()])
        near = scoring.vienna_distance_score("NearCity")
        far = scoring.vienna_distance_score("FarCity")
        assert near == 5.0 and far == 2.8   # 0 km vs ~524 km
        assert near > far

    def test_region_only_uses_region_coords_over_country_fallback(self, monkeypatch):
        # "Lower Austria" geocodes ~60 km from Vienna (St. Pölten) → bucket 4.7,
        # distinct from the Austria country fallback (280 km → 3.5).
        monkeypatch.setattr(scoring, "_geocode", lambda loc: (48.2000, 15.6250))
        assert scoring.vienna_distance_score("Lower Austria") == 4.7

    def test_geocode_failure_falls_back_to_country_distance(self, monkeypatch):
        monkeypatch.setattr(scoring, "_geocode", lambda loc: None)
        # Not bare, geocode fails → Austria hint → 280 km → 3.5.
        assert scoring.vienna_distance_score("Hintertupfing, Austria") == 3.5

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

    def test_geocoding_disabled_preserves_current_scores(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "false")
        # A would-be-geocoded city falls back exactly as today: unknown → 2.4.
        assert scoring.vienna_distance_score("Bregenz") == 2.4

    # --- live Nominatim HTTP path (_geocode_query) with mocked requests ---

    def test_geocode_query_success_parses_lat_lon(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests, "get",
            lambda *a, **k: _FakeResp(200, [{"lat": "48.2082", "lon": "16.3738"}]),
        )
        assert scoring._geocode_query("Vienna") == (48.2082, 16.3738)

    def test_geocode_query_non_200_returns_none(self, monkeypatch):
        monkeypatch.setattr(scoring.requests, "get", lambda *a, **k: _FakeResp(500, []))
        assert scoring._geocode_query("Vienna") is None

    def test_geocode_query_empty_result_returns_none(self, monkeypatch):
        monkeypatch.setattr(scoring.requests, "get", lambda *a, **k: _FakeResp(200, []))
        assert scoring._geocode_query("Nowhere") is None

    def test_geocode_query_request_exception_returns_none(self, monkeypatch):
        def _raise(*a, **k):
            raise scoring.requests.RequestException("boom")

        monkeypatch.setattr(scoring.requests, "get", _raise)
        assert scoring._geocode_query("Vienna") is None

    def test_geocode_query_malformed_payload_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            scoring.requests, "get",
            lambda *a, **k: _FakeResp(200, [{"lat": "not-a-number", "lon": "x"}]),
        )
        assert scoring._geocode_query("Vienna") is None

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
# extraction guards (new for the shared-scorer module split)
# ---------------------------------------------------------------------------

class TestExtractionBehaviourPreserved:
    def test_extracted_scorer_matches_prior_scores(self):
        # Golden scores computed from the pre-extraction formula
        # (0.35·distance + 0.35·language + 0.30·focus, clamped/rounded). Moving
        # the scorer into `scoring` must not change any of these.
        cases = [
            ({"title": "Head of Software Engineering", "location": "Wien", "language_hint": "en"}, 5.0),
            ({"title": "Head of Engineering", "location": "Graz", "language_hint": "de"}, 3.7),
            ({"title": "Office Assistant", "location": "Atlantis", "language_hint": "fr"}, 1.6),
        ]
        for job, expected in cases:
            assert scoring.score_job(job) == expected

    def test_score_has_no_source_term(self):
        # The scorer must judge a posting on its own merits only — never on where
        # it came from — so every source competes on a level field.
        base = {"title": "Head of Engineering", "location": "Wien", "language_hint": "en"}
        baseline = scoring.score_job(base)
        for field in ("source", "url", "company", "application_url", "provider"):
            for value in ("linkedin_api", "google_proxy", "stepstone", "", "karriere.at"):
                assert scoring.score_job({**base, field: value}) == baseline
