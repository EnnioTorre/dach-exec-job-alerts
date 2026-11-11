"""
Unit tests for the deterministic ranking logic in `rank_jobs.py`.

Focus areas:
  - relevance gating (`is_relevant`, `is_relevant_relaxed`)
  - dedup fingerprinting
  - row re-normalization and out-of-geography filtering

The scorer itself (distance / language / IT-management focus / `score_job`)
now lives in `scoring.py` and is tested in `test_scoring.py`.

These functions are pure and side-effect free, so no mocking is needed.
"""

import json

import position_filter
import pytest
import rank_jobs
import scoring


@pytest.fixture(autouse=True)
def _geocoding_off_by_default(monkeypatch):
    # The scorer (in `scoring`) geolocates both the Vienna origin and the row's
    # location; keep the ranking tests hermetic by disabling live geocoding and
    # seeding the origin from cache, mirroring the persistent cache in production.
    monkeypatch.setenv("RANK_GEOCODE", "false")
    monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", "")  # never touch disk in tests
    monkeypatch.setattr(scoring, "_geocode_used", 0)
    monkeypatch.setattr(scoring, "_last_geocode_ts", 0.0)
    monkeypatch.setattr(scoring, "_vienna_origin", None)
    scoring._GEOCODE_CACHE.clear()
    scoring._GEOCODE_CACHE[scoring._VIENNA_PLACE.strip().lower()] = (48.2082, 16.3738)


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


