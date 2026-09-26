from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragtrack import config  # noqa: E402
from dragtrack.db import Database, utcnow  # noqa: E402
from dragtrack.scraper import (WidgetEntry, discover_category_links, match_candidates, parse_category_products,  # noqa: E402
                               parse_money, parse_recent_payments, parse_top_customer, widget_key)
from dragtrack.tracker import Tracker, align, common_prefix, estimated_checkout_price, parse_widget_time  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
HOME = (FIX / "home.html").read_text(encoding="utf-8")


# ---- parsing -----------------------------------------------------------------------
def test_parse_recent_payments():
    entries = parse_recent_payments(HOME)
    assert len(entries) == 5
    first = entries[0]
    assert first.username == "Kianyah"
    assert first.uuid == "779debb4ced7433aacea380d20ece827"
    assert first.product_raw == "Magic Find Booster -"
    assert first.widget_time == "Today 02:15"
    assert first.hhmm == "02:15"
    assert entries[1].product_raw == "Whale Booster - 1 Ho"
    assert entries[4].product_raw == "1,390 DragSim Gems"
    assert entries[1].uuid == entries[3].uuid == entries[4].uuid


def test_parse_top_customer():
    top = parse_top_customer(HOME)
    assert top is not None
    assert top.username == "Egesss"
    assert top.uuid == "f6fd713e91674d3e9bd1428b8d409184"


def test_discover_categories():
    links = discover_category_links(HOME, "https://dragsim.tebex.io")
    names = [n for n, _ in links]
    assert names == ["Gems", "Ranks", "Boosters"]


@pytest.mark.parametrize("fixture,cat,expected", [
    ("cat_gems.html", "Gems", {7302935: ("16,400 DragSim Gems", 99.99), 7302927: ("675 DragSim Gems", 4.99)}),
    ("cat_ranks.html", "Ranks", {7302911: ("DRAGON", 9.99), 7302905: ("VIP", 5.99)}),
    ("cat_boosters.html", "Boosters", {7337383: ("3x Whale Booster Bundle - 3 Hours", 39.99), 7328644: ("Magic Find Booster - 1 Hour", 3.99)}),
])
def test_parse_categories(fixture, cat, expected):
    html = (FIX / fixture).read_text(encoding="utf-8")
    products = parse_category_products(html, cat, f"https://dragsim.tebex.io/category/{cat.lower()}", "https://dragsim.tebex.io")
    assert len(products) == 5
    by_id = {p.package_id: p for p in products}
    for pid, (name, price) in expected.items():
        assert by_id[pid].name == name
        assert by_id[pid].price == price
        assert by_id[pid].category == cat
        assert by_id[pid].currency_symbol == "$"
    if cat == "Ranks":
        assert by_id[7302911].type == "subscription"


@pytest.mark.parametrize("text,expected", [
    ("$9.99", ("$", 9.99)), ("$1,299.99", ("$", 1299.99)), ("9,99 €", ("€", 9.99)), ("£5", ("£", 5.0)),
    ("CA$12.50", ("CA$", 12.5)), ("", ("", None)), ("Free", ("", None)),
])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


# ---- product matching -------------------------------------------------------------------
CATALOG = [
    (7337383, "3x Whale Booster Bundle  - 3 Hours"), (7328647, "Magic Find Booster - 3 Hours"),
    (7328644, "Magic Find Booster - 1 Hour"), (7328630, "Whale Booster - 3 Hours"), (7328629, "Whale Booster - 1 Hour"),
    (7302929, "1,390 DragSim Gems"), (7302911, "DRAGON"),
]


def test_widget_key_truncates_like_tebex():
    assert widget_key("Magic Find Booster - 3 Hours") == "Magic Find Booster -"
    assert widget_key("Whale Booster - 1 Hour") == "Whale Booster - 1 Ho"
    assert widget_key("3x Whale Booster Bundle  - 3 Hours") == "3x Whale Booster Bun"
    assert widget_key("DRAGON") == "DRAGON"


def test_match_candidates():
    assert match_candidates("Whale Booster - 1 Ho", CATALOG) == [7328629]
    assert match_candidates("1,390 DragSim Gems", CATALOG) == [7302929]
    assert match_candidates("DRAGON", CATALOG) == [7302911]
    assert sorted(match_candidates("Magic Find Booster -", CATALOG)) == [7328644, 7328647]
    assert match_candidates("Magic Find Booster - ...", CATALOG) != []
    assert match_candidates("Nonexistent Thing", CATALOG) == []


def test_common_prefix():
    assert common_prefix(["Magic Find Booster - 3 Hours", "Magic Find Booster - 1 Hour"]) == "Magic Find Booster"


