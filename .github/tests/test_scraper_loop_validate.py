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
