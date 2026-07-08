"""
Unit tests for `ai_pre_rank_enrich.py`.

Focus: the language_hint policy. The deterministic scraper detection (derived
from the actual posting body + German gender markers) must NOT be overridden by
the model, which only sees the short title/company line and mislabels
German postings with English-looking titles as "en". The model may only fill a
missing language_hint. Other AI fields (relevance, url_quality, location,
salary, note) are still applied.

Network is never hit: `call_models` and the JSON I/O are monkeypatched.
"""

import json

import ai_pre_rank_enrich as ape


def _run_main(monkeypatch, tmp_path, jobs, updates):
    """Run main() with load_raw/call_models/output path stubbed; return output data."""
    out = tmp_path / "jobs_raw_ai.json"
    monkeypatch.setattr(ape, "load_raw", lambda: {"date": "2026-07-08", "jobs": jobs})
    monkeypatch.setattr(ape, "call_models", lambda prompt: {"updates": updates})
    monkeypatch.setattr(ape, "RAW_AI_OUT", str(out))
    ape.main()
    with open(out, encoding="utf-8") as f:
        return json.load(f)


class TestLanguagePolicy:
    def test_ai_does_not_override_existing_deterministic_language(self, monkeypatch, tmp_path):
        jobs = [{"title": "Engineering Lead (m/w/d) Enterprise Software",
                 "company": "beatvest", "language_hint": "de"}]
        updates = [{"id": "j0", "language_hint": "en"}]  # model tries to flip de->en
        data = _run_main(monkeypatch, tmp_path, jobs, updates)
        assert data["jobs"][0]["language_hint"] == "de"

    def test_ai_fills_missing_language(self, monkeypatch, tmp_path):
        jobs = [{"title": "Head of Platform", "company": "Acme", "language_hint": ""}]
        updates = [{"id": "j0", "language_hint": "en"}]
        data = _run_main(monkeypatch, tmp_path, jobs, updates)
        assert data["jobs"][0]["language_hint"] == "en"

    def test_ai_fills_absent_language_field(self, monkeypatch, tmp_path):
        jobs = [{"title": "Head of Platform", "company": "Acme"}]  # no key at all
        updates = [{"id": "j0", "language_hint": "de"}]
        data = _run_main(monkeypatch, tmp_path, jobs, updates)
        assert data["jobs"][0]["language_hint"] == "de"

    def test_invalid_ai_language_is_ignored(self, monkeypatch, tmp_path):
        jobs = [{"title": "Head of Platform", "company": "Acme", "language_hint": ""}]
        updates = [{"id": "j0", "language_hint": "french"}]
        data = _run_main(monkeypatch, tmp_path, jobs, updates)
        assert data["jobs"][0].get("language_hint", "") == ""


class TestOtherFieldsStillApplied:
    def test_relevance_and_url_quality_and_note_applied(self, monkeypatch, tmp_path):
        jobs = [{"title": "Head of Engineering", "company": "Acme", "language_hint": "en"}]
        updates = [{"id": "j0", "language_hint": "de", "relevance": "reject",
                    "url_quality": "listing", "note": "category page"}]
        data = _run_main(monkeypatch, tmp_path, jobs, updates)
        j = data["jobs"][0]
        assert j["language_hint"] == "en"      # language preserved
        assert j["ai_relevance"] == "reject"   # other fields still applied
        assert j["ai_url_quality"] == "listing"
        assert j["ai_note"] == "category page"

    def test_location_filled_and_salary_only_when_empty(self, monkeypatch, tmp_path):
        jobs = [{"title": "CTO", "company": "Acme", "language_hint": "en",
                 "location": "", "salary_text": "EUR 200k"}]
        updates = [{"id": "j0", "location": "Vienna", "salary_text": "EUR 999k"}]
        data = _run_main(monkeypatch, tmp_path, jobs, updates)
        j = data["jobs"][0]
        assert j["location"] == "Vienna"          # empty location filled
        assert j["salary_text"] == "EUR 200k"     # existing salary not overwritten
