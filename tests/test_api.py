from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragtrack import config, privacy  # noqa: E402
from dragtrack.api import build_app  # noqa: E402
from dragtrack.db import Database, utcnow  # noqa: E402
from dragtrack.notify import DiscordNotifier  # noqa: E402
from dragtrack.scraper import WidgetEntry  # noqa: E402
from dragtrack.tracker import LOG_BUFFER, Tracker  # noqa: E402


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    # Off here on purpose: these cases assert who bought what, which is the
    # behaviour under test. Scrubbing has its own tests below.
    monkeypatch.setattr(config, "ANONYMIZE", False)
    db = Database(tmp_path / "api.db")
    db.upsert_products([
        {"package_id": pid, "name": name, "category": "Boosters", "category_slug": "boosters", "type": "single",
         "price": price, "original_price": None, "currency_symbol": "$", "image": "", "url": ""}
        for pid, name, price in [(1, "Magic Find Booster - 3 Hours", 9.99), (2, "Magic Find Booster - 1 Hour", 3.99)]
    ])
    tracker = Tracker(db, client=None, notifier=None)
    products = {p["package_id"]: p for p in db.products()}
    tracker.record(WidgetEntry("u1", "Kian", "Magic Find Booster -", "Today 02:15"), "live", utcnow(), products)
    app = build_app(db=db, tracker=tracker, run_tracker=False)
    return TestClient(app), db


def test_bootstrap_and_purchases(app_env):
    client, _ = app_env
    boot = client.get("/api/bootstrap").json()
    assert boot["config"]["store_url"] and len(boot["products"]) == 2 and boot["purchase_count"] == 1
    assert boot["health"]["status"] == "starting"
    r = client.get("/api/purchases").json()["purchases"]
    assert len(r) == 1 and r[0]["ambiguous"] is True and r[0]["candidates"] == [1, 2] or sorted(r[0]["candidates"]) == [1, 2]
    assert client.get("/api/purchases?since_id=1").json()["purchases"] == []
    assert client.get("/").status_code == 200
    assert "StoreScope" in client.get("/").text


def test_resolve_reset_delete(app_env):
    client, _ = app_env
    p = client.post("/api/purchases/1/resolve", json={"package_id": 2}).json()["purchase"]
    assert p["product_name"] == "Magic Find Booster - 1 Hour" and p["price"] == 3.99 and p["resolved"] and not p["ambiguous"]
    p = client.post("/api/purchases/1/resolve", json={"price": 4.5}).json()["purchase"]
    assert p["price"] == 4.5 and p["resolved"]
    p = client.post("/api/purchases/1/resolve", json={"reset": True}).json()["purchase"]
    assert p["ambiguous"] and not p["resolved"] and p["price_min"] == 3.99 and p["price_max"] == 9.99
    assert client.post("/api/purchases/1/resolve", json={}).status_code == 400
    assert client.post("/api/purchases/99/resolve", json={"price": 1}).status_code == 404
    assert client.post("/api/purchases/1/resolve", json={"package_id": 12345}).status_code == 404
    csv_text = client.get("/api/export.csv").text
    assert csv_text.startswith("id,purchased_at") and "Kian" in csv_text
    assert client.delete("/api/purchases/1").json() == {"ok": True}
    assert client.delete("/api/purchases/1").status_code == 404
    assert client.get("/api/purchases").json()["purchases"] == []
    types = [e["type"] for e in client.get("/api/events").json()["events"]]
    assert "resolved" in types and "deleted" in types


def test_login_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "hunter2")
    monkeypatch.setattr(config, "DASHBOARD_USER", "admin")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db = Database(tmp_path / "auth.db")
    app = build_app(db=db, tracker=Tracker(db, client=None), run_tracker=False)
    client = TestClient(app, follow_redirects=False)
    # browser navigation -> login page; API -> 401 JSON (basic auth still honoured)
    r = client.get("/", headers={"accept": "text/html"})
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r = client.get("/api/health")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/api/health", auth=("admin", "hunter2")).status_code == 200
    assert client.get("/static/style.css").status_code == 200  # assets are public so the login page can style itself
    page = client.get("/login?next=/%23players").text
    assert "Sign in" in page and 'value="/#players"' in page
    assert 'value="/"' in client.get("/login?next=https://evil.example").text  # open redirect blocked
    # wrong password, then right password
    r = client.post("/login", data={"username": "admin", "password": "nope", "next": "/"})
    assert r.status_code == 401 and "Wrong username" in r.text
    r = client.post("/login", data={"username": "admin", "password": "hunter2", "next": "/#feed"})
    assert r.status_code == 303 and r.headers["location"] == "/#feed" and "dragtrack_session=" in r.headers["set-cookie"]
    assert client.get("/api/health").status_code == 200  # cookie kept by the client
    assert client.get("/", headers={"accept": "text/html"}).status_code == 200
    assert client.get("/login", headers={"accept": "text/html"}).status_code == 303  # already signed in
    r = client.get("/logout")
    assert r.status_code == 303
    client.cookies.clear()
    assert client.get("/api/health").status_code == 401
    # sessions survive a restart because the signing key is persisted
    assert (tmp_path / "session.key").is_file()
    # brute-force throttle
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "x"})
    assert client.post("/login", data={"username": "admin", "password": "hunter2"}).status_code == 429


