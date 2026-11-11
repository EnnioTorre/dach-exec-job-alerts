"""
Guards removal of the order-based scrape cap (Story 4, PR-A).

`scrape_jobs.main()` must write ALL deduped rows to jobs_raw.json so every row
reaches the score+pool stage — no `all_jobs[:200]` cut by source order (BAC-1).
`main()` does live scraping (not unit-testable), so this guards the write line by
source inspection; the behavioural BAC-1 proof is
`test_pool_jobs.py::test_all_rows_scored_no_cap`.
"""

import inspect

import scrape_jobs


def test_scrape_writes_all_deduped_rows_uncapped():
    src = inspect.getsource(scrape_jobs.main)
    assert "all_jobs[:200]" not in src, "the order-based [:200] scrape cap must be removed (BAC-1)"
    assert "jobs_out = all_jobs" in src, "jobs_out must be the full deduped set"