class TestConfigDrivenFilter:
    """BAC-2/3/4/10: the role filter reads its lists from position_filter.json."""

    def test_in_focus_leadership_title_is_relevant(self):
        for title in ("Head of Platform Engineering", "Chief Technology Officer (CTO)"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is True, title

    def test_off_focus_ic_excluded_from_shortlist(self):
        # Individual-contributor titles never pass the strict (shortlist) gate.
        for title in ("Backend Developer", "MLOps / Platform Engineer"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_non_it_discipline_excluded(self):
        job = {"title": "Head of Mechanical Engineering",
               "application_url": "https://acme.com/jobs/123", "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant(job) is False

    def test_ambiguous_title_excluded_without_it_context(self):
        # Generic title, no software/IT signal in title or company.
        for title in ("Head of Engineering", "Head of Product"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "ENPULSION"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_filter_reads_role_terms_from_config(self, tmp_path, monkeypatch):
        # A sentinel leadership title present only in a temp config must qualify,
        # proving the filter reads via the loader, not deleted inline lists.
        cfg = {
            "in_focus_roles": ["head of zorptech"],
            "domain_terms": ["zorptech"],
            "excluded_areas": ["sales"],
            "non_it_excluded": ["mechanical"],
            "management_terms": ["head"],
            "individual_contributor_markers": ["engineer"],
            "it_signal_terms": ["zorptech"],
            "generic_engineering_terms": ["engineer"],
            "search_keyword_sets": {"x": "zorptech"},
        }
        p = tmp_path / "position_filter.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        job = {"title": "Head of Zorptech", "application_url": "https://acme.com/jobs/123"}

        # The shipped config does not know the sentinel title.
        assert rank_jobs.is_relevant(job) is False
        # Re-point the loaded config; the filter now accepts it.
        monkeypatch.setattr(
            position_filter, "POSITION_FILTER", position_filter.load_position_filter(str(p))
        )
        assert rank_jobs.is_relevant(job) is True


class TestIsRelevantRelaxed:
    def test_relaxed_accepts_mgmt_plus_domain_without_url_hint(self):
        job = {"title": "Director of Cloud", "source_url": "https://acme.io/x"}
        assert rank_jobs.is_relevant_relaxed(job) is True

    def test_relaxed_rejects_individual_contributor(self):
        # BAC-5: the relaxed fallback is now leadership-only — ICs no longer
        # fill the shortlist (previously they did).
        job = {"title": "Senior Software Engineer", "source_url": "https://acme.io/jobs/1",
               "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant_relaxed(job) is False

    def test_relaxed_fill_adds_only_in_focus_leadership(self):
        # BAC-5: the relaxed fallback admits IT leadership but never ICs.
        lead = {"title": "Engineering Manager", "source_url": "https://acme.io/jobs/1",
                "company": "Acme Software GmbH"}
        ic = {"title": "Senior Backend Developer", "source_url": "https://acme.io/jobs/2",
              "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant_relaxed(lead) is True
        assert rank_jobs.is_relevant_relaxed(ic) is False

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
# LinkedIn IT-context from the retained posting body (Story 8a)
# ---------------------------------------------------------------------------

class TestLinkedinItContext:
    def _ambiguous_linkedin_row(self, **extra) -> dict:
        # Leadership title, non-IT company name → passes the role gates but fails
        # the title/company IT signal; only the body can confirm it.
        row = {
            "title": "Head of Engineering",
            "company": "ENPULSION",
            "application_url": "https://www.linkedin.com/jobs/view/123/",
            "source_name": "linkedin_at",
        }
        row.update(extra)
        return row

    def test_linkedin_role_confirmed_it_via_body(self):
        # Body carries an IT signal → the ambiguous LinkedIn role is confirmed.
        confirmed = self._ambiguous_linkedin_row(
            body_text="We seek a leader for our Kubernetes platform and cloud infrastructure."
        )
        assert rank_jobs.is_relevant(confirmed) is True
        # Same row, body with no IT context → excluded.
        off_topic = self._ambiguous_linkedin_row(
            body_text="Lead our logistics and warehouse operations team across the region."
        )
        assert rank_jobs.is_relevant(off_topic) is False

    def test_linkedin_ambiguous_without_body_stays_excluded(self):
        # No body at all → an ambiguous LinkedIn title stays excluded.
        assert rank_jobs.is_relevant(self._ambiguous_linkedin_row()) is False
        assert rank_jobs.is_relevant(self._ambiguous_linkedin_row(body_text="")) is False

    def test_body_does_not_rescue_non_linkedin_row(self):
        # The body path is LinkedIn-only: an IT body on a non-LinkedIn ambiguous
        # row must not confirm it.
        row = self._ambiguous_linkedin_row(
            application_url="https://acme.com/jobs/9",
            source_name="karriere_at_cto",
            body_text="Kubernetes cloud platform leadership.",
        )
        assert rank_jobs.is_relevant(row) is False


# ---------------------------------------------------------------------------
# Per-source / per-family funnel counts in jobs_ranked.json (Story 8b)
# ---------------------------------------------------------------------------

class TestRankedFunnel:
    def test_rank_emits_per_source_and_family_funnel_counts(self, tmp_path, monkeypatch):
        # A pool envelope with two sources / two families: a board row that is
        # clearly in focus (shortlisted) and a LinkedIn row that never passes the
        # role filter (reached ranking but not passed) — so the funnel can show an
        # unranked-at-filter source instead of a false 100% yield.
        pool = {
            "date": "2026-11-11",
            "pool_contribution": {
                "by_source": {"karriere_at_cto": 1, "linkedin_at": 1},
                "by_family": {"board": 1, "linkedin": 1},
            },
            "jobs": [
                {
                    "title": "Chief Technology Officer",
                    "company": "Acme Software GmbH",
                    "application_url": "https://www.karriere.at/jobs/111",
                    "source_name": "karriere_at_cto",
                    "pool_family": "board",
                    "location": "Wien", "display": "Wien",
                },
                {
                    "title": "Head of Sales",
                    "company": "Acme Retail",
                    "application_url": "https://www.linkedin.com/jobs/view/222/",
                    "source_name": "linkedin_at",
                    "pool_family": "linkedin",
                    "location": "Wien", "display": "Wien",
                },
            ],
        }
        in_path = tmp_path / "jobs_pool.json"
        in_path.write_text(json.dumps(pool), encoding="utf-8")
        out_path = tmp_path / "jobs_ranked.json"
        monkeypatch.setattr(rank_jobs, "_input_path", lambda *a, **k: str(in_path))
        monkeypatch.setattr(rank_jobs, "_OUTPUT_PATH", str(out_path))
        monkeypatch.setattr(rank_jobs, "load_location_cache", lambda *a, **k: None)
        monkeypatch.setattr(rank_jobs, "save_location_cache", lambda *a, **k: None)

        rank_jobs.main()

        result = json.loads(out_path.read_text(encoding="utf-8"))
        funnel = result["ranked_funnel"]

        # Every stage is present per source and per family.
        for stage in ("pool", "reached", "passed", "shortlisted"):
            assert stage in funnel["by_source"]["karriere_at_cto"]
            assert stage in funnel["by_family"]["board"]

        board = funnel["by_source"]["karriere_at_cto"]
        assert board == {"pool": 1, "reached": 1, "passed": 1, "shortlisted": 1}

        # The sales LinkedIn row reached ranking but never passed the role filter:
        # it must not look like a fully-yielding source.
        li = funnel["by_source"]["linkedin_at"]
        assert li["reached"] == 1
        assert li["passed"] == 0
        assert li["shortlisted"] == 0

        # Family view mirrors the source view and carries the pool contribution.
        assert funnel["by_family"]["board"]["pool"] == 1
        assert funnel["by_family"]["linkedin"]["pool"] == 1
        assert funnel["by_family"]["linkedin"]["passed"] == 0

        # Existing envelope keys are untouched (additive change).
        assert "deduped_source_stats" in result
        assert "jobs" in result


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


class TestStandardizedLocationRanking:
    def test_rank_uses_row_coords_for_distance_when_present(self):
        # Coords at Vienna → nearest bucket; far coords → lower. Also tolerates
        # the JSON list form of coords.
        assert scoring._location_distance_score({"coords": (48.2082, 16.3738)}) == 5.0
        assert scoring._location_distance_score({"coords": [37.7749, -122.4194]}) < 5.0

    def test_rank_drops_out_of_geography_rows_from_shortlist(self):
        assert rank_jobs._row_out_of_geo({"country": "USA", "region": "California"}) is True
        assert rank_jobs._row_out_of_geo({"country": "Österreich", "region": "Wien"}) is False
        assert rank_jobs._row_out_of_geo({"country": "Italien", "region": "Südtirol"}) is False
        assert rank_jobs._row_out_of_geo({"country": "—", "region": "—"}) is False  # unknown kept
        assert rank_jobs._row_out_of_geo({}) is False

    def test_rank_two_spellings_same_place_same_score(self):
        # Same coords; neither location string geolocates offline. Equal score
        # proves the row's coords (not the string) drive the sub-score.
        a = {"coords": (48.2082, 16.3738), "location": "Wien"}
        b = {"coords": (48.2082, 16.3738), "location": "Unresolvable Place XYZ"}
        assert scoring._location_distance_score(b) == 5.0              # coords, not the string
        assert scoring._location_distance_score(a) == scoring._location_distance_score(b)

    def test_rank_falls_back_to_location_when_no_coords(self):
        # No coords → geolocate the location string; a malformed coords also
        # degrades to the string path. Seed the geolocation so it is offline.
        scoring._GEOCODE_CACHE["wien"] = (48.2082, 16.3738)
        assert scoring._location_distance_score({"location": "Wien"}) == 5.0
        assert scoring._location_distance_score({"coords": "bad", "location": "Wien"}) == 5.0
        assert scoring._location_distance_score({"coords": [1.0], "location": "Wien"}) == 5.0

    def test_rank_renormalizes_ai_rewritten_location(self, monkeypatch):
        norm = {
            "place": "München", "region": "Bayern", "country": "Deutschland",
            "coords": (48.1351, 11.5820), "display": "München, Bayern, Deutschland",
        }
        monkeypatch.setattr(rank_jobs, "normalize_location", lambda raw: norm)
        ai_row = {"location": "Munich", "display": "München, Bayern, Deutschland"}  # AI rewrote location
        untouched = {
            "location": "Graz, Steiermark, Österreich", "display": "Graz, Steiermark, Österreich",
            "coords": (47.0707, 15.4395),
        }
        rank_jobs._renormalize_rows([ai_row, untouched])
        assert ai_row["display"] == "München, Bayern, Deutschland"
        assert ai_row["country"] == "Deutschland"
        assert untouched["coords"] == (47.0707, 15.4395)   # untouched, not re-normalized

    def test_rank_preserves_existing_resolved_place_scores(self, monkeypatch):
        def _boom(raw):
            raise AssertionError("re-normalized an already-resolved row")

        monkeypatch.setattr(rank_jobs, "normalize_location", _boom)
        row = {
            "location": "Graz, Steiermark, Österreich", "display": "Graz, Steiermark, Österreich",
            "coords": (47.0707, 15.4395), "country": "Österreich",
            "language_hint": "de", "title": "Head of Engineering",
        }
        before = scoring.score_job(row)
        rank_jobs._renormalize_rows([row])      # must NOT call normalize_location
        after = scoring.score_job(row)
        assert row["coords"] == (47.0707, 15.4395)
        assert before == after


