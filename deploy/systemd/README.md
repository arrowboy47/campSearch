# CampSearch refresh timers (systemd user units)

Run on the AI-core host (Jadu Z4 G4), same place PDW sync runs. They open a
short-lived SSH tunnel to `campsearch-pg` on thebigbox (local port **5435**, so a
hand-run dev tunnel on 5434 never clashes), run `scripts/run_all.py`, then close
the tunnel.

- `campsearch-refresh-daily.timer`  → 03:20 → `run_all.py daily`  (weather, today)
- `campsearch-refresh-weekly.timer` → Sun 04:10 → `run_all.py weekly`
  (fs.usda static + open/closed status → RIDB reservations → 7-day weather)
- `campsearch-refresh-monthly.timer` → 1st of month 04:40 → `run_all.py monthly`
  (OSM Overpass nearby-trail ingest — ~1 h, big `RandomizedDelaySec`)

## Install

```bash
# 1. secrets — NOT in the repo
mkdir -p ~/.config/campsearch
cat > ~/.config/campsearch/refresh.env <<'EOF'
DATABASE_URL=postgresql://campsearch:PASSWORD@localhost:5435/camping
OPENWEATHER_API_KEY=...
RIDB_API_KEY=96b63a3f-a8ae-41c3-99ba-45748555a8e4
EOF
chmod 600 ~/.config/campsearch/refresh.env
# NOTE port 5435 (the timer's tunnel), not 5434.

# 2. units
mkdir -p ~/.config/systemd/user
cp deploy/systemd/campsearch-refresh@.service \
   deploy/systemd/campsearch-refresh-failed@.service \
   deploy/systemd/campsearch-refresh-daily.timer \
   deploy/systemd/campsearch-refresh-weekly.timer \
   deploy/systemd/campsearch-refresh-monthly.timer \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now campsearch-refresh-daily.timer \
  campsearch-refresh-weekly.timer campsearch-refresh-monthly.timer

# 3. optional: keep timers running when logged out
loginctl enable-linger "$USER"
```

## Check / operate

```bash
systemctl --user list-timers 'campsearch-*'
systemctl --user start campsearch-refresh@daily.service     # run now
journalctl --user -u 'campsearch-refresh@daily' -n 100
```

Every run also writes a row to `scrape_runs` in the DB:

```sql
SELECT source, status, started_at, rows_seen, rows_upserted, errors
FROM scrape_runs ORDER BY id DESC LIMIT 20;
```

## Failure alerts

`campsearch-refresh-failed@%i.service` fires on failure and dumps the tail to the
journal. For a push notification, drop an executable `~/.config/campsearch/notify.sh`
that takes one message arg (e.g. a `curl` to the Telegram bot API) — the handler
calls it if present.

## Alternative: Hermes cron

If you'd rather route through the existing `~/.hermes/cron/jobs.json` +
`deliver: telegram` pipeline (like `pdw-nightly-sync`): add a `no_agent` job with
`script` pointing at a thin `~/.hermes/scripts/campsearch_refresh.py` wrapper that
shells out to this repo's venv + `run_all.py`. The systemd timers above are the
default because they don't depend on the Hermes runtime being up.
