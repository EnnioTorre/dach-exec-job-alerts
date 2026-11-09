"""
Shared location primitives for the DACH job pipeline.

Foundation for a standardized `place, region, country` location: a bundled DACH +
South-Tyrol gazetteer, a raw-string cleaner (strips UI/junk text and rejects
filter/facet lists), a Haversine helper, and an out-of-geography predicate. All
pure and offline — geocoding and `normalize_location()` are added by later
stories. See .sdlc/tasks/locations-gazetteer-cleaner/spec.md.
"""

import json
import math
import os
from pathlib import Path

# Em dash used for unknown location parts. Never matched as a place.
PLACEHOLDER = "—"

# A location string with more comma-separated parts than this is treated as a
# filter/facet list (e.g. "all Austrian states") rather than a posting's own
# location, and rejected. The string-level half of PRD open-decision #9; the
# element-scoped half (read the posting's own location element) is Story 4.
MAX_LOCATION_PARTS = 5

# UI/navigation phrases that boards glue onto the location text (digest #109,
# e.g. "… CASuche erweitern"). Stripped as case-insensitive substrings. Kept to
# multi-word phrases so a real place name is never partially stripped.
_JUNK_PHRASES = (
    "suche erweitern",
    "mehr anzeigen",
    "alle jobs",
    "jobs anzeigen",
    "zur suche",
)

# In-geography countries (German-market local form). South Tyrol is in Italy but
# accepted via its region (see _is_out_of_geo).
_DACH_COUNTRIES = {"österreich", "deutschland", "schweiz"}
_SOUTH_TYROL_REGION = "südtirol"

_GAZETTEER: dict[str, dict] | None = None


def _gazetteer_path() -> str:
    return os.getenv(
        "GAZETTEER_PATH",
        str(Path(__file__).resolve().parent.parent / "config" / "dach_gazetteer.json"),
    )


def load_gazetteer() -> dict[str, dict]:
    """
    Load the DACH + South-Tyrol gazetteer: lowercased place -> {region, country,
    lat, lon}. Cached after first load. Returns {} on any read/parse error
    (never raises). Keys prefixed with '_' (e.g. the file's `_comment`) are
    metadata and are skipped.
    """
    global _GAZETTEER
    if _GAZETTEER is not None:
        return _GAZETTEER
    try:
        with open(_gazetteer_path(), encoding="utf-8") as f:
            raw = json.load(f)
        _GAZETTEER = {
            k.strip().lower(): v
            for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict)
        }
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        _GAZETTEER = {}
    return _GAZETTEER


def clean_location_parts(raw: str) -> list[str]:
    """
    Turn a raw scraped location string into candidate place strings.

    Strips UI/junk phrases, splits on commas, drops empty/whitespace-only parts,
    and rejects the whole string (returns []) when it has more than
    MAX_LOCATION_PARTS parts — a filter/facet list, not a posting's location.
    Never raises.
    """
    text = (raw or "")
    low = text.lower()
    for phrase in _JUNK_PHRASES:
        idx = low.find(phrase)
        while idx != -1:
            text = text[:idx] + text[idx + len(phrase):]
            low = text.lower()
            idx = low.find(phrase)

    parts = [p.strip() for p in text.split(",")]
    parts = [p for p in parts if p]
    if len(parts) > MAX_LOCATION_PARTS:
        return []
    return parts


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _is_out_of_geo(country: str, region: str | None = None) -> bool:
    """
    True when a location is outside the searched geography. In-geography when the
    country is Österreich/Deutschland/Schweiz, or the region is Südtirol (South
    Tyrol, in Italy, is accepted).
    """
    if (country or "").strip().lower() in _DACH_COUNTRIES:
        return False
    if (region or "").strip().lower() == _SOUTH_TYROL_REGION:
        return False
    return True