# ---- time parsing ------------------------------------------------------------------------
NOW = datetime(2026, 9, 14, 3, 19, tzinfo=timezone.utc)


def test_parse_widget_time_today_and_future_rollover():
    assert parse_widget_time("Today 02:15", NOW) == datetime(2026, 9, 14, 2, 15, tzinfo=timezone.utc)
    # "Today 23:28" while it is 03:19 -> must be the previous day
    assert parse_widget_time("Today 23:28", NOW) == datetime(2026, 9, 13, 23, 28, tzinfo=timezone.utc)
    assert parse_widget_time("Yesterday 21:58", NOW) == datetime(2026, 9, 13, 21, 58, tzinfo=timezone.utc)
    assert parse_widget_time("12 Sep 2026 10:00", NOW) == datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
    assert parse_widget_time("2 hours ago", NOW) == datetime(2026, 9, 14, 1, 19, tzinfo=timezone.utc)
    assert parse_widget_time("garbage", NOW) is None


def test_parse_widget_time_with_offset():
    # widget clock at UTC-4: "Today 22:15" local == 02:15 UTC next day
    assert parse_widget_time("Today 22:15", NOW, tz_offset_min=-240) == datetime(2026, 9, 14, 2, 15, tzinfo=timezone.utc)


# ---- alignment ------------------------------------------------------------------------------
def test_align_cases():
    old = ["A", "B", "C", "D", "E"]
    assert align(["A", "B", "C", "D", "E"], old, 2) == (0, 0)          # unchanged
    assert align(["N", "A", "B", "C", "D"], old, 2) == (1, 0)          # one new
    assert align(["N2", "N1", "A", "B", "C"], old, 2) == (2, 0)        # two new
    assert align(["B", "C", "D", "E", "F"], old, 2) == (0, 1)          # CDN served an older page: nothing new
    assert align(["N", "B", "C", "D", "E"], old, 2) == (1, 1)          # A vanished (refund) + one new
    assert align(["A2", "A", "B", "C", "D"], old, 2) == (1, 0)         # duplicate key at the top still counts as new
    assert align(["X", "Y", "Z", "W", "V"], old, 2) is None            # complete turnover
    assert align(["N", "A"], ["A"], 1) == (1, 0)                       # tiny widgets


# ---- end-to-end diff with a real DB -----------------------------------------------------------
def make_entries(specs):
    return [WidgetEntry(uuid=u, username=n, product_raw=p, widget_time=t) for (u, n, p, t) in specs]


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEED_EXISTING", True)
    db = Database(tmp_path / "t.db")
    db.upsert_products([
        {"package_id": pid, "name": name, "category": "Boosters", "category_slug": "boosters", "type": "single",
         "price": price, "original_price": None, "currency_symbol": "$", "image": "", "url": ""}
        for pid, name, price in [(1, "Magic Find Booster - 3 Hours", 9.99), (2, "Magic Find Booster - 1 Hour", 3.99),
                                 (3, "Whale Booster - 1 Hour", 6.99), (4, "DRAGON", 9.99)]
    ])
    return Tracker(db, client=None, notifier=None)


def test_diff_seed_then_live(tracker):
    first = make_entries([("u1", "Kian", "Magic Find Booster -", "Today 02:15"), ("u2", "Im5", "Whale Booster - 1 Ho", "Today 23:28"),
                          ("u3", "God", "DRAGON", "Today 22:37")])
    new, method, _ = tracker.diff(first)
    assert method == "seed" and len(new) == 3
    now = "2026-09-14T03:20:00Z"
    products = {p["package_id"]: p for p in tracker.db.products()}
    for e in reversed(new):
        tracker.record(e, "seed", now, products)
    tracker.db.set_state("snapshot", {"strict": [e.strict_key for e in first], "loose": [e.loose_key for e in first]})

    rows = tracker.db.purchases()
    assert [r["username"] for r in rows] == ["God", "Im5", "Kian"]
    kian = rows[2]
    assert kian["ambiguous"] and sorted(kian["candidates"]) == [1, 2]
    assert kian["price_min"] == 3.99 and kian["price_max"] == 9.99 and kian["price"] == 6.99
    assert rows[1]["package_id"] == 3 and rows[1]["price"] == 6.99 and not rows[1]["ambiguous"]

    second = make_entries([("u4", "New", "DRAGON", "Today 03:30")]) + first[:2]
    new, method, vanished = tracker.diff(second)
    assert method == "strict" and [e.username for e in new] == ["New"] and vanished == 0
    tracker.db.set_state("snapshot", {"strict": [e.strict_key for e in second], "loose": [e.loose_key for e in second]})

    # same page served again (CDN) -> nothing new
    new, method, _ = tracker.diff(second)
    assert new == [] and method == "strict"


