"""
Unit tests for the pure helpers in `scrape_jobs.py`.

Focus areas:
  - `_infer_language_hint`: the DE/EN heuristic detector (recently overhauled)
  - `_german_market_lang_hint`: German-market default + English-title probing
  - URL/host helpers: `_domain_family`, `_is_google_url`, `_extract_company_from_host`
  - misc: `_clean_text`, `_extract_salary`, `_parse_retry_after`,
    `_exponential_backoff_delay`

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