def test_discord_notifier(monkeypatch):
    sent = {}

    class FakeResp:
        status_code = 204
        text = ""

    def fake_post(url, json=None, timeout=None):
        sent["url"], sent["json"] = url, json
        return FakeResp()

    monkeypatch.setattr("dragtrack.notify.cffi_requests.post", fake_post)
    monkeypatch.setattr(config, "ANONYMIZE", False)
    n = DiscordNotifier("https://discord.example/webhook", "USD", "DragSim")
    assert n.enabled
    n.purchase({"username": "Kian", "product_name": "Magic Find Booster", "product_raw": "Magic Find Booster -", "price": 6.99,
                "price_min": 3.99, "price_max": 9.99, "ambiguous": True, "category": "Boosters", "widget_time": "Today 02:15",
                "purchased_at": "2026-09-14T02:15:00Z"}, "https://minotar.net/helm/x/64")
    embed = sent["json"]["embeds"][0]
    assert "Kian" in embed["description"] and "$3.99 – $9.99" in embed["fields"][0]["value"]
    assert embed["thumbnail"]["url"].startswith("https://minotar.net")
    assert not DiscordNotifier("", "USD", "x").enabled


def test_ignore_list(app_env, monkeypatch):
    client, db = app_env
    monkeypatch.setattr(config, "IGNORED_PLAYERS", ["EnvGuy"])
    tracker = client.app.state.tracker
    products = {p["package_id"]: p for p in db.products()}
    tracker.record(WidgetEntry("u2", "Tester", "DRAGON", "Today 03:00"), "live", utcnow(), products)
    assert len(client.get("/api/purchases").json()["purchases"]) == 2
    # ignore + purge by username only; uuid gets filled in from the players table
    r = client.post("/api/ignored", json={"username": "tester"}).json()
    assert r["removed"] == 1 and r["ignored"]["uuid"] == "u2"
    assert [p["username"] for p in client.get("/api/purchases").json()["purchases"]] == ["Kian"]
    assert "u2" not in [p["uuid"] for p in client.get("/api/players").json()["players"]]
    # tracker skips ignored players (by uuid, by dashboard name, by env name) but still records others
    assert tracker.is_ignored(WidgetEntry("u2", "Renamed", "DRAGON", "Today 03:05"))
    assert tracker.is_ignored(WidgetEntry("u9", "TESTER", "DRAGON", "Today 03:05"))
    assert tracker.is_ignored(WidgetEntry("u8", "envguy", "DRAGON", "Today 03:05"))
    assert not tracker.is_ignored(WidgetEntry("u1", "Kian", "DRAGON", "Today 03:05"))
    listed = client.get("/api/health").json()["ignored_players"]
    assert {e.get("username") for e in listed} == {"Tester", "EnvGuy"}
    assert client.post("/api/ignored", json={}).status_code == 400
    assert client.post("/api/ignored", json={"uuid": "nope"}).status_code == 400
    assert client.delete("/api/ignored/u2").json() == {"ok": True}
    assert client.delete("/api/ignored/u2").status_code == 404
    assert not tracker.is_ignored(WidgetEntry("u2", "Tester", "DRAGON", "Today 03:05"))


# --- privacy ------------------------------------------------------------------

def test_handles_are_stable_and_salted(tmp_path, monkeypatch):
    """Same buyer, same handle - otherwise per-player stats stop working."""
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ANON_SALT", "test-salt")
    monkeypatch.setattr(privacy, "_SALT", None)

    row = {"username": "GodIsAFurry", "uuid": "25d2ac3ba0564c2b9dad1058242c50b1", "price": 9.99}
    a, b = privacy.scrub(dict(row)), privacy.scrub(dict(row))
    assert a["username"] == b["username"]
    assert a["username"].startswith("Player-")
    assert "GodIsAFurry" not in str(a)
    assert row["uuid"] not in str(a)
    assert a["price"] == 9.99          # everything that is not identity survives


