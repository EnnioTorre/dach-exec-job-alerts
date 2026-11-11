"""
Component tests for the score-and-pool stage (`pool_jobs.py`).

Covers the five plan-named Story-4 tests: every row scored (no cap), per-family
minimum from each family's own top rows, merit fill of the remainder, empty-family
redistribution, and the score-all geocoding budget. Geocoding is kept hermetic by
the autouse fixture (no live Nominatim), mirroring `test_scoring.py`.
"""

import json

import pool_jobs
import pytest
import scoring

_VIENNA_COORDS = (48.2082, 16.3738)


@pytest.fixture(autouse=True)
def _hermetic_geocode(monkeypatch):
    monkeypatch.setenv("RANK_GEOCODE", "false")
    monkeypatch.setenv("RANK_GEOCODE_CACHE_PATH", "")
    monkeypatch.delenv("POOL_GEOCODE_MAX", raising=False)
    monkeypatch.delenv("POOL_TARGET_SIZE", raising=False)
    monkeypatch.delenv("POOL_FAMILY_MIN", raising=False)
    monkeypatch.setattr(scoring, "_geocode_used", 0)
    monkeypatch.setattr(scoring, "_last_geocode_ts", 0.0)
    monkeypatch.setattr(scoring, "_vienna_origin", None)
    monkeypatch.setattr(scoring.time, "sleep", lambda *_: None)
    scoring._GEOCODE_CACHE.clear()
    scoring._GEOCODE_CACHE[scoring._VIENNA_PLACE.strip().lower()] = _VIENNA_COORDS


def _scored(family, score, tag):
    """A pre-scored row (for the pool-building tests)."""
    return {"pool_family": family, "score": score, "source_name": f"{family}_{tag}", "title": f"{family} {tag}"}


def _scrape_row(family, i):
    """A scrape-shaped row with Vienna coords (so scoring needs no geocode)."""
    url = {
        "google": "https://acme.com/j",
        "linkedin": "https://www.linkedin.com/jobs/view/1",
        "board": "https://www.karriere.at/j",
    }[family]
    name = {"google": f"google_de_{i}", "linkedin": f"linkedin_at_{i}", "board": f"karriere_{i}"}[family]
    return {
        "title": "Head of Software Engineering",
        "language_hint": "en",
        "coords": _VIENNA_COORDS,
        "source_name": name,
        "application_url": url,
        "company": "Acme Software GmbH",
    }


# ---------------------------------------------------------------------------
# _pool_family
# ---------------------------------------------------------------------------

class TestPoolFamily:
    def test_google_proxy_source_is_google_even_with_board_host(self):
        # google_proxy rows resolve to a real job host — family must come from source_name first.
        assert pool_jobs._pool_family({"source_name": "google_de_0", "application_url": "https://www.stepstone.de/j"}) == "google"

    def test_linkedin_destination_host(self):
        assert pool_jobs._pool_family({"source_name": "linkedin_at_0", "application_url": "https://www.linkedin.com/jobs/1"}) == "linkedin"

    def test_other_is_board(self):
        assert pool_jobs._pool_family({"source_name": "karriere_0", "application_url": "https://www.karriere.at/j"}) == "board"


# ---------------------------------------------------------------------------
# score-all (no cap)
# ---------------------------------------------------------------------------

class TestScoreAllNoCap:
    def test_all_rows_scored_no_cap(self):
        # > 200 rows across families: every row is scored, none dropped by order.
        rows = [_scrape_row(fam, i) for fam in ("board", "linkedin", "google") for i in range(90)]
        assert len(rows) == 270
        pool_jobs.score_all(rows)
        assert all(isinstance(r["score"], float) for r in rows)
        assert {r["pool_family"] for r in rows} == {"board", "linkedin", "google"}


# ---------------------------------------------------------------------------
# pool building — per-family minimum + merit
# ---------------------------------------------------------------------------

class TestBuildPool:
    def test_each_family_gets_minimum_pool_share(self):
        # board floods with 100 top-scored rows; linkedin/google have only 5 each
        # at lower scores — each still gets its minimum from its OWN top rows.
        rows = (
            [_scored("board", 9.9, i) for i in range(100)]
            + [_scored("linkedin", 3.0 + i * 0.01, i) for i in range(5)]
            + [_scored("google", 2.0 + i * 0.01, i) for i in range(5)]
        )
        pool, _ = pool_jobs.build_pool(rows, target=50, fam_min=3)
        fams = [r["pool_family"] for r in pool]
        assert fams.count("linkedin") >= 3
        assert fams.count("google") >= 3

    def test_pool_remainder_filled_by_global_score(self):
        # board is merit-only (exempt from the minimum): the pool takes the two
        # highest-scored and drops the lowest.
        rows = [_scored("board", 5.0, "a"), _scored("board", 4.0, "b"), _scored("board", 3.0, "c")]
        pool, _ = pool_jobs.build_pool(rows, target=2, fam_min=3)
        assert [r["source_name"] for r in pool] == ["board_a", "board_b"]

    def test_empty_family_minimum_redistributed(self):
        # linkedin is EMPTY: it contributes nothing and its minimum slots are
        # reclaimed by global merit; the pool still fills to target.
        rows = [_scored("board", 9.0 - i * 0.1, i) for i in range(10)] + [_scored("google", 5.0, i) for i in range(3)]
        pool, phases = pool_jobs.build_pool(rows, target=8, fam_min=3)
        fams = [r["pool_family"] for r in pool]
        assert len(pool) == 8
        assert fams.count("linkedin") == 0
        assert fams.count("google") >= 3           # google keeps its minimum
        assert "merit" in phases.values()          # remainder filled by merit


