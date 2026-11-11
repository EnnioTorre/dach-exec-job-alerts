"""
Guards the Story-4 score-and-pool stage wiring in the scheduled workflow.

The pool step must run `pool_jobs.py` after scrape and before AI pre-rank, and
must fail loud (no `continue-on-error`) so a geocoding outage fails the run
rather than shipping a poisoned pool (ADR-0002 / ADR-0003). Text-assert only —
`pyyaml` is not a CI dependency.
"""

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "dach-exec-job-alerts.yml"


def test_workflow_runs_pool_after_scrape_before_ai_fail_loud():
    text = _WORKFLOW.read_text(encoding="utf-8")

    assert "pool_jobs.py" in text, "the Score + pool step must run pool_jobs.py"
    scrape_i = text.index("scrape_jobs.py")
    pool_i = text.index("pool_jobs.py")
    ai_i = text.index("ai_pre_rank_enrich.py")
    assert scrape_i < pool_i < ai_i, "pool must run after scrape and before AI pre-rank"

    # Isolate the Score + pool step block (up to the next step's `name:`),
    # ignoring comment lines, and assert it is fail-loud — not continue-on-error.
    pool_block = text.split("name: Score + pool", 1)[1].split("- name:", 1)[0]
    pool_code = "\n".join(ln for ln in pool_block.splitlines() if not ln.lstrip().startswith("#"))
    assert "continue-on-error" not in pool_code, "the pool step must fail loud (no continue-on-error)"
