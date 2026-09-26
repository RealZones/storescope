"""The polling loop: fetch the store, diff the Recent Payments widget, record purchases."""
from __future__ import annotations

import collections
import logging
import re
import statistics
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config
from .db import Database, utcnow
from .notify import DiscordNotifier
from .scraper import (ChallengeError, FetchError, StoreClient, WidgetEntry, is_store_page, match_candidates,
                      normalize_name, parse_recent_payments, parse_top_customer, scrape_catalog)

log = logging.getLogger("dragtrack.tracker")


# --------------------------------------------------------------------------
# Log ring buffer (shown on the dashboard's Status page)
# --------------------------------------------------------------------------
class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 400):
        super().__init__()
        self.records: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append({
                "at": datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record) if self.formatter else record.getMessage(),
            })
        except Exception:  # pragma: no cover
            pass


LOG_BUFFER = RingBufferHandler()
LOG_BUFFER.setFormatter(logging.Formatter("%(message)s"))


# --------------------------------------------------------------------------
# Widget time parsing (only needed to date seeded entries)
# --------------------------------------------------------------------------
_DATE_FORMATS = (
    "%d %b %Y %H:%M", "%d %B %Y %H:%M", "%b %d, %Y %H:%M", "%B %d, %Y %H:%M",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
    "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%d %b %H:%M", "%b %d %H:%M", "%d %b", "%b %d",
)
_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def parse_widget_time(text: str, now_utc: datetime, tz_offset_min: int = 0) -> datetime | None:
    """Best-effort conversion of the widget's time label ('Today 02:15', 'Yesterday 21:58', '12 Sep 2026 ...') to UTC."""
    s = normalize_name(text)
    if not s:
        return None
    tz = timezone(timedelta(minutes=tz_offset_min))
    now_local = now_utc.astimezone(tz)

    m = re.match(r"^(today|yesterday)(?:\s*,?\s*(?:at\s+)?(\d{1,2}):(\d{2})\s*(am|pm)?)?$", s, re.I)
    if m:
        day = now_local.date() - timedelta(days=1 if m.group(1).lower() == "yesterday" else 0)
        hour, minute = int(m.group(2) or 0), int(m.group(3) or 0)
        if m.group(4):
            hour = hour % 12 + (12 if m.group(4).lower() == "pm" else 0)
        dt = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        # Tebex computes the "Today" label and the HH:MM on different clocks; a "today" that is
        # still in the future actually happened the day before.
        if dt > now_local + timedelta(minutes=5):
            dt -= timedelta(days=1)
        return dt.astimezone(timezone.utc)

    m = re.match(r"^(?:about\s+)?(\d+)\s*(second|minute|hour|day|week)s?\s+ago$", s, re.I)
    if m:
        return now_utc - timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()])

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if dt.year == 1900:  # format without a year
            dt = dt.replace(year=now_local.year)
            if dt.replace(tzinfo=tz) > now_local + timedelta(days=1):
                dt = dt.replace(year=now_local.year - 1)
        return dt.replace(tzinfo=tz).astimezone(timezone.utc)
    return None


# --------------------------------------------------------------------------
# Snapshot alignment
# --------------------------------------------------------------------------
def align(new_keys: list[str], old_keys: list[str], min_overlap: int) -> tuple[int, int] | None:
    """Find how the new widget list overlaps the previous one.

    Returns (k, j): the first k entries of `new_keys` are new; new_keys[k:] lines up with old_keys[j:].
    Prefers the smallest k (fewest new purchases), then the smallest j. None if the lists don't overlap.
    """
    n, o = len(new_keys), len(old_keys)
    for k in range(n):
        for j in range(o):
            overlap = min(n - k, o - j)
            if overlap < min_overlap:
                break
            if new_keys[k:k + overlap] == old_keys[j:j + overlap]:
                return k, j
    return None


def common_prefix(names: list[str]) -> str:
    if not names:
        return ""
    prefix = names[0]
    for name in names[1:]:
        while not name.startswith(prefix):
            prefix = prefix[:-1]
    return prefix.strip(" -–:")


def estimated_checkout_price(price: float | None) -> float | None:
    if price is None:
        return None
    if config.CHECKOUT_DISCOUNT_PERCENT <= 0:
        return price
    if config.CHECKOUT_DISCOUNT_MIN_BASKET and price < config.CHECKOUT_DISCOUNT_MIN_BASKET:
        return price
    multiplier = 1.0 - (config.CHECKOUT_DISCOUNT_PERCENT / 100.0)
    return round(price * multiplier, 2)


