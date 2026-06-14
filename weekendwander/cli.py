"""WeekendWander CLI.

Usage:
  python -m weekendwander.cli --config config.yaml          # run once
  python -m weekendwander.cli --config config.yaml --loop   # run forever
  python -m weekendwander.cli --config config.yaml --dry-run # print, don't notify
"""
import argparse
import os
import sys
import time
from pathlib import Path

import yaml

from .providers import build_provider
from .finder import find_deals
from .state import State
from .notify import Notifier, render


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # secrets come from environment so they never live in the config file
    cfg["travelpayouts_token"] = os.environ.get("TP_TOKEN", cfg.get("travelpayouts_token"))
    nt = cfg.setdefault("notify", {})
    tg = nt.setdefault("telegram", {})
    if os.environ.get("TG_BOT_TOKEN"):
        tg["bot_token"] = os.environ["TG_BOT_TOKEN"]
    if os.environ.get("TG_CHAT_ID"):
        tg["chat_id"] = os.environ["TG_CHAT_ID"]
    # STATE_DIR (set by the OpenShift/K8s deployment) points at the mounted
    # PVC; keep the configured filename but place it there so state persists.
    state_dir = os.environ.get("STATE_DIR")
    if state_dir:
        name = Path(cfg.get("state_file", "weekendwander_state.db")).name
        cfg["state_file"] = str(Path(state_dir) / name)
    return cfg


def run_once(cfg, dry_run=False):
    provider = build_provider(cfg)
    deals = find_deals(provider, cfg)
    print(f"Found {len(deals)} weekend deal(s) under budget.")
    if not deals:
        return 0
    st = State(cfg.get("state_file", "weekendwander_state.db"))
    fresh = [d for d in deals if st.is_new(d)]
    for d in fresh:
        st.remember(d)
    st.close()
    print(f"{len(fresh)} new/cheaper since last run.")
    text = render(fresh, header=cfg.get("notify_header", "Weekend flight deals"))
    if text:
        if dry_run:
            print("\n--- DRY RUN (not sending) ---\n" + text)
        else:
            Notifier(cfg).send(text)
    return len(fresh)


def main(argv=None):
    p = argparse.ArgumentParser(prog="weekendwander")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--loop", action="store_true", help="run repeatedly")
    p.add_argument("--interval", type=int, default=None, help="seconds between runs")
    p.add_argument("--dry-run", action="store_true", help="print, do not notify")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    if args.loop:
        interval = args.interval or cfg.get("interval_seconds", 21600)  # 6h
        print(f"Looping every {interval}s. Ctrl-C to stop.")
        while True:
            try:
                run_once(cfg, args.dry_run)
            except Exception as e:
                print(f"[run error] {e}", file=sys.stderr)
            time.sleep(interval)
    else:
        run_once(cfg, args.dry_run)


if __name__ == "__main__":
    main()
