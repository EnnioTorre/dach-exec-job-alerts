"""
Unit tests for the deterministic ranking logic in `rank_jobs.py`.

Focus areas:
  - relevance gating (`is_relevant`, `is_relevant_relaxed`)
  - Vienna-distance scoring buckets
  - language / IT-management focus scoring
  - the re-weighted `score_job` formula (distance 35% / language 35% /
    IT relevance 30% — salary was removed)
  - dedup fingerprinting

These functions are pure and side-effect free, so no mocking is needed.
"""

import pytest

import rank_jobs


@pytest.fixture(autouse=True)
def _geocoding_off_by_default(monkeypatch):
    # The ranking tests assert the offline table/fallback behaviour; keep them
    # hermetic. Geocoding tests re-enable it and stub the network explicitly.
    monkeypatch.setenv("RANK_GEOCODE", "false")
    monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", "")  # never touch disk in tests
    monkeypatch.setattr(rank_jobs, "_geocode_used", 0)
    monkeypatch.setattr(rank_jobs, "_last_geocode_ts", 0.0)
    rank_jobs._GEOCODE_CACHE.clear()


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# is_relevant
# ---------------------------------------------------------------------------

class TestIsRelevant:
    def test_accepts_clear_leadership_role_with_job_url(self):
        job = {"title": "Head of Software Engineering", "application_url": "https://acme.com/jobs/123"}
        assert rank_jobs.is_relevant(job) is True

    def test_rejects_generic_engineering_without_it_signal(self):
        # Generic "Head of Engineering" at a non-IT-named company could be a
        # mechanical/aerospace role — reject unless an IT signal is present.
        job = {"title": "Head of Engineering", "application_url": "https://acme.com/jobs/123", "company": "ENPULSION"}
        assert rank_jobs.is_relevant(job) is False

    def test_accepts_generic_engineering_when_company_has_it_signal(self):
        job = {"title": "Head of Engineering", "application_url": "https://acme.com/jobs/123", "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant(job) is True

    def test_rejects_non_it_engineering_discipline(self):
        for title in ("Head of Civil Engineering", "Head of Electrical Engineering",
                      "Head of Manufacturing Engineering", "Head of Production"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123", "company": "Acme Software"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_accepts_cto_role(self):
        job = {"title": "Chief Technology Officer (CTO)", "application_url": "https://acme.com/careers/cto-1"}
        assert rank_jobs.is_relevant(job) is True

    def test_rejects_excluded_sales_role(self):
        job = {"title": "Senior Sales Manager", "application_url": "https://acme.com/jobs/9"}
        assert rank_jobs.is_relevant(job) is False

    def test_rejects_when_ai_marks_reject(self):
        job = {
            "title": "Head of Engineering",
            "application_url": "https://acme.com/jobs/123",
            "ai_relevance": "reject",
        }
        assert rank_jobs.is_relevant(job) is False

    def test_rejects_non_job_informational_title(self):
        job = {"title": "What is a CTO? Salary guide", "application_url": "https://acme.com/jobs/1"}
        assert rank_jobs.is_relevant(job) is False

    def test_rejects_search_engine_result_url(self):
        job = {"title": "Head of Engineering", "application_url": "https://www.google.com/search?q=cto"}
        assert rank_jobs.is_relevant(job) is False

    def test_rejects_karriere_listing_page_without_numeric_id(self):
        job = {"title": "Head of Engineering", "application_url": "https://www.karriere.at/jobs/head-of-engineering"}
        assert rank_jobs.is_relevant(job) is False

    def test_accepts_karriere_concrete_job_with_numeric_id(self):
        job = {"title": "Head of Software Engineering", "application_url": "https://www.karriere.at/jobs/7821533"}
        assert rank_jobs.is_relevant(job) is True

    def test_missing_title_and_url_is_not_relevant(self):
        assert rank_jobs.is_relevant({}) is False

    def test_strict_excludes_individual_contributor_roles(self):
        # IC titles must NOT pass the strict gate — they only enter via the
        # relaxed fallback. The digest targets management/leadership roles.
        for title in ("Senior Software Engineer", "Platform Engineer",
                      "Staff Software Engineer", "Cloud Engineer",
                      "Senior Full Stack Developer"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_strict_accepts_management_it_roles(self):
        for title in ("Engineering Manager", "Director of Software Engineering",
                      "VP of Engineering", "Head of Platform"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is True, title


class TestIsRelevantRelaxed:
    def test_relaxed_accepts_mgmt_plus_domain_without_url_hint(self):
        job = {"title": "Director of Cloud", "source_url": "https://acme.io/x"}
        assert rank_jobs.is_relevant_relaxed(job) is True

    def test_relaxed_accepts_individual_contributor_as_fallback(self):
        # ICs excluded from the strict gate are still allowed in the relaxed
        # fallback (used to fill the list when leadership roles are too few).
        job = {"title": "Senior Software Engineer", "source_url": "https://acme.io/jobs/1",
               "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant_relaxed(job) is True

    def test_relaxed_still_rejects_excluded_keyword(self):
        job = {"title": "Head of Sales Engineering", "source_url": "https://acme.io/jobs/1"}
        assert rank_jobs.is_relevant_relaxed(job) is False

    def test_relaxed_rejects_non_it_engineering_discipline(self):
        job = {"title": "Head of Mechanical Engineering", "source_url": "https://acme.io/jobs/1", "company": "Acme Software"}
        assert rank_jobs.is_relevant_relaxed(job) is False

    def test_relaxed_requires_it_signal(self):
        job = {"title": "Director of Engineering", "source_url": "https://acme.io/x", "company": "Rosewood Hotel"}
        assert rank_jobs.is_relevant_relaxed(job) is False


# ---------------------------------------------------------------------------
# _has_it_signal
# ---------------------------------------------------------------------------

class TestHasItSignal:
    def test_signal_in_title(self):
        assert rank_jobs._has_it_signal({"title": "Head of Cloud Operations", "company": "Acme"}) is True

    def test_signal_in_company(self):
        assert rank_jobs._has_it_signal({"title": "Head of Engineering", "company": "efsta IT Services GmbH"}) is True

    def test_cto_counts_as_signal(self):
        assert rank_jobs._has_it_signal({"title": "Chief Technology Officer (CTO)", "company": "Acme"}) is True

    def test_no_signal(self):
        assert rank_jobs._has_it_signal({"title": "Head of Engineering", "company": "ENPULSION"}) is False


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
        assert rank_jobs.vienna_distance_score(location) == expected

    def test_empty_location_returns_neutral_default(self):
        assert rank_jobs.vienna_distance_score("") == 2.4

    def test_unknown_location_returns_neutral_default(self):
        assert rank_jobs.vienna_distance_score("Atlantis") == 2.4

    def test_remote_uses_dach_fallback_distance(self):
        # "remote" → dach fallback (500 km) → 2.8 bucket
        assert rank_jobs.vienna_distance_score("Remote (DACH)") == 2.8

    def test_country_hint_only_austria(self):
        # ".at" / "österreich" without a city → 280 km fallback → 3.5 bucket
        assert rank_jobs.vienna_distance_score("Österreich") == 3.5

    def test_italy_hint_only_uses_fallback_distance(self):
        # "italy" / "south tyrol" without a city → 400 km fallback → 3.5 bucket
        assert rank_jobs.vienna_distance_score("South Tyrol, Italy") == 3.5


# ---------------------------------------------------------------------------
# language_score
# ---------------------------------------------------------------------------

class TestLanguageScore:
    def test_english_scores_high(self):
        assert rank_jobs.language_score("en", "Acme") == 5.0

    def test_german_scores_medium(self):
        assert rank_jobs.language_score("de", "Acme") == 3.0

    def test_other_language_scores_low(self):
        assert rank_jobs.language_score("fr", "Acme") == 1.2

    def test_unknown_language_scores_low(self):
        assert rank_jobs.language_score("", "Acme International GmbH") == 1.2
        assert rank_jobs.language_score("", "Acme GmbH") == 1.2


# ---------------------------------------------------------------------------
# it_management_focus_score
# ---------------------------------------------------------------------------

class TestItManagementFocusScore:
    def test_management_plus_engineering_is_top_score(self):
        assert rank_jobs.it_management_focus_score("Head of Software Engineering") == 5.0

    def test_management_only_is_low(self):
        assert rank_jobs.it_management_focus_score("Head of Marketing") == 1.5

    def test_it_individual_contributor_is_demoted_below_management(self):
        # An IT IC (Cloud Engineer) is demoted to 2.5 so every management role
        # (>= 4.0) outranks it on the focus axis.
        assert rank_jobs.it_management_focus_score("Cloud Engineer") == 2.5

    def test_generic_engineering_ic_without_it_signal(self):
        assert rank_jobs.it_management_focus_score("Development Engineer") == 2.0

    def test_generic_engineering_management_scores_high(self):
        # Generic "Head of Engineering" (no explicit software word) is a
        # leadership role → 4.0 (IT context is enforced by the relevance gate).
        assert rank_jobs.it_management_focus_score("Head of Engineering") == 4.0

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
        worst_mgmt = min(rank_jobs.it_management_focus_score(t) for t in mgmt)
        best_ic = max(rank_jobs.it_management_focus_score(t) for t in ic)
        assert worst_mgmt > best_ic

    def test_excluded_keyword_forces_min(self):
        assert rank_jobs.it_management_focus_score("Sales Engineer") == 1.0

    def test_non_it_industry_forces_min(self):
        assert rank_jobs.it_management_focus_score("Head of Electrical Engineering") == 1.0

    def test_non_it_title(self):
        assert rank_jobs.it_management_focus_score("Office Assistant") == 1.0


# ---------------------------------------------------------------------------
# score_job — re-weighted formula (no salary term)
# ---------------------------------------------------------------------------

class TestScoreJob:
    def test_perfect_job_scores_five(self):
        # Vienna (5.0)*.35 + en (5.0)*.35 + Head of Software Engineering (5.0)*.30 = 5.0
        job = {"title": "Head of Software Engineering", "location": "Wien", "language_hint": "en", "company": "Acme"}
        assert rank_jobs.score_job(job) == 5.0

    def test_score_is_clamped_to_minimum_one(self):
        job = {"title": "Office Assistant", "location": "Atlantis", "language_hint": "de", "company": "Acme"}
        assert rank_jobs.score_job(job) >= 1.0

    def test_score_never_exceeds_five(self):
        job = {"title": "Head of Software Engineering", "location": "Vienna", "language_hint": "en", "company": "Acme International"}
        assert rank_jobs.score_job(job) <= 5.0

    def test_salary_text_is_ignored(self):
        # Salary was removed from scoring; presence/absence must not change score.
        base = {"title": "Head of Engineering", "location": "Wien", "language_hint": "en", "company": "Acme"}
        with_salary = {**base, "salary_text": "EUR 200.000"}
        assert rank_jobs.score_job(base) == rank_jobs.score_job(with_salary)

    def test_english_role_outranks_identical_german_role(self):
        en = {"title": "Head of Engineering", "location": "Wien", "language_hint": "en", "company": "Acme"}
        de = {"title": "Head of Engineering", "location": "Wien", "language_hint": "de", "company": "Acme"}
        assert rank_jobs.score_job(en) > rank_jobs.score_job(de)


# ---------------------------------------------------------------------------
# fingerprint (dedup key)
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_case_and_whitespace_insensitive(self):
        a = {"title": "Head  of   Engineering", "company": "Acme  GmbH"}
        b = {"title": "head of engineering", "company": "acme gmbh"}
        assert rank_jobs.fingerprint(a) == rank_jobs.fingerprint(b)

    def test_different_company_yields_different_fingerprint(self):
        a = {"title": "Head of Engineering", "company": "Acme"}
        b = {"title": "Head of Engineering", "company": "Globex"}
        assert rank_jobs.fingerprint(a) != rank_jobs.fingerprint(b)

    def test_handles_missing_fields(self):
        assert rank_jobs.fingerprint({}) == "|"


# ---------------------------------------------------------------------------
# _source_domain_ok (lifted from main() during refactor)
# ---------------------------------------------------------------------------

class TestSourceDomainOk:
    def test_stepstone_source_requires_stepstone_host(self):
        ok = {"source_name": "stepstone_at_cto", "application_url": "https://www.stepstone.at/jobs/1"}
        bad = {"source_name": "stepstone_at_cto", "application_url": "https://example.com/jobs/1"}
        assert rank_jobs._source_domain_ok(ok) is True
        assert rank_jobs._source_domain_ok(bad) is False

    def test_linkedin_source_requires_linkedin_host(self):
        ok = {"source_name": "linkedin_at_leader_0", "application_url": "https://at.linkedin.com/jobs/view/1"}
        bad = {"source_name": "linkedin_at_leader_0", "application_url": "https://karriere.at/jobs/1"}
        assert rank_jobs._source_domain_ok(ok) is True
        assert rank_jobs._source_domain_ok(bad) is False

    def test_karriere_prefix_requires_karriere_host(self):
        ok = {"source_name": "karriere_at_cto", "application_url": "https://www.karriere.at/jobs/1"}
        bad = {"source_name": "karriere_at_cto", "application_url": "https://example.com/jobs/1"}
        assert rank_jobs._source_domain_ok(ok) is True
        assert rank_jobs._source_domain_ok(bad) is False

    def test_jobs_ch_exact_source(self):
        ok = {"source_name": "jobs_ch", "application_url": "https://www.jobs.ch/en/vacancies/1"}
        assert rank_jobs._source_domain_ok(ok) is True

    def test_unknown_source_always_ok(self):
        assert rank_jobs._source_domain_ok({"source_name": "arbeitnow_dach", "application_url": "https://x.io"}) is True


# ---------------------------------------------------------------------------
# _hard_reject_common (shared reject prefix for both relevance gates)
# ---------------------------------------------------------------------------

class TestHardRejectCommon:
    def test_ai_reject_is_rejected(self):
        assert rank_jobs._hard_reject_common("head of engineering", "https://x.io/jobs/1", "reject", "") is True

    def test_search_url_quality_is_rejected(self):
        assert rank_jobs._hard_reject_common("head of engineering", "https://x.io/jobs/1", "", "search") is True

    def test_search_engine_host_is_rejected(self):
        assert rank_jobs._hard_reject_common("cto", "https://www.google.com/search?q=x", "", "") is True

    def test_clean_job_is_not_rejected(self):
        assert rank_jobs._hard_reject_common("head of engineering", "https://acme.io/jobs/1", "", "") is False


# ---------------------------------------------------------------------------
# Geocoding (Nominatim) — arbitrary locations → Vienna-distance score
# ---------------------------------------------------------------------------

class TestGeocoding:
    def test_known_city_still_scores_from_table_without_geocoding(self, monkeypatch):
        def _boom(loc):
            raise AssertionError("geocode must not be called for a table city")

        monkeypatch.setattr(rank_jobs, "_geocode", _boom)
        assert rank_jobs.vienna_distance_score("Graz") == 4.2

    def test_arbitrary_city_geocoded_to_distance_bucket(self, monkeypatch):
        # Bregenz is not in the table; stub Vienna's own coords → 0 km → 5.0.
        monkeypatch.setattr(rank_jobs, "_geocode", lambda loc: (48.2082, 16.3738))
        assert rank_jobs.vienna_distance_score("Bregenz") == 5.0

    def test_two_cities_same_country_score_by_distance(self, monkeypatch):
        coords = {"nearcity": (48.2082, 16.3738), "farcity": (52.5200, 13.4050)}  # Vienna vs Berlin
        monkeypatch.setattr(rank_jobs, "_geocode", lambda loc: coords[loc.strip().lower()])
        near = rank_jobs.vienna_distance_score("NearCity")
        far = rank_jobs.vienna_distance_score("FarCity")
        assert near == 5.0 and far == 2.8   # 0 km vs ~524 km
        assert near > far

    def test_region_only_uses_region_coords_over_country_fallback(self, monkeypatch):
        # "Lower Austria" geocodes ~60 km from Vienna (St. Pölten) → bucket 4.7,
        # distinct from the Austria country fallback (280 km → 3.5).
        monkeypatch.setattr(rank_jobs, "_geocode", lambda loc: (48.2000, 15.6250))
        assert rank_jobs.vienna_distance_score("Lower Austria") == 4.7

    def test_geocode_failure_falls_back_to_country_distance(self, monkeypatch):
        monkeypatch.setattr(rank_jobs, "_geocode", lambda loc: None)
        # Not bare, geocode fails → Austria hint → 280 km → 3.5.
        assert rank_jobs.vienna_distance_score("Hintertupfing, Austria") == 3.5

    def test_geocode_result_cached_second_call_not_refetched(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "true")
        calls: list[str] = []

        def fake_query(loc):
            calls.append(loc)
            return (48.0, 16.0)

        monkeypatch.setattr(rank_jobs, "_geocode_query", fake_query)
        a = rank_jobs._geocode("Somewhere")
        b = rank_jobs._geocode("Somewhere")
        assert a == b == (48.0, 16.0)
        assert len(calls) == 1          # second call served from cache

    def test_geocode_respects_per_run_budget(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "true")
        monkeypatch.setenv("RANK_GEOCODE_MAX", "1")
        calls: list[str] = []

        def fake_query(loc):
            calls.append(loc)
            return (48.0, 16.0)

        monkeypatch.setattr(rank_jobs, "_geocode_query", fake_query)
        first = rank_jobs._geocode("CityA")
        second = rank_jobs._geocode("CityB")
        assert first == (48.0, 16.0)
        assert second is None           # budget exhausted after one query
        assert calls == ["CityA"]

    def test_geocoding_disabled_preserves_current_scores(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "false")
        # A would-be-geocoded city falls back exactly as today: unknown → 2.4.
        assert rank_jobs.vienna_distance_score("Bregenz") == 2.4

    # --- live Nominatim HTTP path (_geocode_query) with mocked requests ---

    def test_geocode_query_success_parses_lat_lon(self, monkeypatch):
        monkeypatch.setattr(
            rank_jobs.requests, "get",
            lambda *a, **k: _FakeResp(200, [{"lat": "48.2082", "lon": "16.3738"}]),
        )
        assert rank_jobs._geocode_query("Vienna") == (48.2082, 16.3738)

    def test_geocode_query_non_200_returns_none(self, monkeypatch):
        monkeypatch.setattr(rank_jobs.requests, "get", lambda *a, **k: _FakeResp(500, []))
        assert rank_jobs._geocode_query("Vienna") is None

    def test_geocode_query_empty_result_returns_none(self, monkeypatch):
        monkeypatch.setattr(rank_jobs.requests, "get", lambda *a, **k: _FakeResp(200, []))
        assert rank_jobs._geocode_query("Nowhere") is None

    def test_geocode_query_request_exception_returns_none(self, monkeypatch):
        def _raise(*a, **k):
            raise rank_jobs.requests.RequestException("boom")

        monkeypatch.setattr(rank_jobs.requests, "get", _raise)
        assert rank_jobs._geocode_query("Vienna") is None

    def test_geocode_query_malformed_payload_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            rank_jobs.requests, "get",
            lambda *a, **k: _FakeResp(200, [{"lat": "not-a-number", "lon": "x"}]),
        )
        assert rank_jobs._geocode_query("Vienna") is None

