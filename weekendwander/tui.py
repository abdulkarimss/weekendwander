"""Full-screen TUI for WeekendWander (Textual).

Pick your passport, origin, weekend pattern, budget and provider, hit Search,
and see the matching weekend deals — with visa requirements — without touching
config.yaml. Run it with:

    python -m weekendwander.tui [--config config.yaml]

Defaults are seeded from the config file if one is given/found, so the TUI is
just an interactive front-end over the same `find_deals` pipeline the CLI uses.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button, Checkbox, Footer, Header, Input, Label, RichLog, Rule, Select, Static,
)

from .finder import find_deals
from .providers import build_provider

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_DEFAULTS = {
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


def _load_defaults(config_path: str | None) -> dict:
    """Merge config.yaml (if present) over the built-in defaults."""
    cfg = {**_DEFAULTS, "weekend": dict(_DEFAULTS["weekend"])}
    path = config_path or "config.yaml"
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        wk = {**cfg["weekend"], **(loaded.get("weekend") or {})}
        cfg.update(loaded)
        cfg["weekend"] = wk
    return cfg


class WeekendWander(App):
    TITLE = "WeekendWander ✈️"
    SUB_TITLE = "weekend flight deals + visa check"

    CSS = """
    Screen { layout: horizontal; }
    #form {
        width: 44; padding: 1 2; border: round $accent; height: 100%;
    }
    #form Label { color: $text-muted; margin-top: 1; }
    #form Input, #form Select { width: 100%; }
    .row { height: auto; }
    .row Checkbox { width: 1fr; }
    #right { width: 1fr; height: 100%; }
    #results { border: round $secondary; padding: 0 1; height: 1fr; }
    #status { color: $text-muted; height: auto; }
    Button { margin-top: 1; }
    """

    BINDINGS = [("q", "quit", "Quit"), ("ctrl+s", "search", "Search")]

    def __init__(self, config_path: str | None = None):
        super().__init__()
        self._cfg = _load_defaults(config_path)

    def compose(self) -> ComposeResult:
        c = self._cfg
        wk = c["weekend"]
        yield Header(show_clock=True)
        with VerticalScroll(id="form"):
            yield Label("Passport (ISO3)")
            yield Input(c["nationality"], id="nationality", max_length=3)
            yield Label("Origin airport (IATA)")
            yield Input(c["origin"], id="origin", max_length=3)
            yield Label("Provider")
            yield Select(
                [("Google Flights (no token)", "google"),
                 ("Amadeus (free quota, real fares)", "amadeus"),
                 ("Travelpayouts (TP_TOKEN)", "travelpayouts")],
                value=c["provider"] if c["provider"] in ("google", "amadeus", "travelpayouts") else "google",
                allow_blank=False, id="provider",
            )
            yield Label("Depart days")
            with Horizontal(classes="row"):
                for d in ["thu", "fri", "sat"]:
                    yield Checkbox(d.title(), d in wk["depart_days"], id=f"dep_{d}")
            yield Label("Return days")
            with Horizontal(classes="row"):
                for d in ["fri", "sat", "sun"]:
                    yield Checkbox(d.title(), d in wk["return_days"], id=f"ret_{d}")
            yield Label("Nights (min / max)")
            with Horizontal(classes="row"):
                yield Input(str(wk["min_nights"]), id="min_nights", type="integer")
                yield Input(str(wk["max_nights"]), id="max_nights", type="integer")
            yield Label("Max price (SAR)")
            yield Input(str(c["max_price"]), id="max_price", type="integer")
            yield Label("Scan window (weeks)")
            yield Input(str(c["window_weeks"]), id="window_weeks", type="integer")
            yield Label("Destinations (IATA, comma-sep; blank = auto)")
            yield Input(", ".join(c.get("destinations") or []), id="destinations")
            with Horizontal(classes="row"):
                yield Checkbox("Non-stop only", c["direct_only"], id="direct_only")
                yield Checkbox("Easy visa only", c["easy_visa_only"], id="easy_visa_only")
            yield Button("Search", id="search", variant="primary")
        with Vertical(id="right"):
            yield Static("Set your trip on the left, then press Search (or Ctrl-S).",
                         id="status")
            yield Rule()
            yield RichLog(id="results", wrap=True, highlight=False, markup=True,
                          auto_scroll=True)
        yield Footer()

    # --- collect the form into a find_deals config dict ---
    def _build_config(self) -> dict:
        def val(wid_id, default=""):
            return self.query_one(f"#{wid_id}", Input).value.strip() or default

        def checked(wid_id):
            return self.query_one(f"#{wid_id}", Checkbox).value

        def as_int(wid_id, default):
            try:
                return int(val(wid_id, str(default)))
            except ValueError:
                return default

        depart = [d for d in ["thu", "fri", "sat"] if checked(f"dep_{d}")]
        ret = [d for d in ["fri", "sat", "sun"] if checked(f"ret_{d}")]
        dests = [s.strip().upper() for s in val("destinations").split(",") if s.strip()]
        provider = self.query_one("#provider", Select).value

        cfg = dict(self._cfg)
        cfg.update({
            "provider": provider,
            "nationality": val("nationality", "SAU").upper(),
            "origin": val("origin", "RUH").upper(),
            "max_price": as_int("max_price", 1200),
            "window_weeks": as_int("window_weeks", 8),
            "direct_only": checked("direct_only"),
            "easy_visa_only": checked("easy_visa_only"),
            "destinations": dests,
            "weekend": {
                "depart_days": depart or ["thu", "fri"],
                "return_days": ret or ["sat", "sun"],
                "min_nights": as_int("min_nights", 1),
                "max_nights": as_int("max_nights", 3),
            },
        })
        # secrets come from the environment, same as the CLI
        cfg["travelpayouts_token"] = os.environ.get("TP_TOKEN", cfg.get("travelpayouts_token"))
        return cfg

    @on(Button.Pressed, "#search")
    def action_search(self) -> None:
        log = self.query_one("#results", RichLog)
        log.clear()
        self.query_one("#status", Static).update("Searching… this can take a moment.")
        self.query_one("#search", Button).disabled = True
        self._search(self._build_config())

    @work(thread=True)
    def _search(self, cfg: dict) -> None:
        log = self.query_one("#results", RichLog)
        self.call_from_thread(
            log.write,
            f"[dim]Building {cfg['provider']} provider and searching from "
            f"{cfg['origin']} …[/dim]")
        try:
            provider = build_provider(cfg)
        except SystemExit as e:                 # e.g. missing TP_TOKEN
            self.call_from_thread(self._finish, f"[red]{e}[/red]", 0)
            return
        deals = find_deals(provider, cfg,
                            log=lambda *a: self.call_from_thread(log.write,
                                                                 f"[dim]{' '.join(map(str, a))}[/dim]"))
        self.call_from_thread(self._render, deals)

        cur = cfg.get("currency", "sar").upper()
        status = f"{len(deals)} deal(s) under {cfg['max_price']} {cur}."
        stats = getattr(provider, "stats", None)
        if stats and stats.get("errors"):
            status = (f"[yellow]{status}  ⚠ {stats['errors']}/{stats['queries']} "
                      f"queries failed — results may be incomplete.[/yellow]")
        self.call_from_thread(self._finish, status, len(deals))

    def _render(self, deals) -> None:
        log = self.query_one("#results", RichLog)
        if not deals:
            log.write("\n[yellow]No weekend deals matched. Widen the budget, "
                      "window, or destinations.[/yellow]")
            return
        for d in deals:
            v = d["visa"]
            mark = "✅" if v["easy"] else ("📝" if v["status"] not in ("no_entry", "unknown") else "⚠️")
            stops = "non-stop" if not d["transfers"] else f"{d['transfers']} stop(s)"
            dep = (d["departure_at"] or "")[:16].replace("T", " ")
            ret = (d["return_at"] or "")[:16].replace("T", " ")
            bits = [b for b in (d.get("airline"), d.get("flight_number"), d.get("aircraft")) if b]
            detail = ("\n  [cyan]✈ " + "  ·  ".join(bits) + "[/cyan]") if bits else ""
            route = f"\n  [dim]↳ {d['route']}[/dim]" if d.get("route") else ""
            log.write(
                f"\n[b]{d['city']} ({d['destination']})[/b] · {d['country']}\n"
                f"  [b green]{int(round(d['price']))} {d['currency']}[/b green]  |  {stops}\n"
                f"  Out {dep}  →  Back {ret}"
                + detail + route +
                f"\n  {mark} Visa: {v['label']}"
                + (f"\n  [link={d['link']}]Book ↗[/link]" if d.get("link") else "")
            )

    def _finish(self, message: str, _count: int) -> None:
        self.query_one("#status", Static).update(message)
        self.query_one("#search", Button).disabled = False


def main(argv=None):
    p = argparse.ArgumentParser(prog="weekendwander-tui")
    p.add_argument("--config", default=None, help="seed defaults from this YAML")
    args = p.parse_args(argv)
    WeekendWander(args.config).run()


if __name__ == "__main__":
    main()