# --------------------------------------------------------------------------
# Tracker
# --------------------------------------------------------------------------
class Tracker:
    def __init__(self, db: Database, client: StoreClient, notifier: DiscordNotifier | None = None):
        self.db = db
        self.client = client
        self.notifier = notifier
        self.started_at = utcnow()
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self.blocked = False
        self._catalog_requested = threading.Event()
        self._lock = threading.Lock()
        self._last_ok_monotonic: float | None = None

    # ---- product resolution ---------------------------------------------------
    def resolve(self, product_raw: str, products: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
        if products is None:
            products = {p["package_id"]: p for p in self.db.products()}
        candidates = match_candidates(product_raw, [(pid, p["name"]) for pid, p in products.items()])
        # If both an active and an inactive product match, trust the active ones.
        active = [c for c in candidates if products[c].get("active")]
        if active and len(active) < len(candidates):
            candidates = active
        result: dict[str, Any] = {
            "package_id": None, "product_name": product_raw, "category": None,
            "price": None, "price_min": None, "price_max": None,
            "ambiguous": False, "candidates": candidates, "resolved": False,
        }
        if not candidates:
            return result
        if len(candidates) == 1:
            p = products[candidates[0]]
            price = estimated_checkout_price(p["price"])
            result.update(package_id=p["package_id"], product_name=p["name"], category=p["category"],
                          price=price, price_min=price, price_max=price)
            return result
        prices = [estimated_checkout_price(products[c]["price"]) for c in candidates if products[c]["price"] is not None]
        prices = [p for p in prices if p is not None]
        categories = {products[c]["category"] for c in candidates}
        result["ambiguous"] = True
        result["category"] = categories.pop() if len(categories) == 1 else None
        result["product_name"] = common_prefix([products[c]["name"] for c in candidates]) or product_raw
        if prices:
            lo, hi = min(prices), max(prices)
            result["price_min"], result["price_max"] = lo, hi
            policy = config.AMBIGUOUS_PRICE_POLICY
            result["price"] = lo if policy == "min" else hi if policy == "max" else round(sum(prices) / len(prices), 2)
        return result

    # ---- diffing ----------------------------------------------------------------
    def diff(self, entries: list[WidgetEntry]) -> tuple[list[WidgetEntry], str, int]:
        """Return (new_entries newest-first, method, vanished_count)."""
        snapshot = self.db.get_state("snapshot")
        if not snapshot:
            return list(entries), ("seed" if config.SEED_EXISTING else "skip"), 0
        old_strict: list[str] = snapshot.get("strict", [])
        old_loose: list[str] = snapshot.get("loose", [])
        min_overlap = max(1, min(2, len(old_strict)))
        res = align([e.strict_key for e in entries], old_strict, min_overlap)
        method = "strict"
        if res is None:
            res = align([e.loose_key for e in entries], old_loose, min_overlap)
            method = "loose"
        if res is None:
            # Nothing lines up: more purchases than widget slots since the last poll, or the page changed.
            # Fall back to the database so a restart / glitch can't double-count.
            known = self.db.recent_fingerprints(36)
            return [e for e in entries if e.strict_key not in known], "no-overlap", 0
        k, j = res
        return list(entries[:k]), method, j

    # ---- ignore list -----------------------------------------------------------------
    def ignore_sets(self) -> tuple[set[str], set[str]]:
        """(uuids, lowercase usernames) that must never be recorded: dashboard list + IGNORED_PLAYERS env."""
        uuids: set[str] = set()
        names: set[str] = set()
        for e in self.db.ignored_players():
            if e.get("uuid"):
                uuids.add(e["uuid"].lower())
            if e.get("username"):
                names.add(e["username"].lower())
        for x in config.IGNORED_PLAYERS:
            (uuids if re.fullmatch(r"[0-9a-fA-F]{32}", x) else names).add(x.lower())
        return uuids, names

    def is_ignored(self, entry: WidgetEntry, sets: tuple[set[str], set[str]] | None = None) -> bool:
        uuids, names = sets or self.ignore_sets()
        return entry.uuid.lower() in uuids or entry.username.lower() in names

    # ---- recording ----------------------------------------------------------------
    def record(self, entry: WidgetEntry, source: str, now_iso: str, products: dict[int, dict[str, Any]]) -> dict[str, Any]:
        resolved = self.resolve(entry.product_raw, products)
        purchased_at = now_iso
        # Prefer the widget's own HH:MM (exact to the minute) over "when the bot noticed", as long as
        # we know the widget clock's offset from UTC and the result is plausible.
        now_dt = datetime.now(timezone.utc)
        offset = self.effective_offset()
        dt = parse_widget_time(entry.widget_time, now_dt, offset) if offset is not None else None
        if dt is not None and (source == "seed" or (now_dt - timedelta(hours=12) <= dt <= now_dt + timedelta(minutes=5))):
            purchased_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        previous_name = self.db.touch_player(entry.uuid, entry.username, entry.avatar, purchased_at)
        if previous_name:
            self.db.add_event("username_change", f"{previous_name} is now known as {entry.username}",
                              {"uuid": entry.uuid, "old": previous_name, "new": entry.username})
        purchase = {
            "uuid": entry.uuid, "username": entry.username, "product_raw": entry.product_raw,
            **resolved,
            "widget_time": entry.widget_time, "purchased_at": purchased_at, "detected_at": now_iso,
            "source": source, "fingerprint": entry.strict_key,
        }
        purchase["id"] = self.db.insert_purchase(purchase)
        price_txt = "?" if purchase["price"] is None else f"{purchase['price']:.2f}"
        log.info("%s purchase #%d: %s bought %s (%s%s) [%s]", source.upper(), purchase["id"], entry.username,
                 purchase["product_name"], price_txt, " ambiguous" if purchase["ambiguous"] else "", entry.widget_time)
        if resolved["package_id"] is None and not resolved["candidates"]:
            self.db.add_event("unknown_product", f"Unknown product in widget: '{entry.product_raw}' (bought by {entry.username})",
                              {"purchase_id": purchase["id"], "product_raw": entry.product_raw})
        return purchase

    def _observe_widget_clock(self, entries: list[WidgetEntry], now: datetime) -> None:
        """Record (detection time - widget HH:MM) for purchases seen while polling continuously.

        The widget clock sits at a fixed offset from UTC; the residual is just our detection delay, so the
        median of these observations (rounded to the hour) calibrates the widget's timezone automatically.
        """
        if self._last_ok_monotonic is None or time.monotonic() - self._last_ok_monotonic > config.POLL_INTERVAL * 3:
            return  # we were down or failing; the delay would pollute the estimate
        obs = self.db.get_state("widget_clock_offsets", [])
        for e in entries:
            if not e.hhmm:
                continue
            h, m = e.hhmm.split(":")
            widget_min = int(h) * 60 + int(m)
            now_min = now.hour * 60 + now.minute
            delta = (now_min - widget_min + 720) % 1440 - 720  # (-720, 720]
            obs.append(delta)
        self.db.set_state("widget_clock_offsets", obs[-40:])

    def widget_clock_offset(self) -> int | None:
        """Raw median of (detection - widget) in minutes, or None before any observation."""
        obs = self.db.get_state("widget_clock_offsets", [])
        return int(statistics.median(obs)) if obs else None

    def effective_offset(self) -> int | None:
        """Widget clock offset from UTC in minutes: configured value, else auto-calibrated, else assume UTC."""
        if config.WIDGET_TZ_OFFSET_MIN is not None:
            return config.WIDGET_TZ_OFFSET_MIN
        obs = self.db.get_state("widget_clock_offsets", [])
        if len(obs) >= 3:
            # "detection - widget" is positive when the widget clock is behind UTC, so the tz offset is the negative
            return -int(round(statistics.median(obs) / 60.0)) * 60
        return 0

    def _track_top_customer(self, html: str) -> None:
        top = parse_top_customer(html)
        if top is None:
            return
        current = self.db.get_state("top_customer")
        payload = {"username": top.username, "uuid": top.uuid, "text": top.text, "seen_at": utcnow()}
        if not current or current.get("username") != top.username:
            if current:
                self.db.add_event("top_customer", f"Top customer changed: {current.get('username')} -> {top.username}", payload)
            self.db.set_state("top_customer", payload)
        else:
            current["seen_at"] = payload["seen_at"]
            self.db.set_state("top_customer", current)

    # ---- one poll -------------------------------------------------------------------
    def poll_once(self) -> int:
        """Fetch + diff + record. Returns the number of purchases recorded."""
        try:
            res = self.client.fetch("/")
        except ChallengeError as exc:
            self._fail(None, str(exc), blocked=True)
            return 0
        except FetchError as exc:
            self._fail(None, str(exc))
            return 0

        if not is_store_page(res.text):
            self._fail(res.status, "Response did not look like the store page", latency=res.latency_ms, profile=res.profile)
            return 0
        entries = parse_recent_payments(res.text)
        if not entries:
            self._fail(res.status, "Recent Payments widget not found or empty", latency=res.latency_ms, profile=res.profile)
            return 0

        new_entries, method, vanished = self.diff(entries)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        recorded: list[tuple[dict[str, Any], WidgetEntry]] = []
        if method != "skip" and new_entries:
            products = {p["package_id"]: p for p in self.db.products()}
            source = "seed" if method == "seed" else "live"
            ignore = self.ignore_sets()
            for entry in reversed(new_entries):  # oldest first so ids stay chronological
                if self.is_ignored(entry, ignore):
                    log.info("Ignored purchase by %s (%s): player is on the ignore list", entry.username, entry.product_raw)
                    continue
                recorded.append((self.record(entry, source, now_iso, products), entry))
            if source == "live":
                self._observe_widget_clock(new_entries, now_dt)
        if vanished:
            self.db.add_event("widget_anomaly", f"{vanished} widget entr{'y' if vanished == 1 else 'ies'} disappeared without being pushed out (refund?)",
                              {"vanished": vanished})
        if method == "no-overlap" and self.db.get_state("snapshot"):
            log.warning("Widget did not overlap the previous snapshot; recorded %d after DB de-dup", len(recorded))

        self.db.set_state("snapshot", {
            "strict": [e.strict_key for e in entries],
            "loose": [e.loose_key for e in entries],
            "at": now_iso,
        })
        self._track_top_customer(res.text)
        self.db.add_poll(True, res.status, res.latency_ms, len(entries), len(recorded), res.profile, None)
        if self.consecutive_failures:
            self.db.add_event("recovered", f"Polling recovered after {self.consecutive_failures} failure(s)")
        self.consecutive_failures = 0
        self.blocked = False
        self.last_error = None
        self._last_ok_monotonic = time.monotonic()

        if self.notifier and self.notifier.enabled:
            for purchase, entry in recorded:
                if purchase["source"] == "live" or config.DISCORD_NOTIFY_SEED:
                    self.notifier.purchase(purchase, entry.avatar)
        return len(recorded)

    def _fail(self, status: int | None, error: str, latency: int | None = None, profile: str | None = None,
              blocked: bool = False) -> None:
        self.consecutive_failures += 1
        self.last_error = error
        self.blocked = blocked
        log.error("Poll failed (%d in a row): %s", self.consecutive_failures, error)
        self.db.add_poll(False, status, latency, 0, 0, profile or self.client.last_profile, error)
        if self.consecutive_failures in (3, 10, 50):
            self.db.add_event("blocked" if blocked else "poll_failures",
                              f"{self.consecutive_failures} consecutive poll failures: {error}")

    # ---- catalog ----------------------------------------------------------------------
    def refresh_catalog(self) -> bool:
        log.info("Refreshing product catalog...")
        products, errors = scrape_catalog(self.client)
        for err in errors:
            log.warning("Catalog: %s", err)
        if not products:
            log.error("Catalog refresh found no products; keeping existing catalog")
            return False
        changes = self.db.upsert_products([asdict(p) for p in products])
        for it in changes["new"]:
            self.db.add_event("product_new", f"New product: {it['name']} ({it['currency_symbol']}{it['price']})", it)
        for it in changes["price_changed"]:
            self.db.add_event("price_change", f"Price change: {it['name']} {it['old_price']} -> {it['price']}", it)
        for it in changes["removed"]:
            self.db.add_event("product_removed", f"Product removed from store: {it['name']}", {"package_id": it["package_id"]})
        self.db.set_state("catalog_refreshed_at", utcnow())
        log.info("Catalog: %d products (%d new, %d price changes, %d removed)", len(products), len(changes["new"]),
                 len(changes["price_changed"]), len(changes["removed"]))
        if changes["new"]:
            self.re_resolve_unknown()
        return True

    def re_resolve_unknown(self) -> int:
        """Retry purchases whose product wasn't in the catalog when they were recorded."""
        products = {p["package_id"]: p for p in self.db.products()}
        fixed = 0
        for p in self.db.query("SELECT * FROM purchases WHERE package_id IS NULL AND resolved = 0 AND candidates = '[]'"):
            r = self.resolve(p["product_raw"], products)
            if r["candidates"]:
                self.db.update_purchase(p["id"], package_id=r["package_id"], product_name=r["product_name"], category=r["category"],
                                        price=r["price"], price_min=r["price_min"], price_max=r["price_max"],
                                        ambiguous=1 if r["ambiguous"] else 0, candidates=r["candidates"])
                fixed += 1
        if fixed:
            log.info("Re-resolved %d purchase(s) against the refreshed catalog", fixed)
        return fixed

    def request_catalog_refresh(self) -> None:
        self._catalog_requested.set()

    # ---- main loop ----------------------------------------------------------------------
    def run(self, stop: threading.Event) -> None:
        self.started_at = utcnow()
        log.info("DragTrack %s tracking %s every %ds", config.VERSION, config.STORE_URL, config.POLL_INTERVAL)
        try:
            self.refresh_catalog()
        except Exception:
            log.exception("Initial catalog refresh failed")
        next_catalog = time.monotonic() + config.CATALOG_INTERVAL
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                with self._lock:
                    self.poll_once()
            except Exception:
                log.exception("Poll crashed")
                self.consecutive_failures += 1
            if self._catalog_requested.is_set() or time.monotonic() >= next_catalog:
                self._catalog_requested.clear()
                try:
                    with self._lock:
                        self.refresh_catalog()
                except Exception:
                    log.exception("Catalog refresh failed")
                next_catalog = time.monotonic() + config.CATALOG_INTERVAL
            interval = config.POLL_INTERVAL
            if self.consecutive_failures:
                interval = min(config.POLL_INTERVAL * (2 ** min(self.consecutive_failures, 5)), 600)
            stop.wait(max(1.0, interval - (time.monotonic() - t0)))
        log.info("Tracker stopped")

    # ---- health ----------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        stats = self.db.poll_stats()
        total = stats["total"] or 0
        if total == 0:
            status = "starting"
        elif self.blocked and self.consecutive_failures >= 2:
            status = "blocked"
        elif self.consecutive_failures >= 1:
            status = "degraded"
        else:
            status = "ok"
        started = datetime.strptime(self.started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        counts = self.db.one("SELECT (SELECT COUNT(*) FROM purchases) AS purchases, (SELECT COUNT(*) FROM players) AS players, "
                             "(SELECT COUNT(*) FROM products WHERE active = 1) AS products") or {}
        return {
            "status": status,
            "version": config.VERSION,
            "started_at": self.started_at,
            "uptime_s": int((datetime.now(timezone.utc) - started).total_seconds()),
            "poll_interval": config.POLL_INTERVAL,
            "catalog_interval": config.CATALOG_INTERVAL,
            "catalog_refreshed_at": self.db.get_state("catalog_refreshed_at"),
            "profile": self.client.last_profile if self.client else None,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error if self.consecutive_failures else None,  # only while actually failing
            "last_failure": stats["last_error"],  # most recent failed poll ever, for the Status page
            "last_poll": stats["last"],
            "last_ok": stats["last_ok"],
            "success_rate": round(100 * stats["ok_count"] / total, 1) if total else None,
            "avg_latency_ms": stats["avg_latency_ms"],
            "recent_polls": stats["recent"],
            "db_size": self.db.db_size_bytes(),
            "counts": counts,
            "widget_clock_offset_min": self.widget_clock_offset(),
            "widget_tz_offset_min": self.effective_offset(),
            "widget_tz_source": "configured" if config.WIDGET_TZ_OFFSET_MIN is not None else
                                ("auto" if len(self.db.get_state("widget_clock_offsets", [])) >= 3 else "assumed UTC"),
            "widget_clock_samples": len(self.db.get_state("widget_clock_offsets", [])),
            "snapshot": self.db.get_state("snapshot"),
            "top_customer": self.db.get_state("top_customer"),
            "discord": bool(self.notifier and self.notifier.enabled),
            "ignored_players": self.db.ignored_players() + [
                {"uuid": x.lower() if re.fullmatch(r"[0-9a-fA-F]{32}", x) else None,
                 "username": None if re.fullmatch(r"[0-9a-fA-F]{32}", x) else x, "source": "env"}
                for x in config.IGNORED_PLAYERS],
            "seed_existing": config.SEED_EXISTING,
            "ambiguous_policy": config.AMBIGUOUS_PRICE_POLICY,
            "checkout_discount_percent": config.CHECKOUT_DISCOUNT_PERCENT,
            "checkout_discount_min_basket": config.CHECKOUT_DISCOUNT_MIN_BASKET,
        }