# ---------------------------------------------------------------------------
# geocoding budget on the score-all pass
# ---------------------------------------------------------------------------

class TestScoreAllGeocodeBudget:
    def test_score_all_respects_geocode_budget(self, monkeypatch):
        monkeypatch.setenv("RANK_GEOCODE", "true")
        monkeypatch.setenv("POOL_GEOCODE_MAX", "2")
        calls: list[str] = []

        def fake_query(loc):
            calls.append(loc)
            return (48.0, 16.0)

        monkeypatch.setattr(scoring, "_geocode_query", fake_query)
        # Rows with NO coords and distinct uncached locations → each wants a geocode.
        rows = [
            {"title": "Head of Engineering", "language_hint": "en", "location": f"Town{i}", "source_name": f"board_{i}",
             "application_url": "https://www.karriere.at/j"}
            for i in range(5)
        ]
        pool_jobs.score_all(rows)
        assert len(calls) <= 2                      # budget caps the lookups
        assert all(isinstance(r["score"], float) for r in rows)  # beyond-budget rows still scored (neutral distance)


# ---------------------------------------------------------------------------
# envelope assembly + main() orchestration
# ---------------------------------------------------------------------------

class TestBuildEnvelope:
    def test_build_envelope_carries_pool_contribution_and_stats(self, monkeypatch):
        monkeypatch.setenv("POOL_TARGET_SIZE", "4")
        monkeypatch.setenv("POOL_FAMILY_MIN", "1")
        jobs = [
            _scored("board", 9.0, "b1"),
            _scored("board", 8.0, "b2"),
            _scored("linkedin", 3.0, "l1"),
            _scored("google", 2.0, "g1"),
        ]
        raw = {
            "date": "2026-11-11", "stats": {"s": 1}, "family_stats": {"linkedin": 1},
            "deduped_source_stats": {"x": 1}, "jobs": jobs,
        }
        env = pool_jobs._build_envelope(raw, jobs)

        assert env["date"] == "2026-11-11"
        assert env["stats"] == {"s": 1}
        assert env["family_stats"] == {"linkedin": 1}
        assert env["deduped_source_stats"] == {"x": 1}
        assert env["pool_target_size"] == 4 and env["pool_family_min"] == 1
        assert len(env["jobs"]) == 4

        by_family = env["pool_contribution"]["by_family"]
        assert by_family == {"board": 2, "linkedin": 1, "google": 1}  # min for L/G, merit for board
        assert "board_b1" in env["pool_contribution"]["by_source"]

        for fam in ("board", "linkedin", "google"):
            assert set(env["pool_stats"][fam]) == {"scored", "min_filled", "merit_filled", "contributed"}
        assert env["pool_stats"]["linkedin"]["min_filled"] == 1
        assert env["pool_stats"]["board"]["merit_filled"] == 2
        assert env["pool_stats"]["board"]["min_filled"] == 0


class TestMain:
    def test_main_reads_raw_writes_pool(self, tmp_path, monkeypatch):
        raw = {
            "date": "2026-11-11", "stats": {}, "family_stats": {}, "deduped_source_stats": {},
            "jobs": [_scrape_row("board", 0), _scrape_row("linkedin", 0), _scrape_row("google", 0)],
        }
        in_path = tmp_path / "jobs_raw.json"
        out_path = tmp_path / "jobs_pool.json"
        in_path.write_text(json.dumps(raw), encoding="utf-8")
        monkeypatch.setattr(pool_jobs, "_INPUT_PATH", str(in_path))
        monkeypatch.setattr(pool_jobs, "_OUTPUT_PATH", str(out_path))

        pool_jobs.main()

        written = json.loads(out_path.read_text(encoding="utf-8"))
        assert len(written["jobs"]) == 3
        assert all(isinstance(j["score"], float) and "pool_family" in j for j in written["jobs"])
        assert "pool_contribution" in written and "pool_stats" in written

    def test_main_fail_loud_persists_cache_then_raises(self, tmp_path, monkeypatch):
        in_path = tmp_path / "jobs_raw.json"
        in_path.write_text(json.dumps({"jobs": [_scrape_row("board", 0)]}), encoding="utf-8")
        monkeypatch.setattr(pool_jobs, "_INPUT_PATH", str(in_path))
        monkeypatch.setattr(pool_jobs, "_OUTPUT_PATH", str(tmp_path / "jobs_pool.json"))

        def _boom(job):
            raise scoring.GeocodeUnavailable("nominatim down")

        saved: list[bool] = []
        monkeypatch.setattr(pool_jobs, "score_job", _boom)
        monkeypatch.setattr(pool_jobs, "_save_geocode_cache", lambda: saved.append(True))

        with pytest.raises(scoring.GeocodeUnavailable):
            pool_jobs.main()
        assert saved == [True]  # cache persisted in the finally despite the raise
