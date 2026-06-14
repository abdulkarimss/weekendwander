"""IATA airport lookup: country (ISO3), city name, and distance from origin."""
import json
import math
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data" / "airports.json"

with open(_DATA, encoding="utf-8") as _f:
    _AIRPORTS = json.load(_f)


def get(iata: str):
    """Return airport dict {city, country, iso3, lat, lon} or None."""
    return _AIRPORTS.get((iata or "").upper())


def all_iata():
    """Every known IATA code (providers without a discovery endpoint can
    propose these and let the finder apply the nearness filter)."""
    return list(_AIRPORTS.keys())


def country_iso3(iata: str):
    a = get(iata)
    return a["iso3"] if a else None


def city_name(iata: str):
    a = get(iata)
    return a["city"] if a else iata


def country_name(iata: str):
    a = get(iata)
    return a["country"] if a else iata


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def distance_from(origin_iata: str, dest_iata: str):
    """Great-circle km between two airports, or None if either is unknown."""
    o, d = get(origin_iata), get(dest_iata)
    if not o or not d:
        return None
    return haversine_km(o["lat"], o["lon"], d["lat"], d["lon"])
