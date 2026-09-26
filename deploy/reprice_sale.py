"""One-off: apply a checkout-level sale (X% off items >= a price) to purchases inside a time window.

The public Tebex widget can't show basket discounts, so purchases during a checkout sale are booked at
list price. This re-prices them after the fact, keeping the original in `list_price` so it is reversible.

    python deploy/reprice_sale.py --start 2026-09-17T06:46Z --end 2026-09-21T06:00Z --percent 25 --min 19.99          # dry run
    python deploy/reprice_sale.py ... --apply                                                                            # write
    python deploy/reprice_sale.py --undo --start ... --end ...                                                           # revert
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def iso(s: str) -> str:
    s = s.strip().replace(" ", "T")
    if s.endswith("Z") and len(s) == 17:  # 2026-09-17T06:46Z
        s = s[:-1] + ":00Z"
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/dragtrack.db")
    ap.add_argument("--start", required=True, help="window start, ISO UTC e.g. 2026-09-17T06:46Z")
    ap.add_argument("--end", required=True, help="window end (exclusive), ISO UTC")
    ap.add_argument("--percent", type=float, default=25.0)
    ap.add_argument("--min", type=float, default=0.0, help="only items priced at least this (basket minimum, approximated per item)")
    ap.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    ap.add_argument("--undo", action="store_true", help="restore list_price for purchases in the window")
    a = ap.parse_args()
    start, end = iso(a.start), iso(a.end)

    c = sqlite3.connect(a.db, timeout=30)
    c.row_factory = sqlite3.Row
    cols = {r["name"] for r in c.execute("PRAGMA table_info(purchases)")}
    for col in ("list_price", "discount_percent"):
        if col not in cols:
            if a.apply or a.undo:
                c.execute(f"ALTER TABLE purchases ADD COLUMN {col} REAL")
            cols.add(col)

    if a.undo:
        rows = c.execute("SELECT id, price, list_price FROM purchases WHERE purchased_at >= ? AND purchased_at < ? "
                         "AND list_price IS NOT NULL", (start, end)).fetchall()
        for r in rows:
            c.execute("UPDATE purchases SET price = list_price, price_min = list_price, price_max = list_price, "
                      "list_price = NULL, discount_percent = NULL WHERE id = ?", (r["id"],))
        c.commit()
        print(f"restored {len(rows)} purchases to list price")
        return 0

    has_cols = "discount_percent" in {r["name"] for r in c.execute("PRAGMA table_info(purchases)")}
    extra = " AND (discount_percent IS NULL OR discount_percent = 0)" if has_cols else ""
    rows = c.execute("SELECT id, purchased_at, username, product_name, price, price_min, price_max, resolved FROM purchases "
                     f"WHERE purchased_at >= ? AND purchased_at < ? AND price >= ? AND resolved = 0{extra} ORDER BY id",
                     (start, end, a.min)).fetchall()
    factor = 1 - a.percent / 100
    before = sum(r["price"] for r in rows)
    after = sum(round(r["price"] * factor, 2) for r in rows)
    for r in rows:
        print(f"  #{r['id']:<4} {r['purchased_at']}  {r['username']:<18} {r['product_name']:<34} {r['price']:>7.2f} -> {round(r['price'] * factor, 2):>7.2f}")
    print(f"{len(rows)} purchases: {before:.2f} -> {after:.2f}  (-{before - after:.2f})  [{'APPLYING' if a.apply else 'dry run'}]")
    if not a.apply:
        return 0
    for r in rows:
        new = round(r["price"] * factor, 2)
        c.execute("UPDATE purchases SET list_price = ?, price = ?, price_min = ?, price_max = ?, discount_percent = ? WHERE id = ?",
                  (r["price"], new, new if r["price_min"] == r["price"] else r["price_min"],
                   new if r["price_max"] == r["price"] else r["price_max"], a.percent, r["id"]))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    c.execute("INSERT INTO events(at, type, message, data) VALUES(?,?,?,?)",
              (now, "sale_reprice", f"Applied {a.percent:g}% checkout sale ({start} to {end}, items >= {a.min:g}) to {len(rows)} purchases: {before:.2f} -> {after:.2f}",
               json.dumps({"start": start, "end": end, "percent": a.percent, "min": a.min, "ids": [r["id"] for r in rows]})))
    c.commit()
    print("written. undo with --undo and the same --start/--end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
