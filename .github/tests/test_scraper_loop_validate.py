"""
Unit tests for the scraper validation loop (test_scraper_loop.py).

Covers:
  - the pure `validate()` threshold logic
  - the new read-only fast path in `main()`: when existing artifacts already
    pass validation, the scraper must NOT be re-run (this is the fix for the
    stale-stats desync where re-scraping clobbered jobs_raw.json while
    jobs_raw_ai.json kept first-pass data).

`main()` shells out via `_run`; tests monkeypatch it so nothing is executed.
"""

import json
from pathlib import Path

import test_scraper_loop as loop


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def _point_files(monkeypatch, tmp_path, raw_obj, ranked_obj):
    raw = tmp_path / "jobs_raw.json"
    ranked = tmp_path / "jobs_ranked.json"
    _write(raw, raw_obj)
    _write(ranked, ranked_obj)
    monkeypatch.setattr(loop, "RAW", raw)
    monkeypatch.setattr(loop, "RANKED", ranked)
    return raw, ranked


def _good_raw():
    return {
        "jobs": [{"title": f"Job {i}", "source_name": "linkedin_at_leader_0"} for i in range(15)],
        "stats": {"linkedin_at_leader_0": 10, "stepstone_at_cto": 5},
    }


def _good_ranked():
    return {
        "jobs": [
            {"title": "A", "source_name": "linkedin_at_leader_0"},
            {"title": "B", "source_name": "stepstone_at_cto"},
        ]
    }


class TestValidate:
    def test_passes_on_healthy_output(self, monkeypatch, tmp_path):
        _point_files(monkeypatch, tmp_path, _good_raw(), _good_ranked())
        ok, reason = loop.validate(10, 1, 2, 2, 1, 1)
        assert ok is True
        assert "valid result" in reason

    def test_fails_when_raw_too_low(self, monkeypatch, tmp_path):
        _point_files(monkeypatch, tmp_path, {"jobs": [{"title": "x"}], "stats": {"a": 1, "b": 1}}, _good_ranked())
        ok, reason = loop.validate(10, 1, 2, 2, 1, 1)
        assert ok is False
        assert "raw jobs too low" in reason

    def test_fails_when_missing_raw(self, monkeypatch, tmp_path):
        ranked = tmp_path / "jobs_ranked.json"
        _write(ranked, _good_ranked())
        monkeypatch.setattr(loop, "RAW", tmp_path / "does_not_exist.json")
        monkeypatch.setattr(loop, "RANKED", ranked)
        ok, reason = loop.validate(10, 1, 2, 2, 1, 1)
        assert ok is False
        assert "missing" in reason

    def test_fails_when_all_ranked_are_karriere(self, monkeypatch, tmp_path):
        ranked = {"jobs": [{"title": "A", "source_name": "karriere_at_cto"}]}
        _point_files(monkeypatch, tmp_path, _good_raw(), ranked)
        ok, reason = loop.validate(10, 1, 2, 1, 1, 1)
        assert ok is False
        assert "non-karriere" in reason


class TestReadOnlyFastPath:
    def test_skips_rescrape_when_already_valid(self, monkeypatch, tmp_path):
        _point_files(monkeypatch, tmp_path, _good_raw(), _good_ranked())

        # If the scraper is invoked, the test must fail — the whole point of the
        # fix is that valid existing output is not clobbered by a re-scrape.
        def _boom(*a, **k):
            raise AssertionError("_run must not be called when output is valid")

        monkeypatch.setattr(loop, "_run", _boom)
        monkeypatch.setattr(loop.os, "makedirs", lambda *a, **k: None)
        monkeypatch.setenv("TEST_MIN_RAW", "10")
        monkeypatch.setenv("TEST_MIN_RANKED", "1")
        monkeypatch.setenv("TEST_MIN_ACTIVE_SOURCES", "2")
        monkeypatch.setenv("TEST_MIN_RANKED_SOURCES", "2")

        assert loop.main() == 0

    def test_enters_loop_when_output_invalid(self, monkeypatch, tmp_path):
        # Raw too low → not valid → must attempt a re-scrape (here mocked to
        # "fail" so we don't actually run anything and the loop exits non-zero).
        _point_files(monkeypatch, tmp_path, {"jobs": [], "stats": {}}, {"jobs": []})

        calls = {"n": 0}

        def _fake_run(cmd, extra_env=None):
            calls["n"] += 1
            return (1, "", "boom")  # non-zero → attempt fails

        monkeypatch.setattr(loop, "_run", _fake_run)
        monkeypatch.setattr(loop.os, "makedirs", lambda *a, **k: None)
        monkeypatch.setenv("TEST_ATTEMPTS", "1")
        monkeypatch.setenv("TEST_MIN_RAW", "10")

        rc = loop.main()
        assert rc == 1
        assert calls["n"] >= 1  # the scraper WAS invoked for the invalid case


# ---------------------------------------------------------------------------
# Fail-loud geolocation end-to-end (Story 12 / ADR-0002)
# ---------------------------------------------------------------------------


class TestGeocodeOutageLoop:
    def test_scraper_loop_does_not_retry_on_geocode_outage(self, monkeypatch, tmp_path):
        # Invalid existing output → enter the loop. Rank reports a hard outage;
        # the loop must abort immediately (no second attempt) and return the code.
        _point_files(monkeypatch, tmp_path, {"jobs": [], "stats": {}}, {"jobs": []})
        rank_calls = {"n": 0}

        def _fake_run(cmd, extra_env=None):
            if "rank_jobs.py" in cmd[1]:
                rank_calls["n"] += 1
                return (loop.EXIT_GEOCODE_UNAVAILABLE, "", "GEOCODE_UNAVAILABLE: down")
            return (0, "", "")  # scrape ok

        monkeypatch.setattr(loop, "_run", _fake_run)
        monkeypatch.setattr(loop.os, "makedirs", lambda *a, **k: None)
        monkeypatch.setenv("TEST_ATTEMPTS", "3")

        rc = loop.main()
        assert rc == loop.EXIT_GEOCODE_UNAVAILABLE
        assert rank_calls["n"] == 1  # aborted — not retried 3×

    def test_scraper_loop_still_retries_genuine_bad_pass(self, monkeypatch, tmp_path):
        # A plain non-zero rank exit still retries up to TEST_ATTEMPTS.
        _point_files(monkeypatch, tmp_path, {"jobs": [], "stats": {}}, {"jobs": []})
        rank_calls = {"n": 0}

        def _fake_run(cmd, extra_env=None):
            if "rank_jobs.py" in cmd[1]:
                rank_calls["n"] += 1
                return (1, "", "boom")  # generic failure
            return (0, "", "")

        monkeypatch.setattr(loop, "_run", _fake_run)
        monkeypatch.setattr(loop.os, "makedirs", lambda *a, **k: None)
        monkeypatch.setenv("TEST_ATTEMPTS", "2")

        rc = loop.main()
        assert rc == 1
        assert rank_calls["n"] == 2  # retried, not aborted


def test_workflow_final_rerank_not_continue_on_error():
    # The authoritative Stage-3c re-rank must fail the run on an outage, while the
    # optional AI stages stay continue-on-error (ADR-0002 end-to-end).
    wf = Path(__file__).resolve().parents[1] / "workflows" / "dach-exec-job-alerts.yml"
    text = wf.read_text(encoding="utf-8")
    # The step's own YAML, bounded by the next stage's comment banner.
    block = text.split("- name: Re-rank after second pass", 1)[1].split("# ---", 1)[0]
    assert "continue-on-error" not in block
    assert "continue-on-error: true" in text  # the AI stages still have it
