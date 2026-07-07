"""
Unit tests for the pure helpers in `scrape_jobs.py`.

Focus areas:
  - `_infer_language_hint`: the DE/EN heuristic detector (recently overhauled)
  - `_german_market_lang_hint`: German-market default + English-title probing
  - URL/host helpers: `_domain_family`, `_is_google_url`, `_extract_company_from_host`
  - misc: `_clean_text`, `_extract_salary`, `_parse_retry_after`,
    `_exponential_backoff_delay`
  - refactor-extracted helpers: `_source_family`, `_dedup_by_source_family`,
    `_count_by`, `_parse_source_content`

Network is never hit: the one function that would fetch a page
(`_german_market_lang_hint`) is exercised with monkeypatched enrichment.
"""

import pytest

import scrape_jobs


# ---------------------------------------------------------------------------
# _infer_language_hint
# ---------------------------------------------------------------------------

class TestInferLanguageHint:
    def test_empty_text_defaults_to_english(self):
        assert scrape_jobs._infer_language_hint("") == "en"
        assert scrape_jobs._infer_language_hint("   ") == "en"

    def test_clear_english_description(self):
        text = "We are looking for a Head of Engineering to join our team and lead our platform."
        assert scrape_jobs._infer_language_hint(text) == "en"

    def test_clear_german_description(self):
        text = "Wir suchen einen Leiter für unser Unternehmen mit Erfahrung im Bereich Entwicklung."
        assert scrape_jobs._infer_language_hint(text) == "de"

    def test_umlauts_signal_german(self):
        assert scrape_jobs._infer_language_hint("Softwareentwickler in München") == "de"

    def test_gender_notation_flags_german_even_with_english_title(self):
        # English-looking title, but German gender marker (m/w/d) → German audience.
        assert scrape_jobs._infer_language_hint("Software Engineer (m/w/d)") == "de"

    def test_explicit_german_requirement_wins_immediately(self):
        assert scrape_jobs._infer_language_hint("Fluent Deutschkenntnisse required") == "de"

    def test_english_exec_title_stays_english(self):
        assert scrape_jobs._infer_language_hint("Chief Technology Officer") == "en"

    def test_gender_neutral_suffix_flags_german(self):
        assert scrape_jobs._infer_language_hint("Mitarbeiter:innen gesucht") == "de"


# ---------------------------------------------------------------------------
# _german_market_lang_hint
# ---------------------------------------------------------------------------

class TestGermanMarketLangHint:
    def test_german_title_returns_de_without_probing(self, monkeypatch):
        # Should never call the page fetcher when the title is already German.
        def _boom(url):  # pragma: no cover - must not be called
            raise AssertionError("page fetch should not happen for German titles")

        monkeypatch.setattr(scrape_jobs, "_fetch_job_page_language", _boom)
        assert scrape_jobs._german_market_lang_hint("Leiter Entwicklung", "https://karriere.at/jobs/1") == "de"

    def test_english_title_defaults_de_when_enrichment_disabled(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "false")
        assert scrape_jobs._german_market_lang_hint("Chief Technology Officer", "https://karriere.at/jobs/2") == "de"

    def test_english_title_uses_page_language_when_enabled(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "true")
        monkeypatch.setattr(scrape_jobs, "_fetch_job_page_language", lambda url: "en")
        assert scrape_jobs._german_market_lang_hint("Chief Technology Officer", "https://karriere.at/jobs/3") == "en"

    def test_english_title_falls_back_to_de_when_probe_returns_none(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "true")
        monkeypatch.setattr(scrape_jobs, "_fetch_job_page_language", lambda url: None)
        assert scrape_jobs._german_market_lang_hint("Chief Technology Officer", "https://karriere.at/jobs/4") == "de"


# ---------------------------------------------------------------------------
# _serp_lang_hint
# ---------------------------------------------------------------------------

