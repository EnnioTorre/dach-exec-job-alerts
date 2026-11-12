"""
Loader + load-time validation for the external position-filter config
(.github/config/position_filter.json).

Mirrors the sources.json / `scrape_jobs._load_source_config` pattern: the role
rules (which titles qualify, which are excluded) and the search keyword sets live
in JSON so the maintainer can tune the focus without editing Python, and an
invalid edit fails the run loudly rather than silently publishing an unfiltered
or empty digest. Only the RULE CONTENT is external; the filter logic that uses it
stays in Python (rank_jobs.py).
"""

import json
import os

# Location of the external position-filter registry. Overridable via env for
# tests / alternate rule files.
POSITION_FILTER_CONFIG_PATH = os.getenv(
    "POSITION_FILTER_CONFIG",
    os.path.join(os.path.dirname(__file__), "..", "config", "position_filter.json"),
)

# Role-rule lists that must each be a non-empty list of strings.
_REQUIRED_LIST_FIELDS = (
    "in_focus_roles",
    "domain_terms",
    "excluded_areas",
    "non_it_excluded",
    "management_terms",
    "individual_contributor_markers",
    "it_signal_terms",
    "generic_engineering_terms",
    "junior_markers",
    "commercial_terms",
    "off_focus_roles",
    "weak_body_terms",
    "hardware_body_terms",
    "it_industries",
    "excluded_seniority",
)
# Full set of recognised top-level fields (lists + the search keyword object).
_ALLOWED_FIELDS = frozenset(_REQUIRED_LIST_FIELDS + ("search_keyword_sets",))


def load_position_filter(path: str = POSITION_FILTER_CONFIG_PATH) -> dict:
    """
    Load and validate the external position-filter config.

    Returns the validated config dict. Raises ValueError with a clear,
    path-prefixed message on any malformed config so the workflow fails fast
    rather than filtering with a broken or empty rule set.
    """
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: config must be a JSON object")

    unexpected = sorted(set(cfg) - _ALLOWED_FIELDS)
    if unexpected:
        raise ValueError(f"{path}: unexpected field(s): {unexpected}")

    for field in _REQUIRED_LIST_FIELDS:
        if field not in cfg:
            raise ValueError(f"{path}: missing required field {field!r}")
        value = cfg[field]
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise ValueError(f"{path}: {field!r} must be a list of strings")
        if not value:
            raise ValueError(f"{path}: {field!r} must be a non-empty list")

    sks = cfg.get("search_keyword_sets")
    if not isinstance(sks, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in sks.items()
    ):
        raise ValueError(f"{path}: 'search_keyword_sets' must be an object of string->string")

    # Focus cross-check: every search set must target the role focus — carry no
    # excluded-area term and at least one in-focus role (case-insensitive
    # substring, matching runtime filtering). Keeps the searches aligned with the
    # filter that judges their results (BAC-6). off_focus_roles (Data/AI, security)
    # count as excluded here too, so a search set may not target them.
    in_focus_lc = [t.lower() for t in cfg["in_focus_roles"]]
    excluded_lc = [
        t.lower()
        for t in cfg["excluded_areas"] + cfg["non_it_excluded"] + cfg["off_focus_roles"]
    ]
    for name, query in sks.items():
        q = query.lower()
        bad = next((e for e in excluded_lc if e in q), None)
        if bad:
            raise ValueError(
                f"{path}: search_keyword_sets[{name!r}] targets excluded area {bad!r}"
            )
        if not any(role in q for role in in_focus_lc):
            raise ValueError(
                f"{path}: search_keyword_sets[{name!r}] targets no in-focus role"
            )

    # A term cannot be both in-focus and excluded — that is a contradiction the
    # maintainer must resolve. Compared case-insensitively, matching the
    # case-insensitive filtering at runtime. off_focus_roles is an exclusion list,
    # so an in-focus role may not also appear there (e.g. "head of data").
    in_focus = {t.lower() for t in cfg["in_focus_roles"]}
    excluded = (
        {t.lower() for t in cfg["excluded_areas"]}
        | {t.lower() for t in cfg["non_it_excluded"]}
        | {t.lower() for t in cfg["off_focus_roles"]}
    )
    overlap = sorted(in_focus & excluded)
    if overlap:
        raise ValueError(
            f"{path}: term(s) appear in both in-focus and excluded lists: {overlap}"
        )

    return cfg


# Eager, fail-fast load of the default config at import, mirroring
# `SOURCES = _load_source_config()` in scrape_jobs.
POSITION_FILTER = load_position_filter()
