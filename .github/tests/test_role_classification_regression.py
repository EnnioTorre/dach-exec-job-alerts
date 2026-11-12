"""
End-to-end regression for the it-role-classification rules (Story 8, BAC-7 / BAC-10).

Runs the complete strict filter (`rank_jobs.is_relevant`) over a labelled fixture of the
rows the PRD names from the 2026-11-11 run (43088662099) and checks:

  - BAC-7: every row gets the outcome the PRD's "final rules" column gives it;
  - BAC-10: the rows that pass still meet the validation-loop minimums the CI
    workflow sets, checked with the real `test_scraper_loop.validate()`.

Fixture (fixtures/role_classification_11nov.json), one row per line:
  - title / company / application_url / source_name come from the run artifact.
  - body_text is NOT the posting text: it lists the rule terms (IT-signal, generic and
    hardware terms) found in the original posting, so the body rules see the same
    terms without republishing third-party text. Empty means title-only by design.
  - seniority / industries were never saved by the PRD evaluation; they are taken
    from the PRD where it states them, otherwise reconstructed — each row's `note`
    says which.
  - This is the PRD's named rows (41), not the full 74-row labelled set, so the PRD's
    "67 of 74" is not asserted. No row has a seniority; that rule is unit-tested in
    test_rank_jobs.py.

The fixture follows the live position_filter.json lists: a deliberate rule or list
change that alters a row's outcome must update that row's `expected` and `note`.
"""

import json
import re
from pathlib import Path

import position_filter
import rank_jobs
import test_scraper_loop as loop
from text_match import _contains_keyword

TESTS_DIR = Path(__file__).parent
FIXTURE = TESTS_DIR / "fixtures" / "role_classification_11nov.json"
WORKFLOW = TESTS_DIR.parent / "workflows" / "dach-exec-job-alerts.yml"


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _ci_thresholds() -> dict[str, int]:
    """TEST_MIN_* values from the CI workflow, keyed as validate() expects."""
    text = WORKFLOW.read_text(encoding="utf-8")
    found = {k.lower(): int(v) for k, v in re.findall(r'TEST_(MIN_[A-Z_]+):\s*"?(\d+)"?', text)}
    assert found, f"no TEST_MIN_* values found in {WORKFLOW.name}"
    return found


def _why(row: dict) -> str:
    """Rule inputs that decide a row, to make a mismatch actionable."""
    body = (row.get("body_text") or "").lower()
    pf = position_filter.POSITION_FILTER
    hw = sum(_contains_keyword(body, t) for t in pf["hardware_body_terms"])
    strong = sum(_contains_keyword(body, t) for t in rank_jobs._strong_it_terms())
    return (
        f"title_excluded={rank_jobs._title_excluded((row.get('title') or '').lower())} "
        f"it_signal={rank_jobs._has_it_signal(row)} hardware={hw} strong={strong} "
        f"it_industry={rank_jobs._has_it_industry(row)}"
    )


def test_labelled_set_regression_keeps_correct_rows():
    # BAC-7: kept IT rows pass; accepted losses and junior / commercial / hardware /
    # plant / generic-body rows are excluded; the PRD's title-only residual still passes.
    mismatches = []
    for row in _rows():
        actual = rank_jobs.is_relevant(row)
        if actual != (row["expected"] == "pass"):
            mismatches.append(
                f"{row['title']!r} ({row['company']}): expected={row['expected']} "
                f"actual={'pass' if actual else 'excluded'} [{row['bac']}] {_why(row)}\n"
                f"    note: {row['note']}"
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

    ok, msg = loop.validate(**_ci_thresholds())
    assert ok, msg