class TestSerpLangHint:
    def test_german_snippet_returns_de_without_enrichment(self, monkeypatch):
        # A clearly German snippet must not trigger any network enrichment.
        def _boom(*a, **k):  # pragma: no cover - must not be called
            raise AssertionError("enrichment should not run for a German snippet")

        monkeypatch.setattr(scrape_jobs, "_fetch_linkedin_job_language", _boom)
        monkeypatch.setattr(scrape_jobs, "_fetch_job_page_language", _boom)
        lang = scrape_jobs._serp_lang_hint(
            "Softwareentwickler (m/w/d)", "Acme GmbH",
            "Wir suchen einen Leiter für unser Unternehmen mit Erfahrung.",
            "https://www.linkedin.com/jobs/view/123",
        )
        assert lang == "de"

    def test_english_linkedin_row_uses_linkedin_enrichment(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "true")
        seen = {}

        def _ld(job_id):
            seen["job_id"] = job_id
            return "de"

        monkeypatch.setattr(scrape_jobs, "_fetch_linkedin_job_language", _ld)
        monkeypatch.setattr(scrape_jobs, "_fetch_job_page_language",
                            lambda url: (_ for _ in ()).throw(AssertionError("wrong enricher")))
        lang = scrape_jobs._serp_lang_hint(
            "Head of Engineering", "Acme",
            "Lead our platform team.",
            "https://www.linkedin.com/jobs/view/4055123/",
        )
        assert lang == "de"
        assert seen["job_id"] == "4055123"

    def test_english_generic_row_uses_page_enrichment(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "true")
        monkeypatch.setattr(scrape_jobs, "_fetch_job_page_language", lambda url: "de")
        lang = scrape_jobs._serp_lang_hint(
            "Engineering Manager", "Acme",
            "Join our team.",
            "https://www.stepstone.de/jobs/12345",
        )
        assert lang == "de"

    def test_disabled_enrichment_leans_de_on_german_market_host(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "false")
        lang = scrape_jobs._serp_lang_hint(
            "Engineering Manager", "Acme",
            "Join our team.",
            "https://www.stepstone.at/jobs/999",
        )
        assert lang == "de"

    def test_disabled_enrichment_keeps_en_on_neutral_host(self, monkeypatch):
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "false")
        lang = scrape_jobs._serp_lang_hint(
            "Engineering Manager", "Acme",
            "Join our team.",
            "https://www.jobs.ch/en/vacancies/12345",
        )
        assert lang == "en"

    def test_enrichment_unavailable_keeps_en_on_neutral_host(self, monkeypatch):
        # LinkedIn URL but enrichment returns None → keep the English guess
        # (linkedin.com is not a German-market fallback host).
        monkeypatch.setenv("SCRAPER_PAGE_LANG_ENRICH", "true")
        monkeypatch.setattr(scrape_jobs, "_fetch_linkedin_job_language", lambda job_id: None)
        lang = scrape_jobs._serp_lang_hint(
            "Head of Engineering", "Acme",
            "Lead our platform team.",
            "https://www.linkedin.com/jobs/view/777/",
        )
        assert lang == "en"


# ---------------------------------------------------------------------------
# Bolzano / South Tyrol source coverage
# ---------------------------------------------------------------------------

class TestBolzanoSources:
    def test_linkedin_bolzano_source_present(self):
        names = {s["name"] for s in scrape_jobs.SOURCES}
        assert "linkedin_bolzano_0" in names

    def test_google_bolzano_source_present_and_mapped(self):
        by_name = {s["name"]: s for s in scrape_jobs.SOURCES}
        assert "google_jobs_bolzano" in by_name
        assert by_name["google_jobs_bolzano"]["region"] == "IT"
        # The parser mapping for the google Bolzano source must exist.
        assert "google_jobs_bolzano" in scrape_jobs.PARSER_MAP

    def test_italy_region_matches_bolzano_locations(self):
        assert scrape_jobs._region_matches("Bolzano, Italy", "IT")
        assert scrape_jobs._region_matches("Bozen, Südtirol", "IT")
        assert not scrape_jobs._region_matches("Berlin, Germany", "IT")


# ---------------------------------------------------------------------------
# _domain_family / _is_google_url
# ---------------------------------------------------------------------------

class TestDomainFamily:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.google.com/search?q=x", "google"),
            ("https://www.google.de/search", "google"),
            ("https://at.linkedin.com/jobs/view/1", "linkedin.com"),
            ("https://www.karriere.at/jobs/1", "karriere.at"),
            ("https://arbeitnow.com/api/job-board-api", "arbeitnow.com"),
        ],
    )
    def test_domain_family(self, url, expected):
        assert scrape_jobs._domain_family(url) == expected

    def test_is_google_url_true(self):
        assert scrape_jobs._is_google_url("https://www.google.at/search?q=cto") is True

    def test_is_google_url_false(self):
        assert scrape_jobs._is_google_url("https://at.linkedin.com/jobs") is False


