# StoreScope

Revenue analytics for a Tebex webstore. It reads the store's public **Recent Payments**
widget, prices every purchase against the live catalog, and serves a dashboard with
revenue totals, product and buyer stats, analytics and tracker health. One Python
process, SQLite storage, no browser and no API key.

```
python -m dragtrack          # tracker + dashboard on http://0.0.0.0:8787
```

Store owners get their own numbers without waiting for Tebex reports; the widget is
public, so it works against any store without credentials.

### The three problems worth reading the code for

**Cloudflare.** The storefront sits behind a managed challenge that blocks `requests`
and plain `curl` outright. The scraper uses [curl_cffi](https://github.com/lexiforest/curl_cffi)
to present a real Chrome TLS fingerprint, and rotates through edge/safari/firefox profiles
if a profile starts getting challenged.

**Knowing what is new.** The widget shows only the 5 most recent payments, with no ids
and a clock accurate to the minute. Each snapshot is aligned against the previous one to
work out which rows are genuinely new — which has to survive the CDN serving a slightly
stale page, refunds vanishing mid-list, two buyers looking identical, and the process
restarting. A fingerprint guard in the database is the backstop against double counting.

**Truncated names.** The widget cuts product names at 20 characters, so `Magic Find Booster -`
could be the 1-hour ($3.99) or the 3-hour ($9.99) package. Matching simulates the same
truncation; where several products collide the purchase is booked at a configurable price
(avg by default), flagged **ambiguous**, kept with its min/max range, and can be resolved
by hand in the dashboard. Totals show `≈` while any unresolved range remains.

Amounts are list prices — coupons, gift cards, tax and currency conversion are not visible
on a public widget, so treat totals as gross list revenue.

## How it works

* Every `POLL_INTERVAL` seconds (default 30) the bot fetches the store homepage using
  [curl_cffi](https://github.com/lexiforest/curl_cffi) with a real Chrome TLS fingerprint —
  the store sits behind a Cloudflare managed challenge that blocks plain `requests`/`curl`.
  If Cloudflare ever challenges a profile, the client rotates through edge/safari/firefox.
* The widget lists the 5 most recent payments (avatar → player UUID, username, product name
  **truncated to 20 characters**, and a `Today HH:MM` label). The bot aligns each new snapshot
  against the previous one to work out exactly which entries are new — this survives CDN
  serving a slightly older page, refunds vanishing from the list, duplicate-looking rows and
  restarts (a DB fingerprint guard prevents double counting).
* Every hour the bot crawls the category pages for the full catalog (package id, full name,
  price, sale price, type) and logs price changes / new / removed products as events.
* Widget names are matched to products by simulating the 20-char truncation. When several
  products share the same first 20 characters (e.g. `Magic Find Booster -` = 1 Hour $3.99 or
  3 Hours $9.99) the purchase is flagged **ambiguous**, booked at the `AMBIGUOUS_PRICE_POLICY`
  price (avg by default) with the min/max range kept, and can be resolved by hand in the
  dashboard (Live Feed → resolve). Totals show `≈` while unresolved ranges exist.
* Purchase time comes from the widget's own HH:MM (accurate to the minute) once the widget
  clock's UTC offset is known; it is auto-calibrated from live detections (the store's
  labels are UTC as of writing). Cloudflare caches the storefront ~90 s and Tebex itself lags
  a few minutes, so "when the bot noticed" is only used as a fallback.
* Optional Discord webhook embed for each new purchase.
* An ignore list for your own test purchases / staff: open a player in the dashboard and hit
  **Ignore player** (deletes their history and skips them from then on), or list names/UUIDs in
  `IGNORED_PLAYERS`. Managed from the Status page.

Amounts are catalog prices by default. If a known checkout sale is running, set
`CHECKOUT_DISCOUNT_PERCENT` and `CHECKOUT_DISCOUNT_MIN_BASKET` so new purchases are booked
as estimated sale revenue. Coupons, gift cards, sales-tax, currency conversion and exact basket
contents are still not visible on the public widget, so totals are estimates.

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env            # set STORE_URL to your store
python -m dragtrack             # open http://localhost:8787
```

Other modes:

```bash
python -m dragtrack --once      # poll one time and exit (good for testing)
python -m dragtrack --catalog   # refresh the product catalog and exit
python -m dragtrack --no-web    # tracker only
python -m pytest tests -q       # run the tests
```

## Privacy

Purchases arrive attached to a real person — a username and a Minecraft UUID. Aggregate
revenue is fair to publish; one named individual's spending history is not, and under
PIPEDA it is personal information the moment it is used commercially.

So **`ANONYMIZE` is on by default**. Buyer names and UUIDs are replaced with stable salted
handles (`GodIsAFurry` → `Player-8f8577`) in the API, the CSV export and the Discord embed
— including the embed's avatar, whose URL is built from the UUID and would otherwise hand
back the identity the handle was hiding. Storage keeps the real values, which the tracker
needs to align snapshots and to honour the ignore list.

Handles are **stable**, so the same buyer is the same handle across rows and days and
per-player analysis still works — a random handle per row would protect the same
information and destroy the analysis with it. They are **salted**, because Minecraft
usernames are a small public namespace and an unsalted hash is recovered by hashing a name
list. The salt is generated once into `data/anon.salt`, or pin it with `ANON_SALT`.

Set `ANONYMIZE=0` only for a private instance behind auth that nobody else can reach.

## Deploying to a VPS

### Option A — systemd (Debian/Ubuntu)

```bash
git clone <this repo> dragtrack && cd dragtrack
sudo bash deploy/install.sh          # creates /opt/dragtrack, a venv, a service user, enables the service
sudo nano /opt/dragtrack/.env        # set DASHBOARD_PASSWORD (and DISCORD_WEBHOOK_URL if you want)
sudo systemctl restart dragtrack
sudo journalctl -u dragtrack -f      # logs
```

### Option B — Docker

```bash
cp .env.example .env && nano .env
docker compose up -d --build
docker compose logs -f
```

The SQLite database lives in `./data/dragtrack.db` (or the `/data` volume in Docker). Back
that file up and you have everything.

### Exposing the dashboard

Set `DASHBOARD_PASSWORD` in `.env` — the dashboard then shows a login page (`DASHBOARD_USER`,
default `admin`) and keeps you signed in with a 30-day cookie; `/logout` ends the session. The API
also accepts HTTP basic auth for scripts (`curl -u admin:… /api/health`). Five wrong passwords from
one IP lock it out for a minute. Put it behind HTTPS: `deploy/Caddyfile` is a two-line reverse
proxy with automatic certificates. Or keep the port firewalled and reach it over an SSH tunnel:
`ssh -L 8787:127.0.0.1:8787 user@vps` then open http://localhost:8787.

## Dashboard

| Page | What's there |
|---|---|
| Overview | Revenue all-time / today / 7d / 30d with deltas & sparklines, avg order, top spender, the store's own "Top Customer", review queue, revenue & purchase counts per day, category share, top players, recent purchases |
| Live Feed | Every purchase (newest first) with filters (range, category, live/seeded, needs review), search, CSV export, per-purchase **resolve/edit/delete** |
| Players | Ranked cards per player: total spent, purchases, avg, favourite product, category mix, first/last seen; click for a detail view with monthly spend, per-product breakdown, full history and previous usernames |
| Products | Catalog grouped by category with price, sale price, subscription tag, sold count, revenue, share, last sold, price-change history; manual catalog refresh |
| Analytics | Cumulative revenue, weekly revenue by category, weekday × hour heatmap, top products, order-size distribution, new vs returning buyers, peak hour/day, repeat-buyer rate |
| Status | Tracker health (last poll, success rate, latency, TLS profile, Cloudflare block detection), widget snapshot, clock calibration, events (new products, price changes, renames, anomalies) and live logs |

The dashboard polls the API every 10 s; new purchases pop up as toasts. Theme dots at the
bottom-left switch the accent colour (remembered per browser). `/` focuses search.

## API

All JSON, under `/api/` (interactive docs at `/api/docs`):

| Endpoint | Purpose |
|---|---|
| `GET /api/bootstrap` | config, products, players, health, events in one call |
| `GET /api/purchases?since_id=&limit=&offset=&uuid=` | purchases (ascending id; poll with `since_id` for new ones) |
| `GET /api/players` · `GET /api/products` · `GET /api/events` · `GET /api/logs` · `GET /api/health` | |
| `POST /api/purchases/{id}/resolve` `{package_id}` / `{price}` / `{reset:true}` | pin an ambiguous purchase to a product or custom price |
| `DELETE /api/purchases/{id}` | remove a false positive |
| `POST /api/catalog/refresh` | re-crawl the catalog now |
| `GET /api/export.csv` | everything as CSV |

## Configuration

See `.env.example`. Notables: `POLL_INTERVAL`, `SEED_EXISTING` (import the 5 purchases already
visible on first start), `AMBIGUOUS_PRICE_POLICY` (`min|max|avg`), `CHECKOUT_DISCOUNT_PERCENT`
and `CHECKOUT_DISCOUNT_MIN_BASKET` (known checkout sale estimate), `WIDGET_TZ_OFFSET_MIN`
(force the widget clock's offset from UTC instead of auto-calibrating), `DISCORD_WEBHOOK_URL`,
`DASHBOARD_PASSWORD`, `CURRENCY`.

## Layout

```
dragtrack/
  config.py     environment / .env settings
  scraper.py    curl_cffi client (Cloudflare-safe) + HTML parsing of widget & catalog
  tracker.py    poll loop, snapshot alignment, product matching, time calibration, health
  db.py         SQLite schema & queries
  api.py        FastAPI app + static dashboard
  auth.py       login page, signed session cookie, basic auth for the API
  notify.py     Discord webhook
  static/       dashboard (index.html, app.js, style.css — no build step)
tests/          pytest suite with saved HTML fixtures of the real store
deploy/         systemd unit, install script, Caddyfile
```
