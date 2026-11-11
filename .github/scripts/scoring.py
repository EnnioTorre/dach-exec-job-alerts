"""
Deterministic ranking scorer, shared by the final rank (``rank_jobs``) and the
score-and-pool stage (``pool_jobs``) so every row is scored by one formula.

``score_job`` = 0.35·location-distance + 0.35·language + 0.30·IT-management
focus, clamped to [1, 5]. Location distance uses the row's precomputed coords
when present, else a Nominatim geocode of the location string (bounded, cached,
rate-limited, fully degrading).
"""

import json
import math
import os
import time

import position_filter
import requests
from text_match import _contains_any

# ---------------------------------------------------------------------------
# Language priority (0–5 scale, used by `language_score`)
#   English → high (primary target audience)
#   German  → medium (acceptable)
#   rest    → low (any other language)
# ---------------------------------------------------------------------------
LANGUAGE_SCORE_HIGH = 5.0
LANGUAGE_SCORE_MEDIUM = 3.0
LANGUAGE_SCORE_LOW = 1.2

# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

_VIENNA = ["vienna", "wien", "1010", "1020", "1030", "1040", "1050",
           "1060", "1070", "1080", "1090", "1100", "1110", "1120",
           "1130", "1140", "1150", "1160", "1170", "1180", "1190",
           "1200", "1210", "1220", "1230"]

_DACH_LOCATION_POINTS: dict[str, tuple[float, float]] = {
    "vienna": (48.2082, 16.3738),
    "wien": (48.2082, 16.3738),
    "graz": (47.0707, 15.4395),
    "linz": (48.3069, 14.2858),
    "salzburg": (47.8095, 13.0550),
    "innsbruck": (47.2692, 11.4041),
    "klagenfurt": (46.6247, 14.3053),
    "villach": (46.6103, 13.8558),
    "berlin": (52.5200, 13.4050),
    "munich": (48.1351, 11.5820),
    "münchen": (48.1351, 11.5820),
    "frankfurt": (50.1109, 8.6821),
    "hamburg": (53.5511, 9.9937),
    "cologne": (50.9375, 6.9603),
    "köln": (50.9375, 6.9603),
    "düsseldorf": (51.2277, 6.7735),
    "stuttgart": (48.7758, 9.1829),
    "leipzig": (51.3397, 12.3731),
    "zurich": (47.3769, 8.5417),
    "zürich": (47.3769, 8.5417),
    "basel": (47.5596, 7.5886),
    "bern": (46.9480, 7.4474),
    "geneva": (46.2044, 6.1432),
    "genf": (46.2044, 6.1432),
    "lausanne": (46.5197, 6.6323),
    # South Tyrol (German-speaking northern Italy) — ~400 km from Vienna.
    "bolzano": (46.4983, 11.3548),
    "bozen": (46.4983, 11.3548),
}

_COUNTRY_DISTANCE_FALLBACK = {
    "at": 280.0,
    "de": 560.0,
    "ch": 700.0,
    "it": 400.0,
    "dach": 500.0,
}

_AUSTRIA_HINTS = ["austria", "österreich", ".at"]
_GERMANY_HINTS = ["germany", "deutschland", ".de"]
_SWITZERLAND_HINTS = ["switzerland", "schweiz", ".ch"]
_ITALY_HINTS = ["italy", "italia", "south tyrol", "südtirol", "alto adige", ".it"]

# Bare location strings — only a country/region-less marker. These keep the
# country/dach fallback and are never geocoded (saves calls and preserves the
# existing fallback semantics).
_BARE_LOCATIONS = {
    "", "austria", "österreich", "osterreich", "germany", "deutschland",
    "switzerland", "schweiz", "italy", "italia", "dach", "remote",
    "remote (dach)", "at", "de", "ch", "it", ".at", ".de", ".ch", ".it",
}


def _is_bare_country_location(location: str) -> bool:
    """True when the location carries no specific place (country/marker only)."""
    return (location or "").strip().lower() in _BARE_LOCATIONS