# ---------------------------------------------------------------------------
# _extract_company_from_host
# ---------------------------------------------------------------------------

class TestExtractCompanyFromHost:
    def test_strips_www_and_titlecases(self):
        assert scrape_jobs._extract_company_from_host("https://www.acme-corp.com/jobs/1") == "Acme Corp"

    def test_underscores_become_spaces(self):
        assert scrape_jobs._extract_company_from_host("https://tgs_international.io/x") == "Tgs International"


# ---------------------------------------------------------------------------
# _clean_text
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_collapses_whitespace_and_trims(self):
        assert scrape_jobs._clean_text("  Head  of\n  Engineering \t") == "Head of Engineering"

    def test_empty_string(self):
        assert scrape_jobs._clean_text("") == ""


# ---------------------------------------------------------------------------
# _extract_salary
# ---------------------------------------------------------------------------

class TestExtractSalary:
    def test_extracts_eur_range(self):
        assert scrape_jobs._extract_salary("Gehalt: EUR 80.000 - EUR 120.000 brutto") == "EUR 80.000 - EUR 120.000"

    def test_no_salary_returns_empty(self):
        assert scrape_jobs._extract_salary("A great role with a great team.") == ""


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


class TestParseRetryAfter:
    def test_parses_integer_seconds(self):
        assert scrape_jobs._parse_retry_after(_FakeResponse({"Retry-After": "30"})) == 30

    def test_missing_header_returns_none(self):
        assert scrape_jobs._parse_retry_after(_FakeResponse({})) is None

    def test_non_integer_returns_none(self):
        assert scrape_jobs._parse_retry_after(_FakeResponse({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None

    def test_clamps_to_minimum_one(self):
        assert scrape_jobs._parse_retry_after(_FakeResponse({"Retry-After": "0"})) == 1


# ---------------------------------------------------------------------------
# _exponential_backoff_delay
# ---------------------------------------------------------------------------

class TestExponentialBackoffDelay:
    @pytest.mark.parametrize("attempt", [0, 1, 2, 3])
    def test_delay_within_expected_bounds(self, attempt):
        # delay = min(base*2^attempt, max) + jitter in [0, delay]; so result is
        # within [base*2^attempt, 2*base*2^attempt] (before max clamp).
        base = 1.0
        d = scrape_jobs._exponential_backoff_delay(attempt, base=base, max_delay=60.0)
        expected = min(base * (2 ** attempt), 60.0)
        assert expected <= d <= 2 * expected

    def test_respects_max_delay_ceiling(self):
        d = scrape_jobs._exponential_backoff_delay(20, base=1.0, max_delay=5.0)
        # capped delay is 5.0, jitter adds up to 5.0 → never exceeds 10.0
        assert d <= 10.0


# ---------------------------------------------------------------------------
# _source_family (extracted from main() during refactor)
# ---------------------------------------------------------------------------

class TestSourceFamily:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.karriere.at/jobs/123", "karriere.at"),
            ("https://www.stepstone.de/stellenangebote/1", "stepstone"),
            ("https://at.linkedin.com/jobs/view/1", "linkedin"),
            ("https://de.indeed.com/viewjob?jk=1", "indeed"),
            ("https://www.jobs.ch/en/vacancies/1", "jobs.ch"),
            ("https://www.arbeitnow.com/jobs/1", "arbeitnow"),
        ],
    )
    def test_known_families(self, url, expected):
        assert scrape_jobs._source_family({"application_url": url}) == expected

    def test_falls_back_to_source_url(self):
        assert scrape_jobs._source_family({"source_url": "https://www.karriere.at/x"}) == "karriere.at"

    def test_unknown_host_returns_host(self):
        assert scrape_jobs._source_family({"application_url": "https://acme.io/jobs/1"}) == "acme.io"

    def test_no_url_returns_unknown(self):
        assert scrape_jobs._source_family({}) == "unknown"


# ---------------------------------------------------------------------------
# _dedup_by_source_family (extracted from main() during refactor)
# ---------------------------------------------------------------------------

class TestDedupBySourceFamily:
    def test_collapses_same_title_company_and_family(self):
        jobs = [
            {"title": "CTO", "company": "Acme", "application_url": "https://karriere.at/jobs/1"},
            {"title": "cto", "company": "acme", "application_url": "https://karriere.at/jobs/2"},
        ]
        assert len(scrape_jobs._dedup_by_source_family(jobs)) == 1

    def test_keeps_same_role_across_different_boards(self):
        jobs = [
            {"title": "CTO", "company": "Acme", "application_url": "https://karriere.at/jobs/1"},
            {"title": "CTO", "company": "Acme", "application_url": "https://at.linkedin.com/jobs/view/2"},
        ]
        assert len(scrape_jobs._dedup_by_source_family(jobs)) == 2

    def test_preserves_input_order(self):
        jobs = [
            {"title": "A", "company": "X", "application_url": "https://karriere.at/1"},
            {"title": "B", "company": "Y", "application_url": "https://karriere.at/2"},
        ]
        out = scrape_jobs._dedup_by_source_family(jobs)
        assert [j["title"] for j in out] == ["A", "B"]

    def test_empty_input(self):
        assert scrape_jobs._dedup_by_source_family([]) == []


# ---------------------------------------------------------------------------
# _count_by (extracted from main() during refactor)
# ---------------------------------------------------------------------------

class TestCountBy:
    def test_counts_by_source_name(self):
        jobs = [
            {"source_name": "a"},
            {"source_name": "a"},
            {"source_name": "b"},
        ]
        assert scrape_jobs._count_by(jobs, lambda j: j.get("source_name", "unknown")) == {"a": 2, "b": 1}

    def test_missing_key_uses_default(self):
        jobs = [{}, {"source_name": "a"}]
        assert scrape_jobs._count_by(jobs, lambda j: j.get("source_name", "unknown")) == {"unknown": 1, "a": 1}

    def test_empty_input(self):
        assert scrape_jobs._count_by([], lambda j: "x") == {}


# ---------------------------------------------------------------------------
# _parse_source_content (extracted dispatch; behavior-preserving)
# ---------------------------------------------------------------------------

class TestParseSourceContent:
    def test_rss_dispatches_to_parse_rss(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "parse_rss", lambda c, n: [{"via": "rss"}])
        assert scrape_jobs._parse_source_content("x", "src", "rss") == [{"via": "rss"}]

    def test_json_api_dispatches_to_parse_json_jobs(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "parse_json_jobs", lambda c, n: [{"via": "json"}])
        assert scrape_jobs._parse_source_content("x", "src", "json_api") == [{"via": "json"}]

    def test_linkedin_api_dispatches_to_guest_parser(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "parse_linkedin_guest_api", lambda c, n: [{"via": "li"}])
        assert scrape_jobs._parse_source_content("x", "src", "linkedin_api") == [{"via": "li"}]

    def test_proxy_xml_body_uses_rss(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "_looks_like_xml", lambda c: True)
        monkeypatch.setattr(scrape_jobs, "parse_rss", lambda c, n: [{"via": "proxy_rss"}])
        assert scrape_jobs._parse_source_content("<xml/>", "src", "google_proxy") == [{"via": "proxy_rss"}]

    def test_proxy_html_body_uses_google_jobs(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "_looks_like_xml", lambda c: False)
        monkeypatch.setattr(scrape_jobs, "parse_google_jobs", lambda c, n: [{"via": "serp"}])
        assert scrape_jobs._parse_source_content("<html/>", "src", "search_proxy") == [{"via": "serp"}]

    def test_html_prefers_jsonld_when_present(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "extract_jsonld_jobs", lambda c: [{"raw": 1}])
        monkeypatch.setattr(scrape_jobs, "normalize_jsonld", lambda j, n: {"via": "jsonld"})
        assert scrape_jobs._parse_source_content("<html/>", "src", "html") == [{"via": "jsonld"}]

    def test_html_falls_back_to_site_parser(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "extract_jsonld_jobs", lambda c: [])
        monkeypatch.setattr(scrape_jobs, "PARSER_MAP", {"karriere_at_cto": lambda c, n: [{"via": "html_parser"}]})
        assert scrape_jobs._parse_source_content("<html/>", "karriere_at_cto", "html") == [{"via": "html_parser"}]

    def test_html_no_jsonld_no_parser_returns_empty(self, monkeypatch):
        monkeypatch.setattr(scrape_jobs, "extract_jsonld_jobs", lambda c: [])
        monkeypatch.setattr(scrape_jobs, "PARSER_MAP", {})
        assert scrape_jobs._parse_source_content("<html/>", "unknown_src", "html") == []

