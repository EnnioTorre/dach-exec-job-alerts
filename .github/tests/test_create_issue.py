"""
Unit tests for the digest formatting in `create_issue.py`.

`format_body` builds the GitHub issue markdown. These tests verify the two
recent fixes:
  - the Salary column was removed from the Top 15 table
  - pipe characters in Role/Company/Location cells are escaped so titles like
    "... | Remote | Europe" no longer break the markdown table layout

`format_body` is pure (reads only the passed-in dict + a couple of env vars),
so no gh CLI / network calls are triggered.
"""

import create_issue


def _sample_data():
    return {
        "date": "2026-07-07",
        "jobs": [
            {
                "title": "Engineering Manager, Core Platform | Remote | Europe",
                "company": "n8n",
                "location": "Österreich",
                "language_hint": "en",
                "score": 3.9,
                "application_url": "https://at.linkedin.com/jobs/view/123",
            },
            {
                "title": "Head of Engineering",
                "company": "Acme",
                "location": "Wien",
                "language_hint": "en",
                "score": 4.4,
                "application_url": "https://acme.com/jobs/1",
            },
        ],
        "source_stats": {"linkedin_at": 2},
    }


class TestFormatBody:
    def test_returns_string(self):
        assert isinstance(create_issue.format_body(_sample_data()), str)

    def test_no_salary_column_in_top15_header(self):
        body = create_issue.format_body(_sample_data())
        # The ranked-roles header must not contain a Salary column anymore.
        header = "| # | Role | Company | Location | Lang | Score |"
        assert header in body
        assert "Salary |" not in body

    def test_weighting_note_has_no_salary_term(self):
        body = create_issue.format_body(_sample_data())
        assert "salary" not in body.lower()

    def test_pipe_in_title_is_escaped(self):
        body = create_issue.format_body(_sample_data())
        # The literal pipes inside the title must be backslash-escaped.
        assert r"Core Platform \| Remote \| Europe" in body

    def test_top15_rows_have_consistent_column_count(self):
        body = create_issue.format_body(_sample_data())
        lines = body.splitlines()
        header_idx = next(i for i, line in enumerate(lines) if line.startswith("| # | Role |"))

        # Count only *unescaped* pipes — the real column delimiters. Escaped
        # pipes (\|) inside cells are literals and must not split columns.
        def _delimiters(row: str) -> int:
            return row.replace(r"\|", "").count("|")

        header_pipes = _delimiters(lines[header_idx])
        for row in lines[header_idx + 2:header_idx + 4]:
            assert _delimiters(row) == header_pipes

    def test_includes_both_job_titles(self):
        body = create_issue.format_body(_sample_data())
        assert "Head of Engineering" in body
        assert "Engineering Manager, Core Platform" in body

    def test_empty_jobs_still_renders_without_error(self):
        data = {"date": "2026-07-07", "jobs": [], "source_stats": {}}
        body = create_issue.format_body(data)
        assert "Top 15 Ranked Roles" in body

    def test_digest_renders_display_field_when_present(self):
        data = {
            "date": "2026-07-07",
            "jobs": [
                {
                    "title": "CTO",
                    "company": "Acme",
                    "display": "Graz, Steiermark, Österreich",
                    "location": "raw-unused-location",
                    "language_hint": "de",
                    "score": 4.1,
                    "application_url": "https://acme.com/jobs/1",
                }
            ],
            "source_stats": {},
        }
        body = create_issue.format_body(data)
        assert "Graz, Steiermark, Österreich" in body
        assert "raw-unused-location" not in body

    def test_digest_falls_back_to_raw_location_when_no_display(self):
        data = {
            "date": "2026-07-07",
            "jobs": [
                {
                    "title": "Head of Engineering",
                    "company": "Acme",
                    "location": "Wien, Österreich",
                    "language_hint": "de",
                    "score": 4.4,
                    "application_url": "https://acme.com/jobs/2",
                }
            ],
            "source_stats": {},
        }
        body = create_issue.format_body(data)
        assert "Wien, Österreich" in body