# Geocoding resolves a specific city/region string to coordinates via
# OpenStreetMap Nominatim so distance-from-Vienna reflects true proximity. On by
# default; set RANK_GEOCODE=false to disable (exact pre-geocoding behaviour).
# Bounded per run, cached per location, spaced to honour the OSM usage policy
# (<=1 req/s), and fully degrading — any failure returns None.
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_DEFAULT_GEOCODE_UA = (
    "dach-exec-job-alerts/1.0 (+https://github.com/EnnioTorre/dach-exec-job-alerts)"
)
_GEOCODE_CACHE: dict[str, tuple[float, float] | None] = {}
_geocode_used = 0
_last_geocode_ts = 0.0


def _geocoding_enabled() -> bool:
    return os.getenv("RANK_GEOCODE", "true").strip().lower() not in {"0", "false", "no"}


def _geocode_query(location: str) -> tuple[float, float] | None:
    """One Nominatim lookup. Returns (lat, lon), or None on any failure."""
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={
                "q": location,
                "format": "json",
                "limit": 1,
                "countrycodes": "at,de,ch,it",
                "accept-language": "de",
            },
            headers={"User-Agent": os.getenv("RANK_GEOCODE_USER_AGENT", _DEFAULT_GEOCODE_UA)},
            timeout=(3.05, 10),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None
        return (float(data[0]["lat"]), float(data[0]["lon"]))
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"  geocode failed for {location!r}: {exc}")
        return None


def _geocode(location: str) -> tuple[float, float] | None:
    """Cached, budgeted, rate-limited geocoding. None when disabled/exhausted/failed."""
    key = (location or "").strip().lower()
    if not key:
        return None
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    if not _geocoding_enabled():
        return None

    global _geocode_used, _last_geocode_ts
    if _geocode_used >= int(os.getenv("RANK_GEOCODE_MAX", "50")):
        return None

    # Honour Nominatim's <=1 req/s usage policy.
    elapsed = time.monotonic() - _last_geocode_ts
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    _geocode_used += 1
    _last_geocode_ts = time.monotonic()
    coords = _geocode_query(location)
    _GEOCODE_CACHE[key] = coords
    return coords


def _geocode_cache_path() -> str:
    return os.getenv("RANK_GEOCODE_CACHE_PATH", "/tmp/jobs/geocode_cache.json")


def _load_geocode_cache() -> None:
    """Load the persisted geocode cache so the validation loop and re-rank reuse
    coordinates instead of re-querying Nominatim — this keeps scores consistent
    across passes, bounds the aggregate request rate, and removes the
    order-dependence of per-process budget truncation."""
    path = _geocode_cache_path()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            if (isinstance(v, list) and len(v) == 2
                    and all(isinstance(x, (int, float)) for x in v)):
                _GEOCODE_CACHE[k] = (float(v[0]), float(v[1]))
            else:
                _GEOCODE_CACHE[k] = None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass


def _save_geocode_cache() -> None:
    path = _geocode_cache_path()
    if not path:
        return
    try:
        serializable = {k: list(v) if v else None for k, v in _GEOCODE_CACHE.items()}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(serializable, f)
    except OSError:
        pass


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _distance_from_vienna_km(location: str) -> float | None:
    loc = (location or "").lower()
    if not loc:
        return None
    # The all-placeholder location ("—, —, —") has nothing to geocode.
    if not any(c.isalnum() for c in loc):
        return None

    vienna = _DACH_LOCATION_POINTS["vienna"]
    for token, (lat, lon) in _DACH_LOCATION_POINTS.items():
        if token in loc:
            return _haversine_km(vienna[0], vienna[1], lat, lon)

    # Geocode a specific (non-bare) string to real coordinates before falling
    # back to the coarse country constant.
    if not _is_bare_country_location(loc):
        coords = _geocode(location)
        if coords:
            return _haversine_km(vienna[0], vienna[1], coords[0], coords[1])

    if "remote" in loc or "dach" in loc:
        return _COUNTRY_DISTANCE_FALLBACK["dach"]
    if any(k in loc for k in _AUSTRIA_HINTS):
        return _COUNTRY_DISTANCE_FALLBACK["at"]
    if any(k in loc for k in _GERMANY_HINTS):
        return _COUNTRY_DISTANCE_FALLBACK["de"]
    if any(k in loc for k in _SWITZERLAND_HINTS):
        return _COUNTRY_DISTANCE_FALLBACK["ch"]
    if any(k in loc for k in _ITALY_HINTS):
        return _COUNTRY_DISTANCE_FALLBACK["it"]
    return None


