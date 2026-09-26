"""Fill a database with synthetic data, so the dashboard can be run by anyone.

    python -m dragtrack.demo && python -m dragtrack --db demo/demo.db

Two reasons this exists.

The first is that the dashboard is the part worth looking at, and looking at it
otherwise means pointing the scraper at somebody's shop and waiting a month for
the charts to fill in. A reviewer should be able to clone this and see it work.

The second is that the alternative was screenshots of a real store's revenue.
Those numbers belong to whoever runs the shop, not to me, and a portfolio is a
bad reason to publish them - so every figure in the screenshots comes from here.

Nothing in this file is real: the store, the products, the buyers and the prices
are all invented, and the uuids are random. The *shape* is modelled on what a
real storefront looks like, because a demo that looks nothing like production is
not much of a demo:

  - revenue is lumpy, not smooth. Weekends run higher, and a sale weekend spikes.
  - buyers are heavily skewed. A handful account for most of the revenue, and
    most buy exactly once, which is what makes the "top spender" panel mean
    anything.
  - a few rows are deliberately ambiguous, because the truncation collision they
    come from is a real property of the source feed and the dashboard has a
    workflow for resolving them.
"""
from __future__ import annotations

import argparse
import random
import uuid as uuidlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import Database

STORE_NAME = "Frostpeak"

# Invented SKUs, priced the way game stores actually price: currency packs with a
# volume discount, subscription-ish ranks, short consumable boosters.
PRODUCTS: list[tuple[str, str, float]] = [
    ("900 Frost Shards", "Currency", 4.99),
    ("1,900 Frost Shards", "Currency", 9.99),
    ("4,100 Frost Shards", "Currency", 19.99),
    ("8,800 Frost Shards", "Currency", 39.99),
    ("22,500 Frost Shards", "Currency", 99.99),
    ("Explorer Rank", "Ranks", 9.99),
    ("Pioneer Rank", "Ranks", 19.99),
    ("Ascendant Rank", "Ranks", 49.99),
    # These two differ only after the 20th character, which is exactly the
    # collision the resolver exists for: the feed shows "Rank Upgrade - Explo"
    # for both and the price could be either.
    ("Rank Upgrade - Explorer to Pioneer", "Ranks", 12.99),
    ("Rank Upgrade - Explorer to Ascendant", "Ranks", 42.99),
    ("Loot Booster - 1 Hour", "Boosters", 2.99),
    ("Loot Booster - 3 Hours", "Boosters", 6.99),
    ("Loot Booster - 24 Hours", "Boosters", 24.99),
    ("Guild Banner", "Cosmetics", 3.99),
    ("Winter Cloak", "Cosmetics", 7.99),
    ("Aurora Wings", "Cosmetics", 14.99),
    ("Starter Bundle", "Bundles", 14.99),
    ("Founder's Bundle", "Bundles", 59.99),
]

# Invented handles. Deliberately the kind of thing a game store's feed is full of.
NAMES = [
    "frostbyte", "Kaelen_", "mossgrove", "PixelLark", "vantablack", "Duskwren",
    "orbital_", "Nimbus404", "hollowpine", "Sablewood", "quietstorm", "Emberfall",
    "tinroof", "Zephyrine", "glasscannon", "Marrowlight", "driftwood_", "Solene",
    "nightjar", "Cindervale", "paperfox", "Thornbury", "lowtide", "Vesperine",
    "ironbark", "Halcyon_", "saltflat", "Wrenfield", "copperkey", "Lumen__",
]


def _price_bucket(rng: random.Random) -> int:
    """Pick a product index with a realistic skew.

    Cheap items sell constantly and expensive ones rarely; a flat choice over the
    catalog makes average order value meaningless and flattens every chart.
    """
    roll = rng.random()
    if roll < 0.42:      # currency, the small packs
        return rng.choice([0, 1, 1, 2])
    if roll < 0.62:      # boosters
        return rng.choice([9, 10, 10, 11])
    if roll < 0.80:      # ranks, including the two that collide when truncated
        return rng.choice([5, 6, 6, 7, 8, 9])
    if roll < 0.93:      # cosmetics
        return rng.choice([13, 14, 15])
    return rng.choice([3, 4, 16, 17])   # the big-ticket tail


