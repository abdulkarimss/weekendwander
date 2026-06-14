"""Flight price providers.

A provider returns a list of `Offer` dicts:
  {origin, destination, price, currency, departure_at (ISO),
   return_at (ISO or None), transfers, airline, link}

Default implementation: Travelpayouts / Aviasales Data API (v3).
Cache-based prices (last searches by Aviasales users), free affiliate token.
Get a token at: https://www.travelpayouts.com  ->  Tools -> API.

To add another source (e.g. Amadeus Self-Service), subclass BaseProvider
and implement discover() and dated_offers(); wire it in finder via config.
"""
from __future__ import annotations
import calendar
import time
from datetime import date, timedelta
from urllib.parse import quote_plus

import requests

from . import airports

AVIASALES_LINK = "https://www.aviasales.com"
GOOGLE_FLIGHTS_LINK = "https://www.google.com/travel/flights"

_WD = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class BaseProvider:
    def discover(self, origin, currency, market):
        """Return list of candidate destination IATA codes reachable from origin."""
        raise NotImplementedError

    def dated_offers(self, origin, destination, month, currency, market, direct=False):
        """Return list of Offer dicts for `destination` in `month` (YYYY-MM)."""
        raise NotImplementedError


class TravelpayoutsProvider(BaseProvider):
    BASE = "https://api.travelpayouts.com/aviasales/v3"

    def __init__(self, token, timeout=20, retries=2, pause=0.4):
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.pause = pause

    def _get(self, path, params):
        params = {**params, "token": self.token}
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.get(f"{self.BASE}/{path}", params=params,
                                 headers={"X-Access-Token": self.token},
                                 timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last = e
                time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"Travelpayouts request failed ({path}): {last}")

    def discover(self, origin, currency, market):
        """grouped_prices: cheapest fare per destination from origin."""
        data = self._get("grouped_prices", {
            "origin": origin, "currency": currency, "market": market,
            "grouping": "destination",
        })
        d = data.get("data") or {}
        return list(d.keys())

    def dated_offers(self, origin, destination, month, currency, market, direct=False):
        """prices_for_dates with departure_at as a month (YYYY-MM)."""
        data = self._get("prices_for_dates", {
            "origin": origin, "destination": destination,
            "departure_at": month, "currency": currency, "market": market,
            "sorting": "price", "unique": "false",
            "direct": "true" if direct else "false", "limit": 30, "one_way": "false",
        })
        rows = data.get("data") or []
        if isinstance(rows, dict):            # some endpoints group by code
            rows = list(rows.values())
        offers = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = row.get("price") or row.get("value")
            if price is None:
                continue
            link = row.get("link")
            offers.append({
                "origin": row.get("origin", origin),
                "destination": row.get("destination", destination),
                "price": float(price),
                "currency": (data.get("currency") or currency).upper(),
                "departure_at": row.get("departure_at"),
                "return_at": row.get("return_at"),
                "transfers": row.get("transfers", 0),
                "airline": row.get("airline", ""),
                "link": (AVIASALES_LINK + link) if link else None,
            })
        time.sleep(self.pause)            # be polite to the cache API
        return offers


