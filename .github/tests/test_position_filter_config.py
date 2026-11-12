"""
Tests for the externalised position-filter config (.github/config/position_filter.json)
and its loader in position_filter.py.

These guard the refactor that moved the role rules (which titles qualify, which
are excluded, the IT signals, and the search keyword sets) out of Python and into
JSON. The suite asserts the shipped config loads and that the loader fails loudly
on an invalid config, so a bad edit can never silently publish an unfiltered or
empty digest. `load_position_filter` is pure (reads only the given file).
"""

import json

import position_filter as P
import pytest


def _valid_config() -> dict:
    """A minimal, internally-consistent config used as a base for the bad-config cases."""
    return {
        "in_focus_roles": ["cto", "head of engineering"],
        "domain_terms": ["cloud"],
        "excluded_areas": ["sales"],
        "non_it_excluded": ["mechanical"],
        "management_terms": ["head"],
        "individual_contributor_markers": ["software engineer"],
        "it_signal_terms": ["software"],
        "generic_engineering_terms": ["engineer"],
        "junior_markers": ["junior"],
        "commercial_terms": ["deal"],
        "off_focus_roles": ["head of data"],
        "weak_body_terms": ["technology"],
        "hardware_body_terms": ["semiconductor"],
        "it_industries": ["Software Development"],
        "excluded_seniority": ["Entry level"],
        "search_keyword_sets": {"leader": "CTO OR VP Engineering"},
    }


def test_loads_valid_position_filter_config():
    # The real shipped config loads and has the expected role-rule structure.
    cfg = P.load_position_filter()
    for field in P._REQUIRED_LIST_FIELDS:
        assert isinstance(cfg[field], list)
        assert all(isinstance(t, str) for t in cfg[field])
    assert cfg["in_focus_roles"], "in_focus_roles must not be empty"
    assert isinstance(cfg["search_keyword_sets"], dict)
    assert all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in cfg["search_keyword_sets"].items()
    )


def test_invalid_config_missing_in_focus_raises(tmp_path):
    bad = _valid_config()
    bad["in_focus_roles"] = []
    p = tmp_path / "position_filter.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="in_focus_roles"):
        P.load_position_filter(str(p))


def test_invalid_config_unknown_field_raises(tmp_path):
    bad = _valid_config()
    bad["bogus_field"] = ["x"]
    p = tmp_path / "position_filter.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected field"):
        P.load_position_filter(str(p))


def test_term_in_both_in_focus_and_excluded_raises(tmp_path):
    bad = _valid_config()
    bad["in_focus_roles"] = ["cto", "sales"]
    bad["excluded_areas"] = ["sales"]
    p = tmp_path / "position_filter.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="both in-focus and excluded"):
        P.load_position_filter(str(p))


def test_invalid_config_empty_role_list_raises(tmp_path):
    # An empty non-in_focus list (here excluded_areas) is also rejected: an
    # empty exclude list would un-filter the digest and neuter the overlap guard.
    bad = _valid_config()
    bad["excluded_areas"] = []
    p = tmp_path / "position_filter.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a non-empty list"):
        P.load_position_filter(str(p))


def test_cross_check_rejects_off_focus_search_source(tmp_path):
    # A search set aimed at an excluded area fails the focus cross-check ...
    bad = _valid_config()
    bad["search_keyword_sets"] = {"off": "Head of Sales OR CTO"}
    p = tmp_path / "position_filter.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="targets excluded area"):
        P.load_position_filter(str(p))

    # ... and a set with no in-focus role also fails.
    bad["search_keyword_sets"] = {"off": "Program Manager OR Innovation Lead"}
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="no in-focus role"):
        P.load_position_filter(str(p))


# --- Classification rule lists (it-role-classification, Story 1) ----------------

_NEW_CLASSIFICATION_LISTS = (
    "junior_markers",
    "commercial_terms",
    "off_focus_roles",
    "weak_body_terms",
    "hardware_body_terms",
    "it_industries",
    "excluded_seniority",
)


def test_loads_config_with_new_classification_lists():
    # The shipped config exposes every new classification rule list as a
    # non-empty list of strings, so the later rank/scrape stages can read them.
    cfg = P.load_position_filter()
    for field in _NEW_CLASSIFICATION_LISTS:
        assert field in cfg, f"{field} missing from shipped config"
        assert cfg[field], f"{field} must not be empty"
        assert all(isinstance(t, str) for t in cfg[field])


def test_missing_new_list_field_raises(tmp_path):
    # Dropping a required new list fails the load, so a half-configured rule set
    # can never silently let non-IT roles through.
    bad = _valid_config()
    del bad["junior_markers"]
    p = tmp_path / "position_filter.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="junior_markers"):
        P.load_position_filter(str(p))


def test_term_in_both_in_focus_and_off_focus_raises(tmp_path):
    # off_focus_roles is an exclusion list: a term that is also in-focus is a
    # contradiction and must be rejected (e.g. a lingering "head of data").
    bad = _valid_config()
    bad["in_focus_roles"] = ["cto", "head of data"]
    bad["off_focus_roles"] = ["head of data"]
    p = tmp_path / "position_filter.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="both in-focus and excluded"):
        P.load_position_filter(str(p))


def test_head_of_data_removed_from_in_focus_and_transformation_set():
    # The shipped config must not list "head of data" as in-focus nor leave it in
    # the transformation search set — off-focus Data leadership stays excluded.
    cfg = P.load_position_filter()
    in_focus_lc = [t.lower() for t in cfg["in_focus_roles"]]
    assert "head of data" not in in_focus_lc
    assert "head of data" not in cfg["search_keyword_sets"]["transformation"].lower()
