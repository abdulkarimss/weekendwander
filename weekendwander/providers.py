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
import os
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
                "flight_number": str(row.get("flight_number", "") or ""),
                "link": (AVIASALES_LINK + link) if link else None,
            })
        time.sleep(self.pause)            # be polite to the cache API
        return offers


class _WeekendPairProvider(BaseProvider):
    """Shared base for sources with no cheap-per-destination discovery that can
    only be queried for concrete date pairs (Google Flights, Amadeus).

      * discover() proposes every known airport nearest-first, and defers
        entirely to an explicit `destinations` list when one is configured
        (the finder unions in cfg["destinations"] and applies the distance/cap).
      * dated_offers() enumerates the weekend (depart/return) date pairs inside
        `month`, calls the subclass's _query_pair() for each, and tracks query/
        error counts in .stats so the finder can tell a broken source from a
        genuine "no deals".

    Subclasses implement _query_pair(origin, dest, dep, ret, currency, direct).
    """

    def __init__(self, weekend, window_weeks, destinations=None,
                 pairs_per_month=4, pause=1.0):
        self.weekend = weekend
        self.window_weeks = window_weeks
        self.destinations = [d.upper() for d in (destinations or [])]
        self.pairs_per_month = pairs_per_month
        self.pause = pause
        self.stats = {"queries": 0, "errors": 0, "last_error": None}

    def discover(self, origin, currency, market):
        if self.destinations:
            return []
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
        raise NotImplementedError

    def dated_offers(self, origin, destination, month, currency, market, direct=False):
        origin, destination = origin.upper(), destination.upper()
        offers = []
        for dep, ret in self._weekend_pairs(month):
            self.stats["queries"] += 1
            try:
                offer = self._query_pair(origin, destination, dep, ret, currency, direct)
                if offer:
                    offers.append(offer)
            except Exception as e:                 # skip a flaky query, keep going
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(e).__name__}: {e}"
            time.sleep(self.pause)                 # pace requests
        return offers


class GoogleFlightsProvider(_WeekendPairProvider):
    """Google Flights via the unofficial `fast-flights` scraper (no API token).

    Scraping Google is brittle and rate-limited, so this is best pointed at a
    short, explicit `destinations` list with a small `window_weeks`. Tune the
    request budget with `google.pairs_per_month` / `google.pause` in config.
    """

    def __init__(self, weekend, window_weeks, destinations=None,
                 pairs_per_month=4, pause=1.0):
        super().__init__(weekend, window_weeks, destinations, pairs_per_month, pause)
        # imported lazily so the dependency is only needed when this provider
        # is actually selected
        from fast_flights import get_flights, create_query, FlightQuery, Passengers
        self._get_flights = get_flights
        self._create_query = create_query
        self._FlightQuery = FlightQuery
        self._Passengers = Passengers

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
        segs = best.flights                         # outbound legs
        out = segs[0]
        dep_dt = (f"{out.departure.date[0]:04d}-{out.departure.date[1]:02d}-"
                  f"{out.departure.date[2]:02d}T{out.departure.time[0]:02d}:"
                  f"{out.departure.time[1]:02d}:00")
        code = lambda a: getattr(a, "code", a)      # Airport obj or plain str
        route = "→".join([code(segs[0].from_airport)] + [code(s.to_airport) for s in segs])
        aircraft = ", ".join(dict.fromkeys(
            s.plane_type for s in segs if getattr(s, "plane_type", None)))
        link = (f"{GOOGLE_FLIGHTS_LINK}?q=" +
                quote_plus(f"Flights from {origin} to {dest} on {dep} through {ret}"))
        return {
            "origin": origin,
            "destination": dest,
            "price": float(best.price),
            "currency": currency.upper(),
            "departure_at": dep_dt,
            "return_at": ret.isoformat(),         # Google only details the outbound leg
            "transfers": max(len(segs) - 1, 0),
            "airline": ", ".join(best.airlines) if best.airlines else "",
            "flight_number": "",                   # not exposed by the scraper
            "aircraft": aircraft,
            "route": route,
            "link": link,
        }


