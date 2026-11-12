"""
End-to-end regression for the it-role-classification rules (Story 8, BAC-7 / BAC-10).

Runs the complete strict filter (`rank_jobs.is_relevant`) over a labelled fixture of the
rows the PRD names from the 2026-11-11 run (43088662099) and checks:

  - BAC-7: every row gets the outcome the PRD's "final rules" column gives it;
  - BAC-10: the rows that pass still meet the CI validation-loop minimums,
    checked with the real `test_scraper_loop.validate()`.

Fixture provenance and limits (fixtures/role_classification_11nov.json):
  - title / company / application_url / source_name / body_text are copied from the
    run artifact (email addresses redacted); the NXP RF body is reconstructed from the
    PRD excerpt because the artifact has none.
  - seniority / industries were never saved by the PRD evaluation; they are
    reconstructed from the PRD and marked in each row's `note`.
  - label / expected follow the PRD. This is the PRD's named rows (41), not the full
    74-row labelled set, so the PRD's "67 of 74" is not asserted literally.

A deliberate rule change that alters a row's outcome must update that row's
`expected` with a reason in `note`.
"""

import json
from pathlib import Path

import rank_jobs
import test_scraper_loop as loop

FIXTURE = Path(__file__).parent / "fixtures" / "role_classification_11nov.json"

# Values set in .github/workflows/dach-exec-job-alerts.yml for the validation loop.
CI_THRESHOLDS = dict(
    min_raw=10,
    min_ranked=10,
    min_active_sources=2,
    min_ranked_sources=2,
    min_non_karriere_ranked_sources=1,
    min_non_karriere_ranked_jobs=1,
)


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_labelled_set_regression_keeps_correct_rows():
    # BAC-7: kept IT rows pass; accepted losses and junior / commercial / hardware /
    # plant / generic-body rows are excluded; the PRD's title-only residual still passes.
    mismatches = []
    for row in _rows():
        actual = rank_jobs.is_relevant(row)
        if actual != (row["expected"] == "pass"):
            mismatches.append(
                f"{row['title']!r} ({row['company']}): label={row['label']} "
                f"expected={row['expected']} actual={'pass' if actual else 'excluded'}"
            )
    assert not mismatches, "rows with an unexpected outcome:\n" + "\n".join(mismatches)


def test_new_rules_still_meet_min_yield(tmp_path, monkeypatch):
    # BAC-10: the rows passing the full filter clear the CI validation-loop minimums.
    rows = _rows()
    passed = [r for r in rows if rank_jobs.is_relevant(r)]
    stats: dict[str, int] = {}
    for r in rows:
        stats[r["source_name"]] = stats.get(r["source_name"], 0) + 1

    raw_path, ranked_path = tmp_path / "jobs_raw.json", tmp_path / "jobs_ranked.json"
    raw_path.write_text(json.dumps({"jobs": rows, "stats": stats}), encoding="utf-8")
    ranked_path.write_text(json.dumps({"jobs": passed}), encoding="utf-8")
    monkeypatch.setattr(loop, "RAW", raw_path)
    monkeypatch.setattr(loop, "RANKED", ranked_path)

    ok, msg = loop.validate(**CI_THRESHOLDS)
    assert ok, msg
