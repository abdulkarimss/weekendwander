"""Visa requirement lookup using the passport-index dataset (ISO3 codes).

Requirement values in the source data:
  - an integer string ("90", "30", ...) -> visa-free stay of that many days
  - "visa free"        -> no visa needed
  - "visa on arrival"  -> visa issued at the border
  - "e-visa"           -> apply online before travel
  - "eta"              -> electronic travel authorisation before travel
  - "visa required"    -> apply at embassy/consulate in advance
  - "no admission"     -> entry not permitted
  - "-1"               -> same country (your own passport)
"""
import csv
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data" / "visa_iso3.csv"

# (passport_iso3, dest_iso3) -> raw requirement string
_TABLE = {}
with open(_DATA, encoding="utf-8") as _f:
    _r = csv.DictReader(_f)
    for _row in _r:
        _TABLE[(_row["Passport"], _row["Destination"])] = _row["Requirement"].strip()


def _classify(raw: str):
    """Return (status_code, human_label, advance_action_needed: bool)."""
    if raw is None:
        return "unknown", "Unknown — verify manually", True
    raw = raw.strip().lower()
    if raw == "-1":
        return "home", "Home country", False
    if raw.isdigit() or raw == "visa free":
        days = f" ({raw} days)" if raw.isdigit() else ""
        return "visa_free", f"Visa-free{days}", False
    if raw == "visa on arrival":
        return "voa", "Visa on arrival", False
    if raw == "e-visa":
        return "evisa", "e-Visa (apply online first)", True
    if raw == "eta":
        return "eta", "ETA required (apply online first)", True
    if raw == "visa required":
        return "required", "Visa required (apply in advance)", True
    if raw == "no admission":
        return "no_entry", "No admission", True
    return "unknown", f"Unknown ({raw})", True


def check(passport_iso3: str, dest_iso3: str):
    """Return a dict describing the visa situation for this passport+destination."""
    if not passport_iso3 or not dest_iso3:
        return {"status": "unknown", "label": "Unknown — verify manually",
                "advance_needed": True, "easy": False}
    if passport_iso3 == dest_iso3:
        return {"status": "domestic", "label": "Domestic — no visa",
                "advance_needed": False, "easy": True}
    raw = _TABLE.get((passport_iso3, dest_iso3))
    status, label, advance = _classify(raw)
    easy = status in ("visa_free", "voa", "home", "domestic")
    return {"status": status, "label": label, "advance_needed": advance, "easy": easy}
