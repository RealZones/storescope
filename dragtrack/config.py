"""Configuration — read from environment variables, with an optional .env file.

Every setting has a sensible default so `python -m dragtrack` works out of the box.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE lines, # comments). Never overrides real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:  # "KEY=value  # note" -> "value" (systemd's EnvironmentFile would keep the note; we don't)
            value = re.split(r"\s+#", value, 1)[0].strip()
        os.environ.setdefault(key, value)


_load_dotenv(Path(os.environ.get("DRAGTRACK_ENV", ".env")))


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


VERSION = "1.2.0"

# --- Store -----------------------------------------------------------------
# No default on purpose. A store baked in here is a store every fresh clone
# starts polling without being asked, and it names one shop's revenue as this
# tool's subject. Point it at your own: STORE_URL=https://yourstore.tebex.io
STORE_URL = os.environ.get("STORE_URL", "").rstrip("/")
STORE_NAME = os.environ.get("STORE_NAME", "") or (
    STORE_URL.split("//")[-1].split(".")[0].title() if STORE_URL else "Store")
CURRENCY = os.environ.get("CURRENCY", "USD")

# --- Polling ---------------------------------------------------------------
POLL_INTERVAL = max(10, _int("POLL_INTERVAL", 30))          # seconds between widget polls
CATALOG_INTERVAL = max(300, _int("CATALOG_INTERVAL", 3600))  # seconds between catalog refreshes
FETCH_TIMEOUT = _int("FETCH_TIMEOUT", 30)
IMPERSONATE = os.environ.get("IMPERSONATE", "chrome")       # curl_cffi browser profile
# Import the purchases already visible in the widget on first start (source="seed").
SEED_EXISTING = _bool("SEED_EXISTING", True)
# Offset (minutes) of the widget's HH:MM clock from UTC, e.g. -240 for UTC-4. Leave unset to
# auto-calibrate from live purchases (assumes UTC until enough have been seen).
_raw_tz = os.environ.get("WIDGET_TZ_OFFSET_MIN", "").strip()
WIDGET_TZ_OFFSET_MIN: int | None = int(_raw_tz) if _raw_tz.lstrip("-+").isdigit() else None
# When a truncated widget name matches several products: min | max | avg
AMBIGUOUS_PRICE_POLICY = os.environ.get("AMBIGUOUS_PRICE_POLICY", "avg").lower()
if AMBIGUOUS_PRICE_POLICY not in ("min", "max", "avg"):
    AMBIGUOUS_PRICE_POLICY = "avg"

# Optional checkout sale estimate. Tebex's public widget does not show coupons or basket totals,
# so this is only applied when you explicitly know a store-wide sale is running.
CHECKOUT_DISCOUNT_PERCENT = max(0.0, min(100.0, _float("CHECKOUT_DISCOUNT_PERCENT", 0.0)))
CHECKOUT_DISCOUNT_MIN_BASKET = max(0.0, _float("CHECKOUT_DISCOUNT_MIN_BASKET", 0.0))

# Players whose purchases are never recorded (your own test buys, staff): usernames or UUIDs, comma-separated.
# The dashboard can add/remove players too; both lists are merged.
IGNORED_PLAYERS = [x.strip() for x in os.environ.get("IGNORED_PLAYERS", "").split(",") if x.strip()]

# --- Storage ---------------------------------------------------------------
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR / "dragtrack.db")))

# --- Privacy ---------------------------------------------------------------
# ON by default: the safe configuration should be the one you get by doing
# nothing. Buyer names and UUIDs are replaced with stable salted handles in the
# API, the CSV export and the Discord embed. Storage is untouched, so the
# tracker can still align snapshots and honour the ignore list.
#
# Turn it off only for an instance nobody else can reach - your own store, your
# own dashboard, behind auth. See privacy.py for why the handles are salted.
ANONYMIZE = _bool("ANONYMIZE", True)
# Set to pin handles across machines; otherwise generated into DATA_DIR/anon.salt.
ANON_SALT = os.environ.get("ANON_SALT", "").strip()

# --- Web / dashboard -------------------------------------------------------
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _int("PORT", 8787)
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")  # empty = no auth

# --- Notifications ---------------------------------------------------------
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_NOTIFY_SEED = _bool("DISCORD_NOTIFY_SEED", False)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
