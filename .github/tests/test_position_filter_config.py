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