def test_different_buyers_get_different_handles(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ANON_SALT", "test-salt")
    monkeypatch.setattr(privacy, "_SALT", None)
    one = privacy.scrub({"username": "A", "uuid": "a" * 32})
    two = privacy.scrub({"username": "B", "uuid": "b" * 32})
    assert one["username"] != two["username"]


def test_salt_changes_the_handle(tmp_path, monkeypatch):
    """Unsalted, a username list would recover every handle by brute force."""
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    row = {"username": "GodIsAFurry", "uuid": "25d2ac3ba0564c2b9dad1058242c50b1"}

    monkeypatch.setattr(config, "ANON_SALT", "salt-one")
    monkeypatch.setattr(privacy, "_SALT", None)
    first = privacy.scrub(dict(row))["username"]

    monkeypatch.setattr(config, "ANON_SALT", "salt-two")
    monkeypatch.setattr(privacy, "_SALT", None)
    assert privacy.scrub(dict(row))["username"] != first


def test_api_scrubs_by_default(app_env, monkeypatch):
    """The endpoint must not hand out names when scrubbing is on."""
    client, _ = app_env
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(privacy, "_SALT", None)
    names = [p["username"] for p in client.get("/api/purchases").json()["purchases"]]
    assert names and all(n.startswith("Player-") for n in names)


def test_health_scrubs_top_customer_and_snapshot(monkeypatch):
    """The health blob is the other way an identity gets out.

    top_customer comes straight off the storefront page and never touches the
    purchases table; the snapshot keys carry raw uuids, which are worse than
    names because they are permanent and publicly resolvable.
    """
    from dragtrack import config, privacy
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(config, "ANON_SALT", "test-salt")
    monkeypatch.setattr(privacy, "_SALT", "test-salt")

    raw = {
        "top_customer": {"username": "sStepbr0", "uuid": "916ac1da79fc446e8568e01df577a2c2",
                         "text": "sStepbr0 paid the most this year."},
        "snapshot": {"strict": ["916ac1da79fc446e8568e01df577a2c2|MVP+|17:05"],
                     "loose": ["916ac1da79fc446e8568e01df577a2c2|MVP+"]},
        "db_size": 1234,
    }
    out = privacy.scrub_health(raw)
    blob = json.dumps(out)
    assert "sStepbr0" not in blob
    assert "916ac1da79fc446e8568e01df577a2c2" not in blob
    assert out["top_customer"]["username"].startswith("Player-")
    assert "uuid" not in out["top_customer"]
    # the product and time halves of the key survive - they are what make it a key
    assert out["snapshot"]["strict"][0].endswith("|MVP+|17:05")
    assert out["db_size"] == 1234  # untouched


def test_health_untouched_when_anonymize_off(monkeypatch):
    from dragtrack import config, privacy
    monkeypatch.setattr(config, "ANONYMIZE", False)
    raw = {"top_customer": {"username": "sStepbr0", "uuid": "a" * 32}}
    assert privacy.scrub_health(raw) is raw


def test_events_drop_the_ones_that_name_a_buyer(monkeypatch):
    from dragtrack import config, privacy
    monkeypatch.setattr(config, "ANONYMIZE", True)
    # NB: the column is "type". An earlier version of this filter read "kind",
    # which silently matched nothing and dropped no events at all.
    events = [
        {"type": "top_customer", "message": "Top customer changed: Egesss -> sStepbr0"},
        {"type": "username_change", "message": "sStepbr0 is now known as someoneelse"},
        {"type": "price_change", "message": "MVP+ went from 9.99 to 12.99"},
        {"type": "ignored", "message": "Ignoring my-own-alt"},
    ]
    kept = privacy.scrub_events(events)
    assert [e["type"] for e in kept] == ["price_change"]
    assert "sStepbr0" not in json.dumps(kept)


def test_row_scrub_leaves_no_uuid_in_avatar_or_fingerprint(monkeypatch):
    """A row can smuggle the uuid past the username swap inside another string.

    The avatar is a minotar URL with the uuid in the path, and the fingerprint is
    "<uuid>|<product>|<time>". Both survived the first version of scrub(), so a
    "pseudonymised" row still shipped the permanent, publicly resolvable id.
    """
    from dragtrack import config, privacy
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(config, "ANON_SALT", "test-salt")
    monkeypatch.setattr(privacy, "_SALT", "test-salt")

    uuid = "916ac1da79fc446e8568e01df577a2c2"
    row = {
        "username": "sStepbr0", "uuid": uuid,
        "avatar": f"https://minotar.net/avatar/{uuid}.png",
        "fingerprint": f"{uuid}|16,400 DragSim Gems|17:05",
        "price": 9.99,
    }
    out = privacy.scrub(row)
    assert uuid not in json.dumps(out)
    assert "sStepbr0" not in json.dumps(out)
    # the fingerprint stays a usable fingerprint: same shape, same stability
    assert out["fingerprint"].endswith("|16,400 DragSim Gems|17:05")
    assert privacy.scrub(dict(row))["fingerprint"] == out["fingerprint"]
    assert out["price"] == 9.99


def test_logs_scrub_names_inside_prose(monkeypatch):
    """The log buffer is served over HTTP and the tracker logs a name per purchase.

    There are no fields to swap here - it is a formatted sentence - so known
    identifiers are substituted instead.
    """
    from dragtrack import config, privacy
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(config, "ANON_SALT", "test-salt")
    monkeypatch.setattr(privacy, "_SALT", "test-salt")

    uuid = "916ac1da79fc446e8568e01df577a2c2"
    identities = [{"username": "sStepbr0", "uuid": uuid}, {"username": "Egesss", "uuid": "b" * 32}]
    records = [
        {"level": "INFO", "message": f"LIVE purchase #41: sStepbr0 bought MVP+ (9.99) [{uuid}]"},
        {"level": "INFO", "message": "Ignore list: added Egesss"},
        {"level": "INFO", "message": "Catalog: 17 products"},
    ]
    out = privacy.scrub_logs(records, identities)
    blob = json.dumps(out)
    assert "sStepbr0" not in blob and "Egesss" not in blob and uuid not in blob
    assert "bought MVP+ (9.99)" in out[0]["message"]      # the useful part survives
    assert out[2]["message"] == "Catalog: 17 products"    # untouched
    assert records[0]["message"].startswith("LIVE purchase #41: sStepbr0")  # input not mutated


def test_no_endpoint_leaks_an_identity(tmp_path, monkeypatch):
    """The end-to-end guard: walk every read endpoint, grep for the real values.

    This is the check worth having. Four separate leaks got past field-by-field
    review - the top-customer widget, the snapshot alignment keys, the avatar URL
    and the purchase fingerprint - because each one carried an identifier somewhere
    other than a "username" or "uuid" field. Asserting on the whole response body
    does not care where the identifier is hiding.
    """
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    monkeypatch.setattr(config, "ANONYMIZE", True)
    monkeypatch.setattr(config, "ANON_SALT", "test-salt")
    monkeypatch.setattr(privacy, "_SALT", "test-salt")

    uuid = "916ac1da79fc446e8568e01df577a2c2"
    name = "sStepbr0"
    db = Database(tmp_path / "leak.db")
    db.upsert_products([{"package_id": 1, "name": "Whale Booster - 3 Hours", "category": "Boosters",
                         "category_slug": "boosters", "type": "single", "price": 9.99, "original_price": None,
                         "currency_symbol": "$", "image": "", "url": ""}])
    tracker = Tracker(db, client=None, notifier=None)
    products = {p["package_id"]: p for p in db.products()}
    # the avatar matters: it is a minotar URL with the uuid in the path, which is
    # how the identifier got out despite the username and uuid fields being swapped
    tracker.record(WidgetEntry(uuid, name, "Whale Booster - 3 Ho", "Today 21:51",
                               f"https://minotar.net/avatar/{uuid}.png"), "live", utcnow(), products)
    # the three places that are not purchase rows
    db.set_state("top_customer", {"username": name, "uuid": uuid, "text": f"{name} paid the most."})
    db.set_state("snapshot", {"strict": [f"{uuid}|Whale Booster - 3 Ho|21:51"],
                              "loose": [f"{uuid}|Whale Booster - 3 Ho"], "at": utcnow()})
    db.add_event("top_customer", f"Top customer changed: Egesss -> {name}", {"username": name, "uuid": uuid})
    db.add_event("username_change", f"oldname is now known as {name}", {"uuid": uuid})
    LOG_BUFFER.records.append({"at": utcnow(), "level": "INFO", "logger": "t",
                               "message": f"LIVE purchase #1: {name} bought Whale Booster [{uuid}]"})

    client = TestClient(build_app(db=db, tracker=tracker, run_tracker=False))
    for path in ["/api/bootstrap", "/api/health", "/api/events", "/api/players",
                 "/api/purchases", "/api/products", "/api/export.csv", "/api/logs"]:
        body = client.get(path).text
        assert name.lower() not in body.lower(), f"{path} leaked the username"
        assert uuid.lower() not in body.lower(), f"{path} leaked the uuid"
    # ...and the analysis still works: the purchase is there under a handle
    rows = client.get("/api/purchases").json()["purchases"]
    assert len(rows) == 1 and rows[0]["username"].startswith("Player-")
