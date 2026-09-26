"""FastAPI app: JSON API + the dashboard's static files. Starts the tracker thread on startup."""
from __future__ import annotations

import csv
import io
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, privacy
from .auth import COOKIE, SESSION_DAYS, Auth, login_page, login_redirect, safe_next
from .db import Database, utcnow
from .notify import DiscordNotifier
from .scraper import StoreClient
from .tracker import LOG_BUFFER, Tracker, estimated_checkout_price

log = logging.getLogger("dragtrack.api")
STATIC_DIR = Path(__file__).parent / "static"


class ResolveBody(BaseModel):
    package_id: int | None = None
    price: float | None = None
    reset: bool = False


class IgnoreBody(BaseModel):
    uuid: str | None = None
    username: str | None = None
    purge: bool = True


def build_app(db: Database | None = None, tracker: Tracker | None = None, run_tracker: bool = True) -> FastAPI:
    db = db or Database(config.DB_PATH)
    if tracker is None:
        client = StoreClient(config.STORE_URL, impersonate=config.IMPERSONATE, timeout=config.FETCH_TIMEOUT)
        notifier = DiscordNotifier(config.DISCORD_WEBHOOK_URL, config.CURRENCY, config.STORE_NAME)
        tracker = Tracker(db, client, notifier)
    stop_event = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        thread = None
        if run_tracker:
            thread = threading.Thread(target=tracker.run, args=(stop_event,), name="tracker", daemon=True)
            thread.start()
        try:
            yield
        finally:
            stop_event.set()
            if thread:
                thread.join(timeout=5)

    app = FastAPI(title="DragTrack", version=config.VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    app.state.db = db
    app.state.tracker = tracker

    # ---- optional login (session cookie, or HTTP basic auth for curl/API) ---------------
    auth = Auth(config.DASHBOARD_USER, config.DASHBOARD_PASSWORD, config.DATA_DIR) if config.DASHBOARD_PASSWORD else None
    app.state.auth = auth
    if auth:
        @app.middleware("http")
        async def require_login(request: Request, call_next):
            path = request.url.path
            if path in ("/login", "/logout") or path.startswith("/static/") or auth.is_authenticated(request):
                return await call_next(request)
            if not path.startswith("/api/") and "text/html" in request.headers.get("accept", ""):
                target = path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(login_redirect(target), status_code=303)
            return JSONResponse({"error": "authentication required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="DragTrack", charset="UTF-8"'})

        @app.get("/login", include_in_schema=False)
        async def login_form(request: Request):
            if auth.is_authenticated(request):
                return RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
            return HTMLResponse(login_page(next_path=request.query_params.get("next", "/"), user=auth.user))

        @app.post("/login", include_in_schema=False)
        async def login_submit(request: Request):
            form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
            field = lambda k: (form.get(k) or [""])[0]
            next_path = safe_next(field("next"))
            ip = request.client.host if request.client else "?"
            if auth.locked_out(ip):
                return HTMLResponse(login_page("Too many attempts, wait a minute and try again.", next_path, auth.user), status_code=429)
            if not auth.check_password(field("username").strip(), field("password")):
                auth.record_failure(ip)
                log.warning("Failed dashboard login from %s", ip)
                return HTMLResponse(login_page("Wrong username or password.", next_path, auth.user), status_code=401)
            auth.clear_failures(ip)
            resp = RedirectResponse(next_path, status_code=303)
            https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            resp.set_cookie(COOKIE, auth.issue_token(), max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=https)
            return resp

        @app.get("/logout", include_in_schema=False)
        async def logout():
            resp = RedirectResponse("/login", status_code=303)
            resp.delete_cookie(COOKIE)
            return resp

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # always revalidate so upgrades show up immediately
        return response

    # ---- pages -------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ---- API ---------------------------------------------------------------------------
    @app.get("/api/bootstrap")
    async def bootstrap():
        return {
            "config": {
                "anonymized": config.ANONYMIZE,
                "store_url": config.STORE_URL,
                "store_name": config.STORE_NAME,
                "currency": config.CURRENCY,
                "poll_interval": config.POLL_INTERVAL,
                "ambiguous_policy": config.AMBIGUOUS_PRICE_POLICY,
                "checkout_discount_percent": config.CHECKOUT_DISCOUNT_PERCENT,
                "checkout_discount_min_basket": config.CHECKOUT_DISCOUNT_MIN_BASKET,
                "version": config.VERSION,
                "auth": auth is not None,
            },
            "products": db.products(),
            "players": privacy.scrub_all(db.players()),
            "health": privacy.scrub_health(tracker.health()),
            "events": privacy.scrub_events(db.events(60)),
            "purchase_count": db.purchase_count(),
            "server_time": utcnow(),
        }

    @app.get("/api/purchases")
    async def purchases(since_id: int = 0, limit: int = 5000, offset: int = 0, uuid: str | None = None,
                        newest_first: bool = False):
        limit = max(1, min(limit, 20000))
        return {"purchases": privacy.scrub_all(
                    db.purchases(since_id=since_id, limit=limit, offset=offset,
                                 uuid=uuid, newest_first=newest_first)),
                "server_time": utcnow()}

    @app.get("/api/players")
    async def players():
        return {"players": privacy.scrub_all(db.players())}

    @app.get("/api/products")
    async def products():
        return {"products": db.products(), "price_history": db.price_history()}

    @app.get("/api/health")
    async def health():
        return privacy.scrub_health(tracker.health())

    @app.get("/api/events")
    async def events(limit: int = 100):
        return {"events": privacy.scrub_events(db.events(max(1, min(limit, 1000))))}

    @app.get("/api/logs")
    async def logs(limit: int = 200):
        records = list(LOG_BUFFER.records)[-max(1, min(limit, 400)):]
        # Log lines are prose, so this substitutes known names rather than swapping
        # fields; see privacy.scrub_logs for why that is acceptable only here.
        return {"logs": privacy.scrub_logs(records, db.players() if config.ANONYMIZE else None)}

    @app.post("/api/purchases/{purchase_id}/resolve")
    async def resolve_purchase(purchase_id: int, body: ResolveBody):
        p = db.purchase(purchase_id)
        if p is None:
            raise HTTPException(404, "purchase not found")
        if body.reset:
            r = tracker.resolve(p["product_raw"])
            db.update_purchase(purchase_id, package_id=r["package_id"], product_name=r["product_name"], category=r["category"],
                               price=r["price"], price_min=r["price_min"], price_max=r["price_max"],
                               ambiguous=1 if r["ambiguous"] else 0, candidates=r["candidates"], resolved=0)
            return {"purchase": db.purchase(purchase_id)}
        fields: dict[str, Any] = {"resolved": 1}
        if body.package_id is not None:
            prod = db.one("SELECT * FROM products WHERE package_id = ?", (body.package_id,))
            if prod is None:
                raise HTTPException(404, "product not found")
            price = body.price if body.price is not None else estimated_checkout_price(prod["price"])
            fields.update(package_id=prod["package_id"], product_name=prod["name"], category=prod["category"],
                          price=price, price_min=price, price_max=price, ambiguous=0)
        elif body.price is not None:
            fields.update(price=body.price, price_min=body.price, price_max=body.price, ambiguous=0)
        else:
            raise HTTPException(400, "provide package_id, price, or reset")
        db.update_purchase(purchase_id, **fields)
        db.add_event("resolved", f"Purchase #{purchase_id} resolved manually", {"purchase_id": purchase_id, **{k: v for k, v in fields.items() if k != 'candidates'}})
        return {"purchase": db.purchase(purchase_id)}

    @app.delete("/api/purchases/{purchase_id}")
    async def delete_purchase(purchase_id: int):
        if not db.delete_purchase(purchase_id):
            raise HTTPException(404, "purchase not found")
        db.add_event("deleted", f"Purchase #{purchase_id} deleted manually", {"purchase_id": purchase_id})
        return {"ok": True}

    @app.get("/api/ignored")
    async def ignored():
        return {"ignored": db.ignored_players(), "env": config.IGNORED_PLAYERS}

    @app.post("/api/ignored")
    async def ignore_player(body: IgnoreBody):
        uuid = (body.uuid or "").strip().lower() or None
        username = (body.username or "").strip() or None
        if not uuid and not username:
            raise HTTPException(400, "provide uuid or username")
        if uuid and not uuid.startswith("name:") and not (len(uuid) == 32 and all(ch in "0123456789abcdef" for ch in uuid)):
            raise HTTPException(400, "uuid must be 32 hex characters")
        # fill in whichever half we can from the players table
        row = db.one("SELECT uuid, username FROM players WHERE lower(uuid) = ? OR lower(username) = ?",
                     (uuid or "", (username or "").lower()))
        if row:  # known player: use the canonical uuid + username casing
            uuid, username = row["uuid"], row["username"]
        entry = db.add_ignored(uuid, username)
        removed = db.purge_player(uuid, username) if body.purge else 0
        db.add_event("ignored", f"Ignoring {username or uuid}" + (f"; removed {removed} purchase(s)" if body.purge else ""),
                     {"uuid": uuid, "username": username, "removed": removed})
        log.info("Ignore list: added %s (%s), purged %d purchase(s)", username, uuid, removed)
        return {"ignored": entry, "removed": removed}

    @app.delete("/api/ignored/{key}")
    async def unignore_player(key: str):
        if not db.remove_ignored(key):
            raise HTTPException(404, "not on the ignore list")
        db.add_event("unignored", f"No longer ignoring {key}", {"key": key})
        return {"ok": True}

    @app.post("/api/catalog/refresh")
    async def refresh_catalog():
        tracker.request_catalog_refresh()
        return {"ok": True, "message": "Catalog refresh queued"}

    @app.get("/api/export.csv")
    async def export_csv():
        rows = privacy.scrub_all(db.purchases(limit=10_000_000))
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "purchased_at", "detected_at", "username", "uuid", "product", "product_raw", "package_id",
                         "category", "price", "price_min", "price_max", "ambiguous", "resolved", "widget_time", "source"])
        for r in rows:
            writer.writerow([r["id"], r["purchased_at"], r["detected_at"], r["username"], r["uuid"], r["product_name"],
                             r["product_raw"], r["package_id"], r["category"], r["price"], r["price_min"], r["price_max"],
                             int(r["ambiguous"]), int(r["resolved"]), r["widget_time"], r["source"]])
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="dragtrack-purchases.csv"'})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    return app
