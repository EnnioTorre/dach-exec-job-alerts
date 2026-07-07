"""
Unit tests for the parser-selection helpers extracted from scrape_extra.py.

Importing scrape_extra pulls in scrape_jobs (its parser functions) which is
safe offline. Tests focus on the pure routing logic in `_parser_for` and the
generic heading-scan fallback; no network calls are made.
"""

import scrape_extra
import scrape_jobs


class TestParserFor:
    def test_exact_parser_map_entry_wins(self):
        # jobs_ch is a known PARSER_MAP key → parse_jobs_ch regardless of URL.
        assert scrape_extra._parser_for("https://whatever.example/x", "jobs_ch") is scrape_jobs.parse_jobs_ch

    def test_stepstone_domain_heuristic(self):
        assert scrape_extra._parser_for("https://www.stepstone.de/jobs/1", "ai_extra") is scrape_jobs.parse_stepstone

    def test_karriere_domain_heuristic(self):
        assert scrape_extra._parser_for("https://www.karriere.at/jobs/1", "ai_extra") is scrape_jobs.parse_karriere_at

    def test_indeed_uses_rss(self):
        assert scrape_extra._parser_for("https://de.indeed.com/rss?q=cto", "ai_extra") is scrape_jobs.parse_rss

    def test_linkedin_and_google_use_serp_parser(self):
        assert scrape_extra._parser_for("https://linkedin.com/jobs/view/1", "x") is scrape_jobs.parse_google_jobs
        assert scrape_extra._parser_for("https://google.com/search?q=cto", "x") is scrape_jobs.parse_google_jobs

    def test_jobs_ch_domain_heuristic(self):
        assert scrape_extra._parser_for("https://www.jobs.ch/en/vacancies/1", "x") is scrape_jobs.parse_jobs_ch

    def test_unknown_returns_none(self):
        assert scrape_extra._parser_for("https://acme.io/careers", "x") is None


class TestGenericHeadingScan:
    def test_extracts_headings_with_links(self):
        html = """
        <html><body>
          <a href="/jobs/1"><h2>Head of Engineering</h2></a>
          <h3>Short</h3>
          <a href="https://acme.io/jobs/2"><h3>Director of Platform</h3></a>
        </body></html>
        """
        jobs = scrape_extra._generic_heading_scan(html, "https://acme.io/careers", "ai_extra")
        titles = [j["title"] for j in jobs]
        assert "Head of Engineering" in titles
        assert "Director of Platform" in titles
        # "Short" (<=5 chars) is skipped.
        assert "Short" not in titles

    def test_relative_href_is_made_absolute(self):
        html = '<a href="/jobs/1"><h2>Head of Engineering</h2></a>'
        jobs = scrape_extra._generic_heading_scan(html, "https://acme.io/careers", "ai_extra")
        assert jobs[0]["application_url"] == "https://acme.io/jobs/1"

    def test_empty_html_yields_nothing(self):
        assert scrape_extra._generic_heading_scan("<html></html>", "https://acme.io", "x") == []