class GoogleFlightsProvider(BaseProvider):
    """Google Flights via the unofficial `fast-flights` scraper (no API token).

    Google Flights has no public API and no "cheapest fare per destination"
    discovery, and it can only be searched for concrete date pairs. So:

      * discover() proposes every known airport, nearest-first, and lets the
        finder apply the distance/allow-list filter and the max_destinations
        cap — there is no network call here.
      * dated_offers() enumerates the weekend (depart/return) date pairs that
        fall inside `month`, queries each as a round-trip, and returns the
        cheapest itinerary per pair.

    Scraping Google is brittle and rate-limited, so this is best pointed at a
    short, explicit `destinations` list with a small `window_weeks`. Tune the
    request budget with `google.pairs_per_month` / `google.pause` in config.
    """

    def __init__(self, weekend, window_weeks, pairs_per_month=4, pause=1.0):
        self.weekend = weekend
        self.window_weeks = window_weeks
        self.pairs_per_month = pairs_per_month
        self.pause = pause
        # imported lazily so the dependency is only needed when this provider
        # is actually selected
        from fast_flights import get_flights, create_query, FlightQuery, Passengers
        self._get_flights = get_flights
        self._create_query = create_query
        self._FlightQuery = FlightQuery
        self._Passengers = Passengers

    def discover(self, origin, currency, market):
        origin = origin.upper()
        codes = [c for c in airports.all_iata() if c != origin]
        # nearest first so the finder's max_destinations cap keeps close ones
        codes.sort(key=lambda c: airports.distance_from(origin, c) or float("inf"))
        return codes

    def _weekend_pairs(self, month):
        """(depart_date, return_date) pairs inside `month` matching the
        configured weekend pattern and within the scan window."""
        depart_days = {_WD[d] for d in self.weekend["depart_days"]}
        return_days = {_WD[d] for d in self.weekend["return_days"]}
        lo, hi = self.weekend["min_nights"], self.weekend["max_nights"]
        today = date.today()
        horizon = today + timedelta(weeks=self.window_weeks)
        year, mon = (int(x) for x in month.split("-"))
        ndays = calendar.monthrange(year, mon)[1]
        pairs = []
        for day in range(1, ndays + 1):
            dep = date(year, mon, day)
            if dep < today or dep > horizon or dep.weekday() not in depart_days:
                continue
            for nights in range(lo, hi + 1):
                ret = dep + timedelta(days=nights)
                if ret.weekday() in return_days:
                    pairs.append((dep, ret))
        pairs.sort()
        return pairs[: self.pairs_per_month]

    def _query_pair(self, origin, dest, dep, ret, currency, direct):
        q = self._create_query(
            flights=[
                self._FlightQuery(date=dep.isoformat(), from_airport=origin, to_airport=dest),
                self._FlightQuery(date=ret.isoformat(), from_airport=dest, to_airport=origin),
            ],
            trip="round-trip", seat="economy",
            passengers=self._Passengers(adults=1),
            currency=currency.upper(),
            max_stops=0 if direct else None,
        )
        results = self._get_flights(q)
        if not results:
            return None
        best = min(results, key=lambda f: f.price)
        out = best.flights[0]                       # outbound first leg
        dep_dt = (f"{out.departure.date[0]:04d}-{out.departure.date[1]:02d}-"
                  f"{out.departure.date[2]:02d}T{out.departure.time[0]:02d}:"
                  f"{out.departure.time[1]:02d}:00")
        link = (f"{GOOGLE_FLIGHTS_LINK}?q=" +
                quote_plus(f"Flights from {origin} to {dest} on {dep} through {ret}"))
        return {
            "origin": origin,
            "destination": dest,
            "price": float(best.price),
            "currency": currency.upper(),
            "departure_at": dep_dt,
            "return_at": ret.isoformat(),         # Google only details the outbound leg
            "transfers": max(len(best.flights) - 1, 0),
            "airline": ", ".join(best.airlines) if best.airlines else "",
            "link": link,
        }

    def dated_offers(self, origin, destination, month, currency, market, direct=False):
        origin, destination = origin.upper(), destination.upper()
        offers = []
        for dep, ret in self._weekend_pairs(month):
            try:
                offer = self._query_pair(origin, destination, dep, ret, currency, direct)
            except Exception:
                offer = None                       # skip a flaky scrape, keep going
            if offer:
                offers.append(offer)
            time.sleep(self.pause)                 # be gentle with the scraper
        return offers


def build_provider(cfg):
    name = (cfg.get("provider") or "travelpayouts").lower()
    if name == "travelpayouts":
        token = cfg.get("travelpayouts_token")
        if not token:
            raise SystemExit("Missing travelpayouts_token (set TP_TOKEN in .env).")
        return TravelpayoutsProvider(token)
    if name in ("google", "googleflights", "google_flights"):
        try:
            import fast_flights  # noqa: F401
        except ImportError:
            raise SystemExit("Provider 'google' needs fast-flights: pip install fast-flights")
        g = cfg.get("google", {}) or {}
        return GoogleFlightsProvider(
            weekend=cfg["weekend"],
            window_weeks=cfg["window_weeks"],
            pairs_per_month=g.get("pairs_per_month", 4),
            pause=g.get("pause", 1.0),
        )
    raise SystemExit(f"Unknown provider: {name}")
