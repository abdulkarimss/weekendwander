"""Shared config defaults + loader used by the TUI and the web UI.

Kept dependency-free (no textual/flask imports) so either front-end can seed
its form from the same place and merge a user's config.yaml over the top.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULTS = {
    "provider": "google",
    "origin": "RUH",
    "nationality": "SAU",
    "currency": "sar",
    "market": "sa",
    "max_price": 2500,
    "max_hours": 6,
    "max_distance_km": 6000,
    "max_destinations": 50,
    "window_weeks": 8,
    "direct_only": False,
    "easy_visa_only": False,
    "destinations": ["DXB", "AUH", "SHJ", "DOH", "BAH", "KWI", "MCT", "JED", "DMM",
                     "IST", "SAW", "AYT", "ESB", "TBS", "GYD", "EVN",
                     "AMM", "BEY",
                     "CAI", "HRG", "SSH", "ADD", "NBO", "KRT", "DAR", "ZNZ",
                     "ATH", "SKG", "FCO", "MXP", "VCE", "VIE", "MUC", "ZRH",
                     "BUD", "OTP", "SOF", "BEG", "PRG",
                     "DEL", "BOM", "KHI", "ISB", "LHE", "CMB", "MLE", "KTM", "DAC"],
    "weekend": {
        "depart_days": ["thu", "fri"],
        "return_days": ["sat", "sun"],
        "min_nights": 1,
        "max_nights": 3,
    },
}


def load_defaults(config_path: str | None) -> dict:
    """Merge config.yaml (if present) over the built-in defaults."""
    cfg = {**DEFAULTS, "weekend": dict(DEFAULTS["weekend"])}
    path = config_path or "config.yaml"
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        wk = {**cfg["weekend"], **(loaded.get("weekend") or {})}
        cfg.update(loaded)
        cfg["weekend"] = wk
    return cfg
