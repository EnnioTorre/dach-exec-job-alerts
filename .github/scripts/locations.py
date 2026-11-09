"""
Shared location primitives for the DACH job pipeline.

Foundation for a standardized `place, region, country` location: a raw-string
cleaner (strips UI/junk text and rejects filter/facet lists), a Haversine helper,
an out-of-geography predicate, and a Nominatim `addressdetails` resolver that
turns a place into its region/country/coords (German-market local form). All
region/country/coords come from geocoding, not a bundled table.
See .sdlc/tasks/locations-gazetteer-cleaner/spec.md and
.sdlc/tasks/geocode-addressdetails/spec.md.
"""

import json
import math
import os
import time

import requests

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


# --- Nominatim addressdetails resolver (region/country/coords per place) -------
# Same public endpoint and usage-policy discipline as rank_jobs._geocode
# (ADR-0001), plus addressdetails=1 so the response carries address.state /
# address.country. Bounded per run, cached per place, spaced >=1 req/s, and fully
# degrading — any failure returns None. Its budget/cache are independent of the
# ranker's (separate pipeline stages, separate processes).
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_DEFAULT_GEOCODE_UA = (
    "dach-exec-job-alerts/1.0 (+https://github.com/EnnioTorre/dach-exec-job-alerts)"
)
_LOCATION_CACHE: dict[str, dict | None] = {}
_loc_geocode_used = 0
_last_loc_geocode_ts = 0.0


def _location_geocoding_enabled() -> bool:
    return os.getenv("RANK_GEOCODE", "true").strip().lower() not in {"0", "false", "no"}


def _to_german_market(address: dict) -> tuple[str, str]:
    """
    Map a Nominatim `address` object (fetched with accept-language=de) to
    (region, country) in German-market local form. South Tyrol (in Italy) is
    normalised to region 'Südtirol' so _is_out_of_geo accepts it.
    """
    country = (address.get("country") or "").strip() or PLACEHOLDER
    region = (address.get("state") or address.get("region") or "").strip() or PLACEHOLDER
    is_italy = country.lower() in {"italien", "italy"} or (address.get("country_code") or "").lower() == "it"
    if is_italy:
        markers = " ".join(
            str(address.get(k, "")) for k in ("state", "county", "ISO3166-2-lvl4", "ISO3166-2-lvl6")
        ).lower()
        if any(m in markers for m in ("südtirol", "bolzano", "bozen", "it-bz")):
            region = "Südtirol"
            country = "Italien"
    return region, country


def _geocode_address_query(place: str) -> dict | None:
    """One Nominatim addressdetails lookup. Returns {region, country, coords} or None."""
    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={
                "q": place,
                "format": "json",
                "limit": 1,
                "addressdetails": 1,
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
        top = data[0]
        region, country = _to_german_market(top.get("address") or {})
        return {"region": region, "country": country, "coords": (float(top["lat"]), float(top["lon"]))}
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"  address geocode failed for {place!r}: {exc}")
        return None


def _geocode_address(place: str) -> dict | None:
    """
    Cached, budgeted, rate-limited addressdetails resolver. Returns
    {region, country, coords} or None when disabled / budget-exhausted / failed.
    Never raises.
    """
    key = (place or "").strip().lower()
    if not key:
        return None
    if key in _LOCATION_CACHE:
        return _LOCATION_CACHE[key]
    if not _location_geocoding_enabled():
        return None

    global _loc_geocode_used, _last_loc_geocode_ts
    if _loc_geocode_used >= int(os.getenv("RANK_GEOCODE_MAX", "50")):
        return None

    # Honour Nominatim's <=1 req/s usage policy.
    elapsed = time.monotonic() - _last_loc_geocode_ts
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    _loc_geocode_used += 1
    _last_loc_geocode_ts = time.monotonic()
    record = _geocode_address_query(place)
    _LOCATION_CACHE[key] = record
    return record


def _location_cache_path() -> str:
    return os.getenv("LOCATION_CACHE_PATH", "/tmp/jobs/location_cache.json")


def load_location_cache() -> None:
    """Load the persisted per-place cache ({place: {region,country,lat,lon} | null})
    so repeated places cost no new Nominatim request across passes/runs. Tolerates a
    missing/malformed file (never raises); empty path disables disk I/O."""
    path = _location_cache_path()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            if isinstance(v, dict) and "lat" in v and "lon" in v:
                _LOCATION_CACHE[k] = {
                    "region": v.get("region", PLACEHOLDER),
                    "country": v.get("country", PLACEHOLDER),
                    "coords": (float(v["lat"]), float(v["lon"])),
                }
            else:
                _LOCATION_CACHE[k] = None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass


def save_location_cache() -> None:
    """Persist the per-place cache (hits + misses) to LOCATION_CACHE_PATH. Tolerates
    OSError; empty path is a no-op. Mirrors rank_jobs._save_geocode_cache."""
    path = _location_cache_path()
    if not path:
        return
    try:
        serializable: dict[str, dict | None] = {}
        for k, v in _LOCATION_CACHE.items():
            if v:
                lat, lon = v["coords"]
                serializable[k] = {"region": v["region"], "country": v["country"], "lat": lat, "lon": lon}
            else:
                serializable[k] = None
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(serializable, f)
    except OSError:
        pass
