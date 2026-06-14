"""Orchestration: discover destinations, fetch weekend-dated fares,
filter by budget + weekend pattern + nearness, annotate visa, dedupe."""
from __future__ import annotations
from datetime import datetime, date, timedelta

from . import airports, visa

_WD = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _months_ahead(weeks):
    """Set of YYYY-MM strings covering today .. today+weeks."""
    today = date.today()
    end = today + timedelta(weeks=weeks)
    out, cur = [], date(today.year, today.month, 1)
    while cur <= end:
        out.append(cur.strftime("%Y-%m"))
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    return out


def _parse(dt):
    if not dt:
        return None
    try:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(dt[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _is_weekend_trip(offer, cfg):
    dep = _parse(offer.get("departure_at"))
    ret = _parse(offer.get("return_at"))
    if dep is None or ret is None:
        return False
    depart_days = {_WD[d] for d in cfg["weekend"]["depart_days"]}
    return_days = {_WD[d] for d in cfg["weekend"]["return_days"]}
    if dep.weekday() not in depart_days or ret.weekday() not in return_days:
        return False
    nights = (ret.date() - dep.date()).days
    if not (cfg["weekend"]["min_nights"] <= nights <= cfg["weekend"]["max_nights"]):
        return False
    horizon = date.today() + timedelta(weeks=cfg["window_weeks"])
    return date.today() <= dep.date() <= horizon


def _is_nearby(origin, dest, cfg):
    dn = airports.get(dest)
    if dn is None:
        return False
    deny = {c.upper() for c in cfg.get("exclude_countries", [])}
    if dn["iso3"] in deny:
        return False
    allow = {c.upper() for c in cfg.get("include_countries", [])}
    if dn["iso3"] in allow:                       # always-allow list wins
        return True
    dist = airports.distance_from(origin, dest)
    if dist is None:
        return False
    return dist <= cfg["max_distance_km"]


def find_deals(provider, cfg, log=print):
    origin = cfg["origin"].upper()
    nationality = cfg["nationality"].upper()
    budget = cfg["max_price"]
    currency = cfg.get("currency", "sar")
    market = cfg.get("market", "sa")

    # `max_hours` is the friendlier knob: how long you're willing to fly. We
    # turn it into a great-circle distance proxy (~800 km/h block speed) that
    # the nearness filter understands. Falls back to max_distance_km.
    if cfg.get("max_hours"):
        cfg = {**cfg, "max_distance_km": int(cfg["max_hours"] * 800)}
        log(f"Reach: ~{cfg['max_hours']}h flight (≈{cfg['max_distance_km']} km) from {origin}")

    log(f"Discovering destinations from {origin} ...")
    try:
        candidates = provider.discover(origin, currency, market)
    except Exception as e:
        log(f"  discovery failed ({e}); falling back to configured destinations")
        candidates = []
    # union with any explicitly configured destinations
    candidates = list(dict.fromkeys(candidates + cfg.get("destinations", [])))
    # destinations you list explicitly are always honored; the distance filter
    # only prunes auto-discovered candidates.
    configured = {c.upper() for c in cfg.get("destinations", [])}
    nearby = [c for c in candidates if c.upper() != origin
              and (c.upper() in configured or _is_nearby(origin, c, cfg))]
    nearby = nearby[: cfg.get("max_destinations", 30)]
    log(f"  {len(candidates)} reachable, {len(nearby)} to check "
        f"({len(configured)} explicit + within range)")

    months = _months_ahead(cfg["window_weeks"])
    deals = []
    seen = set()
    for i, dest in enumerate(nearby, 1):
        log(f"  [{i}/{len(nearby)}] checking {dest} ({airports.city_name(dest)}) ...")
        found_here = 0
        for month in months:
            try:
                offers = provider.dated_offers(origin, dest, month, currency, market,
                                               direct=cfg.get("direct_only", False))
            except Exception as e:
                log(f"      {dest} {month}: {e}")
                continue
            for o in offers:
                if o["price"] > budget:
                    continue
                if not _is_weekend_trip(o, cfg):
                    continue
                dkey = (o["origin"], o["destination"], o["departure_at"], o["return_at"])
                if dkey in seen:
                    continue
                seen.add(dkey)
                v = visa.check(nationality, airports.country_iso3(o["destination"]))
                if cfg.get("easy_visa_only") and not v["easy"]:
                    continue
                o.update({
                    "city": airports.city_name(o["destination"]),
                    "country": airports.country_name(o["destination"]),
                    "distance_km": airports.distance_from(origin, o["destination"]),
                    "visa": v,
                })
                deals.append(o)
                found_here += 1
        log(f"      {found_here} match(es) under budget")

    # Surface data-source trouble: a broken/blocked scraper otherwise looks
    # identical to "no deals found".
    stats = getattr(provider, "stats", None)
    if stats and stats.get("errors"):
        log(f"  ⚠ data source failed on {stats['errors']}/{stats['queries']} "
            f"queries — results may be incomplete (last: {stats.get('last_error')})")

    deals.sort(key=lambda x: x["price"])
    return deals
