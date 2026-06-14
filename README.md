# WeekendWander ✈️

A small engine that watches for **cheap weekend flights** from your home
airport and **pings you** (Telegram/email) when it finds one — domestic or
international — and tells you **whether you need a visa** for each destination.

Built around your pattern: **out Thursday/Friday, back Saturday/Sunday.**

---

## What it does

1. **Discovers** every destination reachable from your origin (RUH by default).
2. **Keeps only the near ones** — within `max_distance_km` great-circle (a
   proxy for short flight time), plus domestic.
3. **Pulls weekend fares** for the next `window_weeks` and keeps trips that
   *depart Thu/Fri and return Sat/Sun* under your `max_price`.
4. **Tags the visa requirement** for each destination based on **your
   passport** — visa-free / visa-on-arrival / e-visa / ETA / visa-required /
   domestic.
5. **Notifies** only what's new or cheaper than last time (no spam).

```
✈️  Weekend flight deals from Riyadh

Bahrain (BAH) · Bahrain
  290 SAR  |  non-stop
  Out 2026-06-19 18:00  →  Back 2026-06-21 18:00
  ✅ Visa: Visa-free
  Book: https://www.aviasales.com/search/...

Tbilisi (TBS) · Georgia
  760 SAR  |  non-stop
  Out 2026-06-19 06:30  →  Back 2026-06-21 22:10
  ✅ Visa: Visa-free (360 days)
  Book: https://www.aviasales.com/search/...
```

---

## ⚠️ Set your passport

Open `config.yaml` and set `nationality` to **your** passport (ISO3):

```yaml
nationality: SAU   # change to YEM, EGY, IND, PAK, GBR ... whatever you hold
```

Visa results are wrong if this is wrong. Visa data is a snapshot from the
maintained [passport-index dataset]; **always confirm with the airline /
embassy before booking** — rules change.

---

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml      # then edit it (set nationality!)
cp .env.example .env                     # then fill in tokens
```

**1) Flight data token (free).** Sign up at https://www.travelpayouts.com →
*Tools → API* → copy the token into `.env` as `TP_TOKEN`. Prices come from
Aviasales' search cache, so they're indicative — the booking link opens the
live search to confirm.

**2) Telegram (easiest alerts).**
- Message **@BotFather** → `/newbot` → copy the token → `.env` `TG_BOT_TOKEN`.
- Message your new bot once, then open
  `https://api.telegram.org/bot<token>/getUpdates` and copy your numeric
  chat id → `.env` `TG_CHAT_ID`.

Prefer email? Set `notify.email.enabled: true` in `config.yaml` and fill the
SMTP block instead (Gmail needs an app password).

---

## Run

```bash
# load .env then run once
set -a; source .env; set +a
python -m weekendwander.cli --config config.yaml

python -m weekendwander.cli --config config.yaml --dry-run   # print, don't send
python -m weekendwander.cli --config config.yaml --loop      # run every 6h
```

Verify the logic offline (uses real visa data + a mock flight feed):

```bash
python selftest.py
```

---

## Deploy (pick one) — see `deploy/`

| File | For |
|------|-----|
| `deploy/crontab.txt` | a Linux box / VPS |
| `deploy/openshift-cronjob.yaml` | OpenShift / Kubernetes CronJob (+ PVC for state) |
| `deploy/gitlab-ci.yml` | a scheduled GitLab CI pipeline |
| `Dockerfile` | container build |

---

## Tuning knobs (`config.yaml`)

- `max_price` — budget ceiling (SAR).
- `max_distance_km` — how far "nearby" reaches (3500 ≈ Gulf, Levant, Egypt,
  Turkey, Caucasus, parts of India/East Africa).
- `easy_visa_only: true` — only surface visa-free / visa-on-arrival places
  (best for spontaneous trips — no paperwork).
- `direct_only: true` — non-stop only.
- `weekend.*` — change the depart/return days and trip length.
- `include_countries` / `exclude_countries` — ISO3 overrides.
- `destinations` — force-check specific IATA codes regardless of distance.

---

## How it's wired

```
cli.py ─ load config + env (.env keeps secrets out of the file)
  └─ finder.find_deals
       ├─ providers.TravelpayoutsProvider   discover() + dated_offers()  (v3 API)
       ├─ airports.py    IATA → country ISO3 + km from origin
       ├─ visa.py        (passport ISO3, dest ISO3) → requirement
       └─ state.py       SQLite: skip already-notified / not-cheaper deals
  └─ notify.py           Telegram / email / console
```

Swap in another price source (e.g. Amadeus Self-Service) by subclassing
`BaseProvider` in `providers.py` — `finder` doesn't care where offers come from.

Data: `data/airports.json` (OpenFlights, IATA→country+coords),
`data/visa_iso3.csv` (passport-index dataset). Refresh either occasionally.

[passport-index dataset]: https://github.com/ilyankou/passport-index-dataset