def _distance_to_score(d: float | None) -> float:
    if d is None:
        return 2.4
    if d <= 25:
        return 5.0
    if d <= 100:
        return 4.7
    if d <= 250:
        return 4.2
    if d <= 450:
        return 3.5
    if d <= 700:
        return 2.8
    if d <= 900:
        return 2.2
    return 1.8


def vienna_distance_score(location: str) -> float:
    return _distance_to_score(_distance_from_vienna_km(location))


def _location_distance_score(job: dict) -> float:
    """Distance sub-score from the row's precomputed coords when present (so two
    spellings of a place score identically), else the location-string fallback.
    Tolerates a malformed/short/non-numeric coords by degrading to the string path."""
    coords = job.get("coords")
    if isinstance(coords, (list, tuple)) and len(coords) == 2:
        try:
            v = _DACH_LOCATION_POINTS["vienna"]
            return _distance_to_score(_haversine_km(v[0], v[1], float(coords[0]), float(coords[1])))
        except (TypeError, ValueError):
            pass
    return _distance_to_score(_distance_from_vienna_km(job.get("location", "")))


def language_score(language_hint: str, company: str = "") -> float:
    """
    Score a posting by its language: English (target audience) ranks high,
    German ranks medium, and any other language ("rest") ranks low.

    ``company`` is accepted for backward compatibility but no longer affects
    the score — the language policy is now a simple three-way mapping.
    """
    lang = (language_hint or "").strip().lower()
    if lang == "en":
        return LANGUAGE_SCORE_HIGH
    if lang == "de":
        return LANGUAGE_SCORE_MEDIUM
    return LANGUAGE_SCORE_LOW


def it_management_focus_score(title: str) -> float:
    """
    Score how closely a title matches IT *management / leadership* focus.

    The digest targets leadership roles (Engineering/Program Manager, Lead,
    Director, Head of…, VP, CTO), so any management title outranks every
    individual-contributor title on this axis.

    5.0: IT/software leadership (management + explicit software/IT signal)
    4.0: management + generic engineering (IT implied by the relevance gate)
    2.5: IT individual contributor (Senior/Platform/Cloud Engineer, …) — demoted
    2.0: generic engineering individual contributor
    1.5: management title without any engineering/IT signal
    1.0: explicitly non-IT indicators
    """
    t = title.lower()
    pf = position_filter.POSITION_FILTER

    if _contains_any(t, pf["excluded_areas"]) or _contains_any(t, pf["non_it_excluded"]):
        return 1.0

    has_it_signal = _contains_any(t, pf["it_signal_terms"])
    has_generic_eng = _contains_any(t, pf["generic_engineering_terms"])
    has_management = _contains_any(t, pf["management_terms"])

    # Management / leadership roles always rank above individual contributors.
    if has_management and has_it_signal:
        return 5.0
    if has_management and has_generic_eng:
        return 4.0
    # Individual-contributor roles are demoted below every leadership role.
    if has_it_signal:
        return 2.5
    if has_generic_eng:
        return 2.0
    if has_management:
        return 1.5
    return 1.0


def score_job(job: dict) -> float:
    ls = _location_distance_score(job)
    lang = language_score(job.get("language_hint", ""))
    focus = it_management_focus_score(job.get("title", ""))
    # Weighted formula: distance from Vienna 35%, language 35%,
    # IT relevance 30%. (Salary was dropped: no active source populates it.)
    raw = 0.35 * ls + 0.35 * lang + 0.30 * focus
    return round(max(1.0, min(5.0, raw)), 1)
