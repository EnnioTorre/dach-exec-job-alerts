"""
Deterministic ranking scorer, shared by the final rank (``rank_jobs``) and the
score-and-pool stage (``pool_jobs``) so every row is scored by one formula.

``score_job`` = 0.35·location-distance + 0.35·language + 0.30·IT-management
focus, clamped to [1, 5]. Location distance is the great-circle distance from a
geolocated Vienna origin to the row's geolocated coordinates — the row's
precomputed coords when present, else a Nominatim geocode of the location string
(bounded, cached, rate-limited). A legitimate miss or disabled geocoding scores
neutral; a Nominatim outage fails the run rather than fabricating a value (ADR-0002).
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

# The distance axis is anchored on Vienna, resolved by geolocation (cached)
# rather than a hardcoded coordinate. Resolved once per process; later runs read
# it straight from the persistent geocode cache.
_VIENNA_PLACE = "Wien, Österreich"
_vienna_origin: tuple[float, float] | None = None


def _origin() -> tuple[float, float] | None:
    """Geolocated Vienna reference point (lat, lon), or None when unresolvable."""
    global _vienna_origin
    if _vienna_origin is None:
        _vienna_origin = _geocode(_VIENNA_PLACE)
    return _vienna_origin


# Geocoding resolves a location string to coordinates via OpenStreetMap Nominatim
# so distance-from-Vienna reflects true proximity. On by default; set
# RANK_GEOCODE=false to disable. Bounded per run, cached per location, spaced to
# honour the OSM usage policy (<=1 req/s). Per ADR-0002 a transport/service
# failure is retried and then RAISED (the run fails loudly) rather than
# fabricating a value; a legitimate miss (200 + no match), operator-disable, or an
# exhausted budget returns None (an honest "unknown", scored neutral).
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_DEFAULT_GEOCODE_UA = (
    "dach-exec-job-alerts/1.0 (+https://github.com/EnnioTorre/dach-exec-job-alerts)"
)
_GEOCODE_MAX_ATTEMPTS = 3
_GEOCODE_CACHE: dict[str, tuple[float, float] | None] = {}
_geocode_used = 0
_last_geocode_ts = 0.0


class GeocodeUnavailable(RuntimeError):
    """Nominatim failed to respond after retries. Per ADR-0002 the run fails loud
    rather than fabricating a fallback coordinate."""


def _geocoding_enabled() -> bool:
    return os.getenv("RANK_GEOCODE", "true").strip().lower() not in {"0", "false", "no"}


def _geocode_budget() -> int:
    # Per-stage budget: the score+pool stage caps itself via POOL_GEOCODE_MAX so it
    # never doubles the ranker's Nominatim volume; falls back to RANK_GEOCODE_MAX,
    # then 50. Non-numeric -> 50 (never raises). Mirrors locations._location_geocode_budget.
    raw = os.getenv("POOL_GEOCODE_MAX") or os.getenv("RANK_GEOCODE_MAX") or "50"
    try:
        return int(raw)
    except ValueError:
        return 50


def _geocode_query(location: str) -> tuple[float, float] | None:
    """One Nominatim lookup. Returns (lat, lon); None for a legitimate miss (200 +
    no match, or a non-429 4xx). Raises GeocodeUnavailable on a transient/service
    failure (request error, 429, 5xx, or a malformed 200 body) so the caller can
    retry and ultimately fail the run (ADR-0002)."""
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
    except requests.RequestException as exc:
        raise GeocodeUnavailable(f"nominatim request failed for {location!r}: {exc}") from exc

    if resp.status_code == 429 or resp.status_code >= 500:
        raise GeocodeUnavailable(f"nominatim HTTP {resp.status_code} for {location!r}")
    if resp.status_code != 200:
        return None  # other 4xx: a client error, not a transient outage
    try:
        data = resp.json()
    except ValueError as exc:
        raise GeocodeUnavailable(f"nominatim invalid JSON for {location!r}: {exc}") from exc
    if not isinstance(data, list):
        raise GeocodeUnavailable(f"nominatim unexpected payload for {location!r}")
    if not data:
        return None  # legitimate miss
    try:
        return (float(data[0]["lat"]), float(data[0]["lon"]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise GeocodeUnavailable(f"nominatim malformed entry for {location!r}: {exc}") from exc


def _geocode(location: str) -> tuple[float, float] | None:
    """Cached, budgeted, rate-limited geocoding. Returns None when disabled, the
    per-run budget is spent, or the location is a legitimate miss. Retries a
    transient Nominatim failure and then raises GeocodeUnavailable (ADR-0002)."""
    key = (location or "").strip().lower()
    if not key:
        return None
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]
    if not _geocoding_enabled():
        return None

    global _geocode_used, _last_geocode_ts
    if _geocode_used >= _geocode_budget():
        return None

    _geocode_used += 1
    last_exc: GeocodeUnavailable | None = None
    for attempt in range(_GEOCODE_MAX_ATTEMPTS):
        # Honour Nominatim's <=1 req/s usage policy, on every (re)try.
        elapsed = time.monotonic() - _last_geocode_ts
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _last_geocode_ts = time.monotonic()
        try:
            coords = _geocode_query(location)
        except GeocodeUnavailable as exc:
            last_exc = exc
            print(f"  geocode attempt {attempt + 1} failed for {location!r}: {exc}")
            continue
        _GEOCODE_CACHE[key] = coords
        return coords

    raise GeocodeUnavailable(
        f"geocoding unavailable for {location!r} after {_GEOCODE_MAX_ATTEMPTS} attempts"
    ) from last_exc


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
    """Great-circle km from the geolocated Vienna origin to the geolocated
    location string. None when either endpoint cannot be resolved."""
    loc = (location or "").strip()
    # A blank or all-placeholder location ("—, —, —") has nothing to geolocate.
    if not loc or not any(c.isalnum() for c in loc):
        return None
    origin = _origin()
    coords = _geocode(location)
    if origin is None or coords is None:
        return None
    return _haversine_km(origin[0], origin[1], coords[0], coords[1])


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
    """Distance sub-score from the row's geolocated coords when present (so two
    spellings of a place score identically), else by geolocating the location
    string. Degrades to neutral when coordinates cannot be resolved."""
    origin = _origin()
    coords = job.get("coords")
    if origin is not None and isinstance(coords, (list, tuple)) and len(coords) == 2:
        try:
            return _distance_to_score(
                _haversine_km(origin[0], origin[1], float(coords[0]), float(coords[1]))
            )
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
