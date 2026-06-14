"""Web UI for WeekendWander (Flask + Server-Sent Events).

A browser front-end over the same `find_deals` pipeline the CLI/TUI use. The
search is slow (per-date-pair lookups), so results stream live over SSE:
progress lines, then a card per deal, then a summary — mirroring the TUI.

    python -m weekendwander.web [--config config.yaml] [--host H] [--port P]

Secrets come from the environment (TP_TOKEN / AMADEUS_CLIENT_ID / ...), so load
your .env first:  set -a; source .env; set +a
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading

from flask import Flask, Response, request

from .defaults import load_defaults
from .finder import find_deals
from .providers import build_provider

app = Flask(__name__)
CONFIG_PATH: str | None = None       # set by main(); seeds form defaults

_TRUE = {"1", "true", "on", "yes", "True"}


def _safe_int(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _config_from_args(args, base: dict) -> dict:
    """Build a find_deals config from request query params over the defaults."""
    def get(key, default):
        v = args.get(key)
        return v if v not in (None, "") else default

    wk = base["weekend"]
    dep = [d for d in get("depart_days", "").split(",") if d] or wk["depart_days"]
    ret = [d for d in get("return_days", "").split(",") if d] or wk["return_days"]
    dests = [x.strip().upper() for x in get("destinations", "").split(",") if x.strip()]

    cfg = dict(base)
    cfg.update({
        "provider": get("provider", base["provider"]),
        "nationality": get("nationality", base["nationality"]).upper(),
        "origin": get("origin", base["origin"]).upper(),
        "max_price": _safe_int(get("max_price", base["max_price"]), base["max_price"]),
        "max_hours": _safe_int(get("max_hours", base.get("max_hours", 6)), base.get("max_hours", 6)),
        "window_weeks": _safe_int(get("window_weeks", base["window_weeks"]), base["window_weeks"]),
        "direct_only": args.get("direct_only") in _TRUE,
        "easy_visa_only": args.get("easy_visa_only") in _TRUE,
        "destinations": dests,            # blank = auto-sweep nearest in range
        "weekend": {
            "depart_days": dep,
            "return_days": ret,
            "min_nights": _safe_int(get("min_nights", wk["min_nights"]), wk["min_nights"]),
            "max_nights": _safe_int(get("max_nights", wk["max_nights"]), wk["max_nights"]),
        },
    })
    # secrets live in the environment, same as the CLI
    cfg["travelpayouts_token"] = os.environ.get("TP_TOKEN", cfg.get("travelpayouts_token"))
    return cfg


def _serialize(d: dict) -> dict:
    keys = ("destination", "city", "country", "price", "currency", "transfers",
            "departure_at", "return_at", "airline", "flight_number", "aircraft",
            "route", "link", "distance_km")
    out = {k: d.get(k) for k in keys}
    out["visa"] = d.get("visa", {})
    return out


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _run_search(cfg: dict):
    """Generator yielding SSE events: live `log` lines, then `deal`s, then `done`."""
    q: queue.Queue = queue.Queue()
    result: dict = {}

    def work():
        try:
            provider = build_provider(cfg)
            deals = find_deals(provider, cfg,
                               log=lambda *a: q.put(" ".join(str(x) for x in a)))
            result["deals"] = deals
            result["stats"] = getattr(provider, "stats", None)
        except SystemExit as e:                 # e.g. missing token/creds
            result["error"] = str(e)
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            q.put(None)                          # sentinel: work finished

    threading.Thread(target=work, daemon=True).start()
    for line in iter(q.get, None):
        yield _sse("log", line.replace("\n", " "))

    if "error" in result:
        yield _sse("failed", json.dumps(result["error"], ensure_ascii=False))
        return
    deals = result.get("deals", [])
    for d in deals:
        yield _sse("deal", json.dumps(_serialize(d), ensure_ascii=False))
    yield _sse("done", json.dumps({"count": len(deals), "stats": result.get("stats")},
                                  ensure_ascii=False))


@app.route("/")
def index():
    base = load_defaults(CONFIG_PATH)
    return PAGE.replace("__DEFAULTS_JSON__", json.dumps(base, default=str))


@app.route("/search")
def search():
    base = load_defaults(CONFIG_PATH)
    cfg = _config_from_args(request.args, base)
    return Response(_run_search(cfg), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeekendWander ✈️</title>
<style>
  :root { --bg:#0e1117; --panel:#161b22; --line:#30363d; --txt:#e6edf3;
          --muted:#8b949e; --accent:#58a6ff; --green:#3fb950; --warn:#d29922; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; }
  header { padding:14px 20px; border-bottom:1px solid var(--line);
           display:flex; align-items:baseline; gap:12px; }
  header h1 { font-size:18px; margin:0; }
  header span { color:var(--muted); font-size:13px; }
  .wrap { display:flex; gap:16px; padding:16px; align-items:flex-start; }
  form { width:320px; flex:none; background:var(--panel); border:1px solid var(--line);
         border-radius:10px; padding:16px; position:sticky; top:16px; }
  form label { display:block; color:var(--muted); font-size:12px; margin:10px 0 3px; }
  input,select { width:100%; background:#0d1117; color:var(--txt);
                 border:1px solid var(--line); border-radius:6px; padding:7px 9px; }
  .days { display:flex; gap:10px; flex-wrap:wrap; }
  .days label { display:flex; align-items:center; gap:5px; margin:0; color:var(--txt); }
  .days input { width:auto; }
  .two { display:flex; gap:10px; } .two > div { flex:1; }
  .checks { margin-top:12px; display:flex; gap:16px; }
  button { margin-top:16px; width:100%; background:var(--accent); color:#06121f;
           border:0; border-radius:7px; padding:10px; font-weight:700; cursor:pointer; }
  button:disabled { opacity:.55; cursor:wait; }
  .main { flex:1; min-width:0; }
  #status { color:var(--muted); margin:4px 2px 12px; min-height:20px; }
  #status.warn { color:var(--warn); }
  details { margin-bottom:14px; } summary { color:var(--muted); cursor:pointer; }
  #log { font:12px/1.45 ui-monospace,Menlo,monospace; color:var(--muted);
         white-space:pre-wrap; max-height:180px; overflow:auto; padding:8px;
         background:#0d1117; border:1px solid var(--line); border-radius:6px; }
  .deal { background:var(--panel); border:1px solid var(--line); border-radius:10px;
          padding:12px 14px; margin-bottom:10px; }
  .deal .top { display:flex; justify-content:space-between; gap:10px; align-items:baseline; }
  .deal .city { font-weight:700; font-size:15px; } .deal .ctry { color:var(--muted); }
  .deal .price { color:var(--green); font-weight:700; font-size:15px; white-space:nowrap; }
  .deal .meta { color:var(--muted); margin-top:3px; }
  .deal .det { margin-top:5px; color:var(--accent); }
  .deal .route { color:var(--muted); font:12px ui-monospace,monospace; }
  .deal .visa { margin-top:5px; } .deal a { color:var(--accent); }
</style></head>
<body>
<header><h1>WeekendWander ✈️</h1><span>weekend flight deals + visa check</span></header>
<div class="wrap">
  <form id="f" onsubmit="return search(event)">
    <label>Passport (ISO3)</label><input id="nationality" maxlength="3">
    <label>Origin airport (IATA)</label><input id="origin" maxlength="3">
    <label>Provider</label>
    <select id="provider">
      <option value="google">Google Flights (no token)</option>
      <option value="amadeus">Amadeus (free quota, real fares)</option>
      <option value="travelpayouts">Travelpayouts (TP_TOKEN)</option>
    </select>
    <label>Depart days</label>
    <div class="days" id="depart_days"></div>
    <label>Return days</label>
    <div class="days" id="return_days"></div>
    <div class="two">
      <div><label>Min nights</label><input id="min_nights" type="number" min="0"></div>
      <div><label>Max nights</label><input id="max_nights" type="number" min="0"></div>
    </div>
    <div class="two">
      <div><label>Max price</label><input id="max_price" type="number"></div>
      <div><label>Max hours</label><input id="max_hours" type="number" step="0.5"></div>
    </div>
    <label>Scan window (weeks)</label><input id="window_weeks" type="number">
    <label>Destinations (IATA, comma-sep; blank = auto)</label>
    <input id="destinations">
    <div class="checks">
      <label><input type="checkbox" id="direct_only"> Non-stop</label>
      <label><input type="checkbox" id="easy_visa_only"> Easy visa</label>
    </div>
    <button id="go" type="submit">Search</button>
  </form>
  <div class="main">
    <div id="status">Set your trip, then Search.</div>
    <details open><summary>Progress</summary><div id="log"></div></details>
    <div id="deals"></div>
  </div>
</div>
<script>
const D = __DEFAULTS_JSON__;
const $ = id => document.getElementById(id);
const ALL_DAYS = ["mon","tue","wed","thu","fri","sat","sun"];
let es = null;

function makeDays(box, selected){
  box.innerHTML = "";
  ALL_DAYS.forEach(d => {
    const l = document.createElement("label");
    l.innerHTML = `<input type="checkbox" value="${d}" ${selected.includes(d)?"checked":""}> ${d[0].toUpperCase()+d.slice(1)}`;
    box.appendChild(l);
  });
}
function days(box){ return [...box.querySelectorAll("input:checked")].map(i=>i.value); }

function init(){
  $("nationality").value = D.nationality; $("origin").value = D.origin;
  $("provider").value = D.provider;
  $("min_nights").value = D.weekend.min_nights; $("max_nights").value = D.weekend.max_nights;
  $("max_price").value = D.max_price; $("max_hours").value = D.max_hours;
  $("window_weeks").value = D.window_weeks;
  $("destinations").value = (D.destinations||[]).join(", ");
  $("direct_only").checked = !!D.direct_only; $("easy_visa_only").checked = !!D.easy_visa_only;
  makeDays($("depart_days"), D.weekend.depart_days);
  makeDays($("return_days"), D.weekend.return_days);
}

function visaMark(v){
  if(v.easy) return "✅";
  return (["no_entry","unknown"].includes(v.status)) ? "⚠️" : "📝";
}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

function renderDeal(d){
  const dep = (d.departure_at||"").slice(0,16).replace("T"," ");
  const ret = (d.return_at||"").slice(0,16).replace("T"," ");
  const stops = d.transfers ? d.transfers+" stop(s)" : "non-stop";
  const bits = [d.airline, d.flight_number, d.aircraft].filter(Boolean).map(esc).join("  ·  ");
  const el = document.createElement("div");
  el.className = "deal";
  el.innerHTML =
    `<div class="top"><span><span class="city">${esc(d.city)} (${esc(d.destination)})</span> <span class="ctry">· ${esc(d.country)}</span></span>`+
    `<span class="price">${Math.round(d.price)} ${esc(d.currency)}</span></div>`+
    `<div class="meta">${stops} · Out ${esc(dep)} → Back ${esc(ret)}</div>`+
    (bits ? `<div class="det">✈ ${bits}</div>` : "")+
    (d.route ? `<div class="route">↳ ${esc(d.route)}</div>` : "")+
    `<div class="visa">${visaMark(d.visa)} Visa: ${esc(d.visa.label)}</div>`+
    (d.link ? `<div><a href="${esc(d.link)}" target="_blank" rel="noopener">Book ↗</a></div>` : "");
  $("deals").appendChild(el);
}

function search(ev){
  ev.preventDefault();
  if(es) es.close();
  $("deals").innerHTML = ""; $("log").textContent = "";
  $("status").className = ""; $("status").textContent = "Searching…";
  $("go").disabled = true;
  const p = new URLSearchParams({
    nationality:$("nationality").value, origin:$("origin").value,
    provider:$("provider").value, max_price:$("max_price").value,
    max_hours:$("max_hours").value, window_weeks:$("window_weeks").value,
    min_nights:$("min_nights").value, max_nights:$("max_nights").value,
    destinations:$("destinations").value,
    depart_days:days($("depart_days")).join(","),
    return_days:days($("return_days")).join(","),
    direct_only:$("direct_only").checked, easy_visa_only:$("easy_visa_only").checked,
  });
  es = new EventSource("/search?"+p.toString());
  es.addEventListener("log", e => {
    const log = $("log"); log.textContent += e.data + "\n"; log.scrollTop = log.scrollHeight;
  });
  es.addEventListener("deal", e => renderDeal(JSON.parse(e.data)));
  es.addEventListener("failed", e => {
    $("status").className = "warn";
    $("status").textContent = "Error: " + JSON.parse(e.data);
    es.close(); $("go").disabled = false;
  });
  es.addEventListener("done", e => {
    const s = JSON.parse(e.data);
    let msg = s.count + " deal(s).";
    if(s.stats && s.stats.errors){ msg += `  ⚠ ${s.stats.errors}/${s.stats.queries} queries failed — results may be incomplete.`; $("status").className="warn"; }
    $("status").textContent = msg;
    es.close(); $("go").disabled = false;
  });
  es.onerror = () => {   // connection dropped before done
    if(es.readyState === EventSource.CLOSED){ $("go").disabled = false; }
  };
  return false;
}
init();
</script>
</body></html>
"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="weekendwander-web")
    p.add_argument("--config", default=None, help="seed form defaults from this YAML")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    global CONFIG_PATH
    CONFIG_PATH = args.config
    print(f"WeekendWander web → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    # threaded so SSE streams don't block other requests
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