def test_diff_no_overlap_uses_db_guard(tracker):
    entries = make_entries([("u1", "A", "DRAGON", "Today 01:00"), ("u2", "B", "DRAGON", "Today 01:01")])
    products = {p["package_id"]: p for p in tracker.db.products()}
    for e in entries:
        tracker.record(e, "live", utcnow(), products)
    tracker.db.set_state("snapshot", {"strict": ["zzz|x|00:00", "yyy|x|00:00"], "loose": ["zzz|x", "yyy|x"]})
    fresh = make_entries([("u9", "Z", "DRAGON", "Today 03:00")])
    new, method, _ = tracker.diff(fresh + entries)
    assert method == "no-overlap"
    assert [e.username for e in new] == ["Z"]


def test_player_rename_tracked(tracker):
    products = {p["package_id"]: p for p in tracker.db.products()}
    tracker.record(WidgetEntry("u1", "OldName", "DRAGON", "Today 01:00"), "live", "2026-09-14T01:00:00Z", products)
    tracker.record(WidgetEntry("u1", "NewName", "DRAGON", "Today 02:00"), "live", "2026-09-14T02:00:00Z", products)
    players = tracker.db.players()
    assert len(players) == 1 and players[0]["username"] == "NewName" and players[0]["aliases"] == ["OldName"]
    assert any(e["type"] == "username_change" for e in tracker.db.events())


def test_checkout_discount_estimate(tracker, monkeypatch):
    monkeypatch.setattr(config, "CHECKOUT_DISCOUNT_PERCENT", 25.0)
    monkeypatch.setattr(config, "CHECKOUT_DISCOUNT_MIN_BASKET", 19.99)
    assert estimated_checkout_price(19.99) == 14.99
    assert estimated_checkout_price(19.98) == 19.98
    assert estimated_checkout_price(9.99) == 9.99

    products = {p["package_id"]: p for p in tracker.db.products()}
    products[5] = {"package_id": 5, "name": "DRAGON+", "category": "Ranks", "price": 19.99, "active": 1}
    resolved = tracker.resolve("DRAGON+", products)
    assert resolved["price"] == 14.99
    assert resolved["price_min"] == 14.99
    assert resolved["price_max"] == 14.99


# ---- widget clock calibration ------------------------------------------------------------
def test_effective_offset_auto_calibrates(tracker, monkeypatch):
    monkeypatch.setattr(config, "WIDGET_TZ_OFFSET_MIN", None)
    assert tracker.effective_offset() == 0  # nothing observed yet -> assume UTC
    tracker.db.set_state("widget_clock_offsets", [238, 241, 245])  # widget clock 4h behind UTC + small lag
    assert tracker.effective_offset() == -240
    tracker.db.set_state("widget_clock_offsets", [1, 12, 3])
    assert tracker.effective_offset() == 0
    monkeypatch.setattr(config, "WIDGET_TZ_OFFSET_MIN", 60)
    assert tracker.effective_offset() == 60


def test_live_record_uses_widget_time_when_plausible(tracker, monkeypatch):
    monkeypatch.setattr(config, "WIDGET_TZ_OFFSET_MIN", 0)
    products = {p["package_id"]: p for p in tracker.db.products()}
    now = datetime.now(timezone.utc)
    hhmm = (now - timedelta(minutes=7)).strftime("%H:%M")
    p = tracker.record(WidgetEntry("u1", "A", "DRAGON", f"Today {hhmm}"), "live", utcnow(), products)
    assert p["purchased_at"].endswith(f"T{hhmm}:00Z")
    # implausible (garbage) widget time -> falls back to detection time
    p2 = tracker.record(WidgetEntry("u1", "A", "DRAGON", "whenever"), "live", "2026-01-01T00:00:00Z", products)
    assert p2["purchased_at"] == "2026-01-01T00:00:00Z"


# ---- .env loader --------------------------------------------------------------------------
def test_dotenv_loader_strips_inline_comments(tmp_path, monkeypatch):
    from dragtrack.config import _load_dotenv
    env = tmp_path / ".env"
    env.write_text('A_KEY=chrome   # trailing note\nB_KEY="quoted # not a comment"\nC_KEY=plain\n# whole line\nD_KEY=\n', encoding="utf-8")
    for k in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
        monkeypatch.delenv(k, raising=False)
    _load_dotenv(env)
    import os
    assert os.environ["A_KEY"] == "chrome"
    assert os.environ["B_KEY"] == "quoted # not a comment"
    assert os.environ["C_KEY"] == "plain" and os.environ["D_KEY"] == ""
