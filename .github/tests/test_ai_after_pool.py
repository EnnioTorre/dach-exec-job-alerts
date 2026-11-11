"""
Story 5 — AI pre-rank and the ranker read the bounded pool (`jobs_pool.json`).

Verifies the data-flow wiring: the AI stage's input is the pool (not the full
scrape), the ranker falls back to the pool when the AI output is absent, and both
degrade gracefully when the pool is missing. Stdlib + monkeypatched paths; no
network, no `complete_json`.
"""

import json
from pathlib import Path

import ai_pre_rank_enrich
import rank_jobs

_WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "dach-exec-job-alerts.yml"


# ---------------------------------------------------------------------------
# AI pre-rank input = the pool
# ---------------------------------------------------------------------------

def test_ai_pre_rank_reads_pool_input(tmp_path, monkeypatch):
    pool = tmp_path / "pool.json"
    raw = tmp_path / "raw.json"
    pool.write_text(json.dumps({"jobs": [{"title": "pooled"}]}), encoding="utf-8")
    raw.write_text(json.dumps({"jobs": [{"title": "raw"}]}), encoding="utf-8")
    monkeypatch.setattr(ai_pre_rank_enrich, "POOL_IN", str(pool))
    monkeypatch.setattr(ai_pre_rank_enrich, "RAW_IN", str(raw))

    assert ai_pre_rank_enrich.load_raw()["jobs"] == [{"title": "pooled"}]


def test_ai_input_bounded_by_pool(tmp_path, monkeypatch):
    pool = tmp_path / "pool.json"
    raw = tmp_path / "raw.json"
    pool.write_text(json.dumps({"jobs": [{"i": i} for i in range(50)]}), encoding="utf-8")
    raw.write_text(json.dumps({"jobs": [{"i": i} for i in range(300)]}), encoding="utf-8")
    monkeypatch.setattr(ai_pre_rank_enrich, "POOL_IN", str(pool))
    monkeypatch.setattr(ai_pre_rank_enrich, "RAW_IN", str(raw))

    # AI input is the 50-row pool, not the 300-row scrape.
    assert len(ai_pre_rank_enrich.load_raw()["jobs"]) == 50


def test_ai_pre_rank_falls_back_to_raw_when_no_pool(tmp_path, monkeypatch):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"jobs": [{"title": "raw"}]}), encoding="utf-8")
    monkeypatch.setattr(ai_pre_rank_enrich, "POOL_IN", str(tmp_path / "absent.json"))
    monkeypatch.setattr(ai_pre_rank_enrich, "RAW_IN", str(raw))

    assert ai_pre_rank_enrich.load_raw()["jobs"] == [{"title": "raw"}]


# ---------------------------------------------------------------------------
# rank input preference: AI-enriched pool → pool → raw
# ---------------------------------------------------------------------------

def test_rank_reads_pool_when_no_ai(tmp_path):
    ai = tmp_path / "ai.json"                       # absent
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    raw = tmp_path / "raw.json"
    raw.write_text("{}", encoding="utf-8")
    assert rank_jobs._input_path((str(ai), str(pool), str(raw))) == str(pool)


def test_rank_prefers_ai_over_pool(tmp_path):
    ai = tmp_path / "ai.json"
    ai.write_text("{}", encoding="utf-8")
    pool = tmp_path / "pool.json"
    pool.write_text("{}", encoding="utf-8")
    assert rank_jobs._input_path((str(ai), str(pool))) == str(ai)


def test_rank_last_ditch_raw_when_none_present(tmp_path):
    raw = tmp_path / "raw.json"                      # none exist on disk
    assert rank_jobs._input_path((str(tmp_path / "ai"), str(tmp_path / "pool"), str(raw))) == str(raw)


# ---------------------------------------------------------------------------
# workflow stage order
# ---------------------------------------------------------------------------

def test_workflow_stage_order_pool_before_ai():
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.index("pool_jobs.py") < text.index("ai_pre_rank_enrich.py")