class AmadeusProvider(_WeekendPairProvider):
    """Amadeus Self-Service Flight Offers Search — real GDS fares, free monthly
    quota. Needs AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET (OAuth2 client
    credentials). Like the Google provider it has no cheap-per-destination
    discovery in this flow, so it prices each weekend date-pair — but the API
    returns the full round-trip, so return times are real (not just the date).

    `amadeus.environment` selects the test (default) or production host; they
    use *separate* credentials. Tune budget with amadeus.pairs_per_month /
    amadeus.max_results / amadeus.pause.
    """

    HOSTS = {"test": "https://test.api.amadeus.com",
             "production": "https://api.amadeus.com"}

    def __init__(self, client_id, client_secret, weekend, window_weeks,
                 destinations=None, currency="sar", environment="test",
                 max_results=5, pairs_per_month=4, pause=0.2,
                 timeout=20, retries=2):
        super().__init__(weekend, window_weeks, destinations, pairs_per_month, pause)
        self.cid = client_id
        self.secret = client_secret
        self.currency = currency.upper()
        self.host = self.HOSTS.get(environment, self.HOSTS["test"])
        self.max_results = max_results
        self.timeout = timeout
        self.retries = retries
        self._session = requests.Session()
        self._access_token = None
        self._token_exp = 0.0

    def _token(self):
        if self._access_token and time.time() < self._token_exp - 30:
            return self._access_token
        r = self._session.post(
            f"{self.host}/v1/security/oauth2/token",
            data={"grant_type": "client_credentials",
                  "client_id": self.cid, "client_secret": self.secret},
            timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        self._access_token = j["access_token"]
        self._token_exp = time.time() + j.get("expires_in", 1799)
        return self._access_token

    def _get(self, path, params):
        last = None
        for attempt in range(self.retries + 1):
            r = self._session.get(
                f"{self.host}{path}", params=params,
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=self.timeout)
            if r.status_code == 429:               # rate limited — back off, retry
                last = r
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        if last is not None:
            last.raise_for_status()
        raise RuntimeError(f"Amadeus request failed ({path})")

    def _query_pair(self, origin, dest, dep, ret, currency, direct):
        cur = (currency or self.currency).upper()
        params = {
            "originLocationCode": origin,
            "destinationLocationCode": dest,
            "departureDate": dep.isoformat(),
            "returnDate": ret.isoformat(),
            "adults": 1,
            "currencyCode": cur,
            "max": self.max_results,
        }
        if direct:
            params["nonStop"] = "true"
        data = self._get("/v2/shopping/flight-offers", params)
        offers = data.get("data") or []
        if not offers:
            return None
        best = min(offers, key=lambda o: float(o["price"]["grandTotal"]))
        itins = best.get("itineraries", [])
        out = itins[0]["segments"]
        ret_at = (itins[1]["segments"][0]["departure"]["at"]
                  if len(itins) > 1 else ret.isoformat())

        dicts = data.get("dictionaries") or {}
        carriers_map = dicts.get("carriers") or {}
        aircraft_map = dicts.get("aircraft") or {}
        airlines = sorted({carriers_map.get(s["carrierCode"], s["carrierCode"]) for s in out})
        flight_nums = [f"{s['carrierCode']}{s.get('number', '')}".strip() for s in out]
        aircraft = list(dict.fromkeys(
            aircraft_map.get((s.get("aircraft") or {}).get("code", ""),
                             (s.get("aircraft") or {}).get("code", ""))
            for s in out if (s.get("aircraft") or {}).get("code")))
        route = "→".join([out[0]["departure"]["iataCode"]]
                         + [s["arrival"]["iataCode"] for s in out])
        link = (f"{GOOGLE_FLIGHTS_LINK}?q=" +
                quote_plus(f"Flights from {origin} to {dest} on {dep} through {ret}"))
        return {
            "origin": origin,
            "destination": dest,
            "price": float(best["price"]["grandTotal"]),
            "currency": best["price"].get("currency", cur),
            "departure_at": out[0]["departure"]["at"],
            "return_at": ret_at,
            "transfers": max(len(out) - 1, 0),
            "airline": ", ".join(airlines),
            "flight_number": " / ".join(fn for fn in flight_nums if fn),
            "aircraft": ", ".join(aircraft),
            "route": route,
            "link": link,
        }


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
            destinations=cfg.get("destinations", []),
            pairs_per_month=g.get("pairs_per_month", 4),
            pause=g.get("pause", 1.0),
        )
    if name == "amadeus":
        cid = os.environ.get("AMADEUS_CLIENT_ID") or cfg.get("amadeus_client_id")
        secret = os.environ.get("AMADEUS_CLIENT_SECRET") or cfg.get("amadeus_client_secret")
        if not (cid and secret):
            raise SystemExit("Provider 'amadeus' needs AMADEUS_CLIENT_ID / "
                             "AMADEUS_CLIENT_SECRET (set in .env).")
        a = cfg.get("amadeus", {}) or {}
        return AmadeusProvider(
            cid, secret,
            weekend=cfg["weekend"], window_weeks=cfg["window_weeks"],
            destinations=cfg.get("destinations", []),
            currency=cfg.get("currency", "sar"),
            environment=a.get("environment", "test"),
            max_results=a.get("max_results", 5),
            pairs_per_month=a.get("pairs_per_month", 4),
            pause=a.get("pause", 0.2),
        )
    raise SystemExit(f"Unknown provider: {name}")
