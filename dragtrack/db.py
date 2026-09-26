"""SQLite storage. One connection shared between the tracker thread and the web server, guarded by a lock."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    package_id      INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    category_slug   TEXT,
    type            TEXT DEFAULT 'single',
    price           REAL,
    original_price  REAL,
    currency_symbol TEXT DEFAULT '',
    image           TEXT DEFAULT '',
    url             TEXT DEFAULT '',
    first_seen      TEXT,
    last_seen       TEXT,
    active          INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id  INTEGER NOT NULL,
    price       REAL,
    original_price REAL,
    seen_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    uuid        TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    avatar      TEXT DEFAULT '',
    first_seen  TEXT,
    last_seen   TEXT,
    aliases     TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid         TEXT NOT NULL,
    username     TEXT NOT NULL,
    product_raw  TEXT NOT NULL,
    package_id   INTEGER,
    product_name TEXT,
    category     TEXT,
    price        REAL,
    price_min    REAL,
    price_max    REAL,
    ambiguous    INTEGER DEFAULT 0,
    candidates   TEXT DEFAULT '[]',
    resolved     INTEGER DEFAULT 0,
    widget_time  TEXT DEFAULT '',
    purchased_at TEXT NOT NULL,
    detected_at  TEXT NOT NULL,
    source       TEXT DEFAULT 'live',
    fingerprint  TEXT
);
CREATE INDEX IF NOT EXISTS idx_purchases_uuid ON purchases(uuid);
CREATE INDEX IF NOT EXISTS idx_purchases_fp ON purchases(fingerprint);
CREATE INDEX IF NOT EXISTS idx_purchases_time ON purchases(purchased_at);
CREATE TABLE IF NOT EXISTS polls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    status     INTEGER,
    latency_ms INTEGER,
    entries    INTEGER,
    new_count  INTEGER,
    profile    TEXT,
    error      TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    type    TEXT NOT NULL,
    message TEXT NOT NULL,
    data    TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---- low level ---------------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- state -------------------------------------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM state WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def set_state(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, json.dumps(value)))

    # ---- events ------------------------------------------------------------
    def add_event(self, type_: str, message: str, data: dict | None = None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO events(at, type, message, data) VALUES(?,?,?,?)",
                      (utcnow(), type_, message, json.dumps(data or {})))
            c.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 2000)")

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            try:
                r["data"] = json.loads(r["data"] or "{}")
            except ValueError:
                r["data"] = {}
        return rows

    # ---- polls -------------------------------------------------------------
    def add_poll(self, ok: bool, status: int | None, latency_ms: int | None, entries: int, new_count: int,
                 profile: str | None, error: str | None) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO polls(at, ok, status, latency_ms, entries, new_count, profile, error) VALUES(?,?,?,?,?,?,?,?)",
                      (utcnow(), 1 if ok else 0, status, latency_ms, entries, new_count, profile, error))
            c.execute("DELETE FROM polls WHERE id NOT IN (SELECT id FROM polls ORDER BY id DESC LIMIT 5000)")

    def poll_stats(self) -> dict[str, Any]:
        last = self.one("SELECT * FROM polls ORDER BY id DESC LIMIT 1")
        last_ok = self.one("SELECT * FROM polls WHERE ok = 1 ORDER BY id DESC LIMIT 1")
        last_err = self.one("SELECT * FROM polls WHERE ok = 0 ORDER BY id DESC LIMIT 1")
        agg = self.one("""SELECT COUNT(*) AS total, SUM(ok) AS ok_count, AVG(CASE WHEN ok=1 THEN latency_ms END) AS avg_latency
                          FROM polls WHERE id IN (SELECT id FROM polls ORDER BY id DESC LIMIT 500)""") or {}
        consecutive = 0
        for row in self.query("SELECT ok FROM polls ORDER BY id DESC LIMIT 100"):
            if row["ok"]:
                break
            consecutive += 1
        recent = self.query("SELECT at, ok, latency_ms, new_count FROM polls ORDER BY id DESC LIMIT 120")
        return {
            "last": last, "last_ok": last_ok, "last_error": last_err,
            "total": agg.get("total") or 0, "ok_count": agg.get("ok_count") or 0,
            "avg_latency_ms": round(agg["avg_latency"]) if agg.get("avg_latency") else None,
            "consecutive_failures": consecutive,
            "recent": list(reversed(recent)),
        }

    # ---- products ------------------------------------------------------------
    def products(self, include_inactive: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM products" + ("" if include_inactive else " WHERE active = 1")
        return self.query(sql + " ORDER BY category, price DESC, name")

    def product_names(self) -> list[tuple[int, str]]:
        return [(r["package_id"], r["name"]) for r in self.query("SELECT package_id, name FROM products")]

    def upsert_products(self, items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Insert/update catalog rows. Returns {'new': [...], 'price_changed': [...], 'removed': [...]}"""
        now = utcnow()
        changes: dict[str, list[dict[str, Any]]] = {"new": [], "price_changed": [], "removed": []}
        seen_ids = {it["package_id"] for it in items}
        with self.tx() as c:
            existing = {r["package_id"]: dict(r) for r in c.execute("SELECT * FROM products").fetchall()}
            for it in items:
                old = existing.get(it["package_id"])
                if old is None:
                    c.execute("""INSERT INTO products(package_id, name, category, category_slug, type, price, original_price,
                                 currency_symbol, image, url, first_seen, last_seen, active)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                              (it["package_id"], it["name"], it["category"], it["category_slug"], it["type"], it["price"],
                               it["original_price"], it["currency_symbol"], it["image"], it["url"], now, now))
                    c.execute("INSERT INTO price_history(package_id, price, original_price, seen_at) VALUES(?,?,?,?)",
                              (it["package_id"], it["price"], it["original_price"], now))
                    changes["new"].append(it)
                else:
                    if old["price"] != it["price"] or old["original_price"] != it["original_price"]:
                        c.execute("INSERT INTO price_history(package_id, price, original_price, seen_at) VALUES(?,?,?,?)",
                                  (it["package_id"], it["price"], it["original_price"], now))
                        changes["price_changed"].append({**it, "old_price": old["price"]})
                    c.execute("""UPDATE products SET name=?, category=?, category_slug=?, type=?, price=?, original_price=?,
                                 currency_symbol=?, image=?, url=?, last_seen=?, active=1 WHERE package_id=?""",
                              (it["name"], it["category"], it["category_slug"], it["type"], it["price"], it["original_price"],
                               it["currency_symbol"], it["image"], it["url"], now, it["package_id"]))
            for pid, old in existing.items():
                if pid not in seen_ids and old["active"]:
                    c.execute("UPDATE products SET active = 0 WHERE package_id = ?", (pid,))
                    changes["removed"].append(old)
        return changes

    def price_history(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM price_history ORDER BY seen_at")

    # ---- players -------------------------------------------------------------
    def players(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM players ORDER BY last_seen DESC")
        for r in rows:
            try:
                r["aliases"] = json.loads(r["aliases"] or "[]")
            except ValueError:
                r["aliases"] = []
        return rows

    def touch_player(self, uuid: str, username: str, avatar: str, at: str) -> str | None:
        """Create/update a player row. Returns the previous username if it changed."""
        with self.tx() as c:
            row = c.execute("SELECT * FROM players WHERE uuid = ?", (uuid,)).fetchone()
            if row is None:
                c.execute("INSERT INTO players(uuid, username, avatar, first_seen, last_seen, aliases) VALUES(?,?,?,?,?,'[]')",
                          (uuid, username, avatar, at, at))
                return None
            previous = None
            aliases = json.loads(row["aliases"] or "[]")
            if row["username"] != username:
                previous = row["username"]
                if previous not in aliases:
                    aliases.append(previous)
            first_seen = min(row["first_seen"] or at, at)
            last_seen = max(row["last_seen"] or at, at)
            c.execute("UPDATE players SET username=?, avatar=?, first_seen=?, last_seen=?, aliases=? WHERE uuid=?",
                      (username, avatar or row["avatar"], first_seen, last_seen, json.dumps(aliases), uuid))
            return previous

    # ---- purchases -------------------------------------------------------------
    def insert_purchase(self, p: dict[str, Any]) -> int:
        with self.tx() as c:
            cur = c.execute("""INSERT INTO purchases(uuid, username, product_raw, package_id, product_name, category, price,
                               price_min, price_max, ambiguous, candidates, resolved, widget_time, purchased_at, detected_at,
                               source, fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (p["uuid"], p["username"], p["product_raw"], p.get("package_id"), p.get("product_name"),
                             p.get("category"), p.get("price"), p.get("price_min"), p.get("price_max"),
                             1 if p.get("ambiguous") else 0, json.dumps(p.get("candidates") or []),
                             1 if p.get("resolved") else 0, p.get("widget_time", ""), p["purchased_at"], p["detected_at"],
                             p.get("source", "live"), p.get("fingerprint")))
            return int(cur.lastrowid)

    def purchase(self, pid: int) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM purchases WHERE id = ?", (pid,))
        return self._decode_purchase(row) if row else None

    def purchases(self, since_id: int = 0, limit: int = 5000, offset: int = 0, uuid: str | None = None,
                  newest_first: bool = False) -> list[dict[str, Any]]:
        clauses, params = ["id > ?"], [since_id]
        if uuid:
            clauses.append("uuid = ?")
            params.append(uuid)
        order = "DESC" if newest_first else "ASC"
        rows = self.query(f"SELECT * FROM purchases WHERE {' AND '.join(clauses)} ORDER BY id {order} LIMIT ? OFFSET ?",
                          params + [limit, offset])
        return [self._decode_purchase(r) for r in rows]

    def purchase_count(self) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM purchases")
        return int(row["n"]) if row else 0

    def recent_fingerprints(self, hours: int = 36) -> set[str]:
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {r["fingerprint"] for r in self.query("SELECT fingerprint FROM purchases WHERE detected_at >= ?", (cutoff_iso,))}

    def update_purchase(self, pid: int, **fields: Any) -> None:
        if not fields:
            return
        if "candidates" in fields and not isinstance(fields["candidates"], str):
            fields["candidates"] = json.dumps(fields["candidates"])
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE purchases SET {cols} WHERE id = ?", (*fields.values(), pid))

    def delete_purchase(self, pid: int) -> bool:
        with self.tx() as c:
            cur = c.execute("DELETE FROM purchases WHERE id = ?", (pid,))
            return cur.rowcount > 0

    @staticmethod
    def _decode_purchase(row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["candidates"] = json.loads(row.get("candidates") or "[]")
        except ValueError:
            row["candidates"] = []
        row["ambiguous"] = bool(row.get("ambiguous"))
        row["resolved"] = bool(row.get("resolved"))
        return row

    # ---- ignore list -------------------------------------------------------------
    def ignored_players(self) -> list[dict[str, Any]]:
        return self.get_state("ignored_players", [])

    def add_ignored(self, uuid: str | None, username: str | None) -> dict[str, Any]:
        entries = self.ignored_players()
        key_uuid = (uuid or "").lower() or None
        key_name = (username or "").strip() or None
        for e in entries:
            same_uuid = key_uuid and e.get("uuid") == key_uuid
            same_name = key_name and not key_uuid and (e.get("username") or "").lower() == key_name.lower()
            if same_uuid or same_name:
                if key_name and not e.get("username"):
                    e["username"] = key_name
                    self.set_state("ignored_players", entries)
                return e
        entry = {"uuid": key_uuid, "username": key_name, "added_at": utcnow()}
        entries.append(entry)
        self.set_state("ignored_players", entries)
        return entry

    def remove_ignored(self, key: str) -> bool:
        entries = self.ignored_players()
        k = key.lower()
        kept = [e for e in entries if not (e.get("uuid") == k or (e.get("username") or "").lower() == k)]
        if len(kept) == len(entries):
            return False
        self.set_state("ignored_players", kept)
        return True

    def purge_player(self, uuid: str | None, username: str | None) -> int:
        """Delete every purchase (and the player row) for a uuid or, failing that, a username. Returns rows removed."""
        with self.tx() as c:
            if uuid:
                n = c.execute("DELETE FROM purchases WHERE lower(uuid) = ?", (uuid.lower(),)).rowcount
                c.execute("DELETE FROM players WHERE lower(uuid) = ?", (uuid.lower(),))
            elif username:
                n = c.execute("DELETE FROM purchases WHERE lower(username) = ?", (username.lower(),)).rowcount
                c.execute("DELETE FROM players WHERE lower(username) = ?", (username.lower(),))
            else:
                n = 0
        return int(n)

    def db_size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0
