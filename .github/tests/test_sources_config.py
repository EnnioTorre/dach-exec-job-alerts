"""
Tests for the externalised source registry (.github/config/sources.json) and
its Python loader in scrape_jobs.

These guard the refactor that moved the SEARCH CONTENT (keywords, locations,
page offsets, SERP queries) out of Python and into JSON, while keeping the
type->parser wiring and name-prefix contracts in Python. The suite asserts the
loader is behaviour-preserving and that the config stays internally consistent.
"""

import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

import scrape_jobs as S

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "sources.json"


@pytest.fixture(scope="module")
def raw_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_config_file_exists_and_parses(raw_config):
    assert isinstance(raw_config, dict)
    assert isinstance(raw_config.get("sources"), list) and raw_config["sources"]
    assert isinstance(raw_config.get("keyword_sets"), dict)


def test_loaded_sources_have_expected_shape():
    assert S.SOURCES, "SOURCES must not be empty"
    for src in S.SOURCES:
        assert set(src) >= {"name", "type", "url", "region"}
        assert src["name"]
        assert src["type"] in S._KNOWN_SOURCE_TYPES
        assert src["url"].startswith("http")


def test_source_names_are_unique():
    names = [s["name"] for s in S.SOURCES]
    assert len(names) == len(set(names))


def test_keyword_set_references_resolve(raw_config):
    keyword_sets = raw_config["keyword_sets"]
    for entry in raw_config["sources"]:
        ks = entry.get("keyword_set")
        if ks is not None:
            assert ks in keyword_sets, f"{entry['name']} refs unknown keyword_set {ks!r}"


def test_linkedin_urls_encode_keywords_location_start(raw_config):
    keyword_sets = raw_config["keyword_sets"]
    by_name = {s["name"]: s for s in S.SOURCES}
    for entry in raw_config["sources"]:
        if entry.get("type") != "linkedin_api" or entry.get("url"):
            continue
        keywords = entry.get("keywords") or keyword_sets[entry["keyword_set"]]
        expected = S._LINKEDIN_GUEST_SEARCH + urlencode({
            "keywords": keywords,
            "location": entry.get("location", ""),
            "start": str(entry.get("start", 0)),
        })
        assert by_name[entry["name"]]["url"] == expected


def test_google_proxy_urls_encode_query_num(raw_config):
    by_name = {s["name"]: s for s in S.SOURCES}
    for entry in raw_config["sources"]:
        if entry.get("type") not in {"google_proxy", "search_proxy"} or entry.get("url"):
            continue
        expected = S._GOOGLE_SEARCH + urlencode({
            "q": entry["query"],
            "num": str(entry.get("num", 20)),
        })
        assert by_name[entry["name"]]["url"] == expected


def test_html_and_json_sources_use_literal_url(raw_config):
    by_name = {s["name"]: s for s in S.SOURCES}
    for entry in raw_config["sources"]:
        if entry.get("type") in {"html", "json_api", "rss"}:
            assert entry.get("url"), f"{entry['name']} ({entry['type']}) needs explicit url"
            assert by_name[entry["name"]]["url"] == entry["url"]


def test_fallbacks_structure():
    assert isinstance(S.SOURCE_URL_FALLBACKS, dict)
    for name, alts in S.SOURCE_URL_FALLBACKS.items():
        assert isinstance(alts, list) and alts
        assert all(isinstance(u, str) and u.startswith("http") for u in alts)


def test_loader_rejects_unknown_type(tmp_path):
    bad = {"sources": [{"name": "x", "type": "bogus", "url": "http://x"}]}
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown type"):
        S._load_source_config(str(p))


def test_loader_rejects_missing_name(tmp_path):
    bad = {"sources": [{"type": "html", "url": "http://x"}]}
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'name'"):
        S._load_source_config(str(p))


def test_loader_rejects_duplicate_name(tmp_path):
    bad = {"sources": [
        {"name": "dup", "type": "html", "url": "http://x"},
        {"name": "dup", "type": "html", "url": "http://y"},
    ]}
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate source name"):
        S._load_source_config(str(p))


def test_loader_rejects_unresolved_keyword_set(tmp_path):
    bad = {
        "keyword_sets": {},
        "sources": [{"name": "li", "type": "linkedin_api",
                     "keyword_set": "nope", "location": "Austria", "start": 0}],
    }
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keyword_set"):
        S._load_source_config(str(p))


def test_loader_rejects_google_without_query(tmp_path):
    bad = {"sources": [{"name": "g", "type": "google_proxy", "num": 20}]}
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="missing 'query'"):
        S._load_source_config(str(p))
