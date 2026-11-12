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
            "junior_markers": ["junior"],
            "commercial_terms": ["deal"],
            "off_focus_roles": ["head of data"],
            "weak_body_terms": ["technology"],
            "hardware_body_terms": ["semiconductor"],
            "it_industries": ["Software Development"],
            "excluded_seniority": ["Entry level"],
            "search_keyword_sets": {"x": "Head of Zorptech"},
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


class TestTitleExclusionGate:
    """it-role-classification Story 2 (BAC-1/2/11/13/14/15/16/17): the shared
    title gate excludes junior / commercial / off-focus titles in both the strict
    filter and the shortfall fill, while leaving genuine IT leadership eligible."""

    def test_junior_title_excluded(self):
        # BAC-1: junior titles are not relevant even with an IT signal.
        for title in ("(Junior) Deal Strategy Manager Insurance Software (m/w/d)",
                      "Junior IT Project Manager"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_commercial_title_excluded_despite_software(self):
        # BAC-2: commercial titles are excluded even with software/cloud/saas.
        # Each reaches the gate (no excluded_areas substring) and would pass
        # without it (management + IT signal), so the gate is what excludes them.
        for title in ("Business Development Manager Software", "Account Manager SaaS",
                      "Partner Manager Cloud"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_commercial_term_outweighs_software_or_product(self):
        # BAC-14: a commercial term wins over a title software/product signal.
        for title in ("Product Marketing Manager Software", "Software Strategy Manager"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_shortfall_fill_applies_same_exclusions(self):
        # BAC-11: the relaxed shortfall fill applies the identical gate. Each title
        # passes the relaxed predicate (mgmt + domain + IT signal) and is excluded
        # only by the gate.
        for title in ("Junior Software Engineering Manager", "Business Development Manager Software",
                      "Head of Data & AI"):
            job = {"title": title, "source_url": "https://acme.io/jobs/1",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant_relaxed(job) is False, title

    def test_off_focus_data_ai_security_excluded(self):
        # BAC-16: off-focus Data/AI and IT-security leadership is excluded. Each
        # reaches the gate and would pass without it (management + the "it" signal),
        # so the gate is what excludes them.
        for title in ("IT Security & Compliance Manager", "Head of Data & AI", "Head of IT Security"):
            job = {"title": title, "application_url": "https://acme.com/jobs/123",
                   "company": "Acme Software GmbH"}
            assert rank_jobs.is_relevant(job) is False, title

    def test_associate_title_not_junior(self):
        # BAC-13: "associate" is not a junior marker; with IT context it stays eligible.
        job = {"title": "Associate Director Product", "application_url": "https://acme.com/jobs/123",
               "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant(job) is True

    def test_head_of_product_stays_eligible(self):
        # BAC-15: "product" is not a commercial term; "Head of Product" stays eligible.
        job = {"title": "Head of Product", "application_url": "https://acme.com/jobs/123",
               "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant(job) is True

    def test_data_plane_software_title_not_excluded(self):
        # BAC-17: whole-word/phrase matching — "Data Plane" does not hit the
        # "data science"/"head of data" off-focus phrases; a software title stays eligible.
        job = {"title": "Software Engineering Manager - Data Plane",
               "application_url": "https://acme.com/jobs/123"}
        assert rank_jobs.is_relevant(job) is True

    def test_head_of_data_platform_excluded_accepted_tradeoff(self):
        # Accepted trade-off (precision over recall): the `head of data` off-focus
        # phrase prefix-matches "Head of Data Platform", so this data-platform
        # leadership title is excluded even though `data platform` is an in-focus
        # domain term. Pinned so any future change to keep it is a conscious one.
        job = {"title": "Head of Data Platform", "application_url": "https://acme.com/jobs/123",
               "company": "Acme Software GmbH"}
        assert rank_jobs.is_relevant(job) is False


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


class TestTieredBodyEvidence:
    """it-role-classification Story 3 (BAC-5/12): a LinkedIn body confirms an
    ambiguous title only via a STRONG IT term; weak/generic words no longer
    confirm, while weak terms still confirm in titles via _has_it_signal."""

    def _ambiguous_linkedin_row(self, **extra) -> dict:
        # Leadership title, non-IT company → only the body can confirm it.
        row = {
            "title": "Head of Engineering",
            "company": "ENPULSION",
            "application_url": "https://www.linkedin.com/jobs/view/123/",
            "source_name": "linkedin_at",
        }
        row.update(extra)
        return row

    def test_generic_word_body_does_not_confirm(self):
        # BAC-5: a body of only weak/generic words does not confirm.
        row = self._ambiguous_linkedin_row(
            body_text="We drive technology and digital transformation of our data and the CTO agenda."
        )
        assert rank_jobs.is_relevant(row) is False

    def test_strong_body_term_confirms(self):
        # BAC-5: a strong IT term in the body confirms the ambiguous LinkedIn row.
        row = self._ambiguous_linkedin_row(
            body_text="You will own our software platform and cloud backend."
        )
        assert rank_jobs.is_relevant(row) is True

    def test_plant_chief_technical_officer_excluded_without_it_body(self):
        # BAC-12: a plant "Chief Technical Officer" with a generic production body
        # is not confirmed (no strong IT term).
        row = self._ambiguous_linkedin_row(
            title="Chief Technical Officer",
            company="RAS Fish Processing",
            body_text="Lead our production facilities, plant technology and manufacturing operations.",
        )
        assert rank_jobs.is_relevant(row) is False

    def test_weak_term_still_valid_in_title(self):
        # BAC-5: weak terms remain valid in TITLES — _has_it_signal is unchanged,
        # so "IoT Technology Director" stays eligible on the title (no body needed).
        job = {"title": "IoT Technology Director", "application_url": "https://acme.com/jobs/123"}
        assert rank_jobs.is_relevant(job) is True


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

    def _run_main(self, tmp_path, monkeypatch, envelope: dict) -> dict:
        in_path = tmp_path / "jobs_pool.json"
        in_path.write_text(json.dumps(envelope), encoding="utf-8")
        out_path = tmp_path / "jobs_ranked.json"
        monkeypatch.setattr(rank_jobs, "_input_path", lambda *a, **k: str(in_path))
        monkeypatch.setattr(rank_jobs, "_OUTPUT_PATH", str(out_path))
        monkeypatch.setattr(rank_jobs, "load_location_cache", lambda *a, **k: None)
        monkeypatch.setattr(rank_jobs, "save_location_cache", lambda *a, **k: None)
        rank_jobs.main()
        return json.loads(out_path.read_text(encoding="utf-8"))

    def test_funnel_passed_counts_deduped_not_duplicates(self, tmp_path, monkeypatch):
        # Two in-focus rows with the SAME title+company (one fingerprint) from two
        # sources: dedup keeps one. `passed` must credit only the surviving
        # source — not report both as having passed (honest per-source yield).
        row = {
            "title": "Chief Technology Officer",
            "company": "Acme Software GmbH",
            "location": "Wien", "display": "Wien", "pool_family": "board",
        }
        envelope = {
            "date": "2026-11-11",
            "pool_contribution": {
                "by_source": {"karriere_at_cto": 1, "stepstone_at_cto": 1},
                "by_family": {"board": 2},
            },
            "jobs": [
                {**row, "source_name": "karriere_at_cto",
                 "application_url": "https://www.karriere.at/jobs/111"},
                {**row, "source_name": "stepstone_at_cto",
                 "application_url": "https://www.stepstone.at/jobs/222"},
            ],
        }
        funnel = self._run_main(tmp_path, monkeypatch, envelope)["ranked_funnel"]
        passed = {s: funnel["by_source"][s]["passed"] for s in funnel["by_source"]}
        # Both reached; exactly one survives dedup, so passed sums to 1, not 2.
        assert sum(passed.values()) == 1
        assert funnel["by_source"]["karriere_at_cto"]["reached"] == 1
        assert funnel["by_source"]["stepstone_at_cto"]["reached"] == 1

    def test_funnel_degrades_without_pool_contribution(self, tmp_path, monkeypatch):
        # Raw-fallback input (no pool_contribution, no pool_family): main() must
        # not raise, pool counts default to 0, and the family key falls back to
        # `board` — the degrade path the spec's Security Surface declares.
        envelope = {
            "date": "2026-11-11",
            "jobs": [
                {
                    "title": "Chief Technology Officer",
                    "company": "Acme Software GmbH",
                    "application_url": "https://www.karriere.at/jobs/111",
                    "source_name": "karriere_at_cto",
                    "location": "Wien", "display": "Wien",
                },
            ],
        }
        funnel = self._run_main(tmp_path, monkeypatch, envelope)["ranked_funnel"]
        assert funnel["by_source"]["karriere_at_cto"]["pool"] == 0
        assert funnel["by_source"]["karriere_at_cto"]["shortlisted"] == 1
        # No pool_family on the row → the board default key carries it.
        assert "board" in funnel["by_family"]
        assert funnel["by_family"]["board"]["pool"] == 0

    def test_funnel_survives_malformed_pool_contribution(self, tmp_path, monkeypatch):
        # A malformed pool_contribution (non-dict values from a corrupt artifact)
        # must degrade, not crash — one bad field never aborts the rank.
        envelope = {
            "date": "2026-11-11",
            "pool_contribution": {"by_source": ["oops"], "by_family": "bad"},
            "jobs": [
                {
                    "title": "Chief Technology Officer",
                    "company": "Acme Software GmbH",
                    "application_url": "https://www.karriere.at/jobs/111",
                    "source_name": "karriere_at_cto",
                    "pool_family": "board",
                    "location": "Wien", "display": "Wien",
                },
            ],
        }
        funnel = self._run_main(tmp_path, monkeypatch, envelope)["ranked_funnel"]
        assert funnel["by_source"]["karriere_at_cto"]["pool"] == 0
        assert funnel["by_source"]["karriere_at_cto"]["shortlisted"] == 1


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




# ---------------------------------------------------------------------------
# Fail-loud geolocation end-to-end (Story 12 / ADR-0002)
# ---------------------------------------------------------------------------

class TestGeocodeFailLoud:
    def _cto_envelope(self):
        return {
            "date": "2026-11-12",
            "jobs": [{
                "title": "Chief Technology Officer",
                "company": "Acme Software GmbH",
                "application_url": "https://www.karriere.at/jobs/111",
                "location": "Wien", "display": "Wien",
            }],
        }

    def _wire_main(self, tmp_path, monkeypatch, envelope):
        in_path = tmp_path / "in.json"
        in_path.write_text(json.dumps(envelope), encoding="utf-8")
        monkeypatch.setattr(rank_jobs, "_input_path", lambda *a, **k: str(in_path))
        monkeypatch.setattr(rank_jobs, "_OUTPUT_PATH", str(tmp_path / "out.json"))
        monkeypatch.setattr(rank_jobs, "load_location_cache", lambda *a, **k: None)
        monkeypatch.setattr(rank_jobs, "save_location_cache", lambda *a, **k: None)
        monkeypatch.setattr(rank_jobs, "_load_geocode_cache", lambda *a, **k: None)
        return tmp_path / "out.json"

    def test_rank_main_saves_geocode_cache_on_geocode_failure(self, tmp_path, monkeypatch):
        self._wire_main(tmp_path, monkeypatch, self._cto_envelope())
        saved = {"n": 0}
        monkeypatch.setattr(rank_jobs, "_save_geocode_cache",
                            lambda *a, **k: saved.__setitem__("n", saved["n"] + 1))

        def _boom(_job):
            raise rank_jobs.GeocodeUnavailable("nominatim down")

        monkeypatch.setattr(rank_jobs, "score_job", _boom)
        with pytest.raises(rank_jobs.GeocodeUnavailable):
            rank_jobs.main()
        # The finally/except persisted the cache before the outage propagated.
        assert saved["n"] >= 1

    def test_rank_main_disabled_geocode_still_writes_ranked(self, tmp_path, monkeypatch):
        # RANK_GEOCODE=false (autouse fixture) → neutral distance, no raise.
        monkeypatch.setattr(rank_jobs, "_save_geocode_cache", lambda *a, **k: None)
        out = self._wire_main(tmp_path, monkeypatch, self._cto_envelope())
        rank_jobs.main()  # must not raise
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["jobs"], "a neutral-distance digest must still be written"

    def test_rank_cli_exits_geocode_code_on_outage(self, monkeypatch, capsys):
        def _outage():
            raise rank_jobs.GeocodeUnavailable("down")

        monkeypatch.setattr(rank_jobs, "main", _outage)
        with pytest.raises(SystemExit) as ei:
            rank_jobs._cli()
        assert ei.value.code == rank_jobs.EXIT_GEOCODE_UNAVAILABLE
        assert "GEOCODE_UNAVAILABLE" in capsys.readouterr().err

    def test_rank_cli_does_not_map_other_errors(self, monkeypatch):
        def _other():
            raise RuntimeError("something else")

        monkeypatch.setattr(rank_jobs, "main", _other)
        with pytest.raises(RuntimeError):
            rank_jobs._cli()

    def test_retry_reuses_warm_cache_no_regeocode(self, monkeypatch):
        # A cached location (hit OR recorded miss) must never re-query Nominatim,
        # so a retry after a persisted cache is warm (ADR-0002 bounds retries).
        def _must_not_query(*a, **k):
            raise AssertionError("cached location must not be re-geocoded")

        monkeypatch.setattr(scoring, "_geocode_query", _must_not_query)
        scoring._GEOCODE_CACHE["somewhere, österreich"] = (48.0, 16.0)
        scoring._GEOCODE_CACHE["nowhere, österreich"] = None
        assert scoring._geocode("Somewhere, Österreich") == (48.0, 16.0)
        assert scoring._geocode("Nowhere, Österreich") is None