def _funnel_data():
    # A pool/reached/passed/shortlisted funnel across three sources: a board
    # source that passes, a LinkedIn search source with a 50% pass-share, and a
    # Google source whose rows never reached ranking.
    return {
        "date": "2026-11-11",
        "jobs": [],
        "source_stats": {"karriere_at_cto": 5, "linkedin_at": 10, "google_jobs_at": 3},
        "ranked_funnel": {
            "by_source": {
                "karriere_at_cto": {"pool": 5, "reached": 4, "passed": 3, "shortlisted": 2},
                "linkedin_at": {"pool": 10, "reached": 10, "passed": 5, "shortlisted": 1},
                "google_jobs_at": {"pool": 0, "reached": 0, "passed": 0, "shortlisted": 0},
            },
            "by_family": {
                "linkedin": {"pool": 10},
                "google": {"pool": 0},
                "board": {"pool": 5},
            },
        },
    }


def _source_row(body: str, src: str) -> str:
    return next(ln for ln in body.splitlines() if f"`{src}`" in ln)


class TestSourcePerformanceFunnel:
    def test_funnel_reports_reached_and_passed_per_source(self):
        body = create_issue.format_body(_funnel_data())
        assert "| Source | Scraped | Pool | Reached | Passed | Shortlist | Top 15 | Pass-share |" in body
        row = _source_row(body, "karriere_at_cto")
        # Scraped 5, Pool 5, Reached 4, Passed 3, Shortlist 2 in order.
        assert "| 5 | 5 | 4 | 3 | 2 |" in row

    def test_funnel_reports_family_pool_contribution(self):
        body = create_issue.format_body(_funnel_data())
        assert "Pool contribution by family:" in body
        assert "linkedin 10" in body
        assert "board 5" in body

    def test_funnel_no_100pct_yield_for_unranked_source(self):
        body = create_issue.format_body(_funnel_data())
        row = _source_row(body, "google_jobs_at")
        # reached == 0 → pass-share is '—', never a false 100%.
        assert row.rstrip().endswith("| — |")
        assert "100%" not in row

    def test_funnel_reports_search_source_pass_share(self):
        body = create_issue.format_body(_funnel_data())
        row = _source_row(body, "linkedin_at")
        # passed 5 / reached 10 = 50% pass-share.
        assert row.rstrip().endswith("| 50% |")

    def test_funnel_summary_drops_after_filter_dedup_wording(self):
        body = create_issue.format_body(_funnel_data())
        assert "after filter + dedup" not in body
        # Pin the honest totals: reached = 4+10+0, passed = 3+5+0.
        assert "**14** reached ranking" in body
        assert "**8** passed the role filter" in body

    def test_funnel_counts_pool_source_absent_from_scrape_stats(self):
        # A source present only in the funnel (e.g. an MCP/second-pass merge, no
        # scrape-stats row) must still get a table row and be counted in the
        # totals — otherwise passed < shortlisted and the funnel is non-monotonic.
        data = _funnel_data()
        data["ranked_funnel"]["by_source"]["mcp_merge"] = {
            "pool": 2, "reached": 2, "passed": 2, "shortlisted": 1,
        }
        body = create_issue.format_body(data)
        row = _source_row(body, "mcp_merge")
        # Scraped falls back to 0; its funnel counts still render.
        assert "| 0 | 2 | 2 | 2 | 1 |" in row
        # Totals now include it: reached 14+2, passed 8+2.
        assert "**16** reached ranking" in body
        assert "**10** passed the role filter" in body

    def test_funnel_table_renders_ai_columns_when_present(self):
        data = _funnel_data()
        data["ai_enrichment"] = {
            "source_analysis": [
                {
                    "source": "linkedin_at",
                    "quality_rating": 4,
                    "recommendation": "keep",
                    "note": "strong roles",
                },
            ]
        }
        body = create_issue.format_body(data)
        assert "| Source | Scraped | Pool | Reached | Passed | Shortlist | Top 15 | Pass-share | AI Quality | Recommendation |" in body
        row = _source_row(body, "linkedin_at")
        assert "4/5" in row
        assert "keep — strong roles" in row

    def test_source_table_falls_back_without_funnel(self):
        # A pre-Story-8 artifact (no ranked_funnel) renders the legacy table.
        data = {
            "date": "2026-11-11",
            "jobs": [],
            "source_stats": {"karriere_at_cto": 5},
            "deduped_source_stats": {"karriere_at_cto": 3},
        }
        body = create_issue.format_body(data)
        assert "| Source | Scraped | Unique | Shortlist | Top 15 | Yield |" in body
        assert "after filter + dedup" in body
        assert "Reached | Passed" not in body
