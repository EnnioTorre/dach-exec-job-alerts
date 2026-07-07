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
        header_idx = next(i for i, l in enumerate(lines) if l.startswith("| # | Role |"))

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
