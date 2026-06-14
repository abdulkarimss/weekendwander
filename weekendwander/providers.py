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
import time
import requests

AVIASALES_LINK = "https://www.aviasales.com"


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


def build_provider(cfg):
    name = (cfg.get("provider") or "travelpayouts").lower()
    if name == "travelpayouts":
        token = cfg.get("travelpayouts_token")
        if not token:
            raise SystemExit("Missing travelpayouts_token (set TP_TOKEN in .env).")
        return TravelpayoutsProvider(token)
    raise SystemExit(f"Unknown provider: {name}")