def seed(db_path: Path, days: int = 96, seed_value: int = 7) -> dict:
    """Build the database. Deterministic, so the screenshots are reproducible."""
    rng = random.Random(seed_value)
    db = Database(db_path)

    db.upsert_products([
        {"package_id": i + 1, "name": name, "category": cat,
         "category_slug": cat.lower(), "type": "single", "price": price,
         "original_price": None, "currency_symbol": "$", "image": "", "url": ""}
        for i, (name, cat, price) in enumerate(PRODUCTS)
    ])

    # Buyers are not a fixed cast. Most people who ever buy from a store buy
    # exactly once, and a handful come back over and over - so a new buyer is
    # invented as purchases arrive, and only sometimes does an existing one
    # return. A flat pool where everyone buys the same amount would make the
    # "repeat buyers" and "top spender" panels read 100% and meaningless.
    buyers: list[dict] = []
    name_pool = NAMES * 6   # reused with a numeric suffix once exhausted

    def pick_buyer() -> dict:
        # Return an existing buyer with a probability that grows as the pool does,
        # weighted so the regulars dominate; otherwise invent a new one.
        if buyers and rng.random() < 0.62:
            return rng.choices(buyers, weights=[b["weight"] for b in buyers])[0]
        i = len(buyers)
        base = name_pool[i % len(name_pool)]
        b = {"uuid": uuidlib.UUID(int=rng.getrandbits(128)).hex,
             "username": base if i < len(NAMES) else f"{base}{i // len(NAMES) + 1}",
             # Pareto: a couple of buyers end up worth far more than the rest.
             "weight": rng.paretovariate(1.6)}
        buyers.append(b)
        return b

    now = datetime.now(timezone.utc)
    # days-1, so the last day of the loop IS today. Otherwise the dashboard opens
    # on "Today $0.00 / down 100%", which is a seeding artefact, not a feature.
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    sale_start, sale_end = days - 23, days - 20   # one weekend-long sale

    rows = 0
    for day in range(days):
        when = start + timedelta(days=day)
        # Baseline that drifts upward, so the 30d chart has a direction to it.
        base = 1.5 + 3.4 * (day / days)
        if when.weekday() >= 5:
            base *= 1.55                      # weekends
        if sale_start <= day <= sale_end:
            base *= 3.1                       # the sale
        count = max(0, int(rng.gauss(base, base * 0.45)))

        for _ in range(count):
            buyer = pick_buyer()
            idx = _price_bucket(rng)
            name, category, price = PRODUCTS[idx]
            # Purchases cluster in the evening, not spread flat across the day.
            hour = int(min(23, max(0, rng.gauss(19, 4))))
            at = when.replace(hour=hour, minute=rng.randrange(60))
            if at > now:
                continue   # today is only partly over
            iso = at.strftime("%Y-%m-%dT%H:%M:%SZ")

            # The feed truncates names at 20 characters, which is what makes some
            # rows ambiguous. Reproduced here so the "needs review" flow has
            # something to act on.
            raw = name[:20]
            collides = [p for p in PRODUCTS if p[0][:20] == raw]
            ambiguous = len(collides) > 1
            lo = min(p[2] for p in collides)
            hi = max(p[2] for p in collides)

            db.touch_player(buyer["uuid"], buyer["username"],
                            f"https://minotar.net/avatar/{buyer['uuid']}.png", iso)
            db.insert_purchase({
                "uuid": buyer["uuid"], "username": buyer["username"],
                "product_raw": raw,
                "package_id": None if ambiguous else PRODUCTS.index((name, category, price)) + 1,
                "product_name": None if ambiguous else name,
                "category": category, "price": round((lo + hi) / 2, 2) if ambiguous else price,
                "price_min": lo, "price_max": hi,
                "ambiguous": 1 if ambiguous else 0, "candidates": "[]", "resolved": 0,
                "widget_time": at.strftime("%H:%M"),
                "purchased_at": iso, "detected_at": iso,
                "source": "seed" if day == 0 else "live",
                "fingerprint": f"{buyer['uuid']}|{raw}|{at.strftime('%H:%M')}",
            })
            rows += 1

    # A plausible poll history, so the Status page and the success-rate figure
    # have something to show. Mostly fine, with a short rough patch: a scraper
    # whose history is 100% green over a month is not a scraper anyone believes.
    polls, poll_at = [], now - timedelta(hours=10)
    while poll_at < now:
        ago = now - poll_at
        blip = timedelta(hours=6, minutes=20) < ago < timedelta(hours=7)
        polls.append((poll_at.strftime("%Y-%m-%dT%H:%M:%SZ"), 0 if blip else 1,
                      403 if blip else 200,
                      None if blip else max(120, int(rng.gauss(430, 90))),
                      0 if blip else 5, 0, "chrome",
                      "challenge (403), rotating profile" if blip else None))
        poll_at += timedelta(seconds=30)
    with db.tx() as conn:
        conn.executemany("INSERT INTO polls (at, ok, status, latency_ms, entries, new_count, profile, error) "
                         "VALUES (?,?,?,?,?,?,?,?)", polls)

    top = max(buyers, key=lambda b: b["weight"])
    db.set_state("top_customer", {
        "username": top["username"], "uuid": top["uuid"],
        "text": "Paid the most this year.",
        "seen_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})

    return {"purchases": rows, "products": len(PRODUCTS), "buyers": len(buyers),
            "db": str(db_path)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a database with synthetic data for the demo dashboard.")
    ap.add_argument("--db", default="demo/demo.db", type=Path)
    ap.add_argument("--days", default=96, type=int)
    ap.add_argument("--seed", default=7, type=int)
    args = ap.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    if args.db.exists():
        args.db.unlink()
    info = seed(args.db, days=args.days, seed_value=args.seed)
    print(f"{info['purchases']} purchases, {info['products']} products, "
          f"{info['buyers']} buyers -> {info['db']}")
    print(f"\n  STORE_NAME={STORE_NAME} DB_PATH={info['db']} python -m dragtrack\n")


if __name__ == "__main__":
    main()
