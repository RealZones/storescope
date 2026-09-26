"""Entry point: `python -m dragtrack` runs the tracker + dashboard in one process."""
from __future__ import annotations

import argparse
import logging
import sys
import threading

from . import config
from .tracker import LOG_BUFFER


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(config.LOG_LEVEL)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    root.addHandler(LOG_BUFFER)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dragtrack", description="Track Tebex store purchases + serve the dashboard.")
    parser.add_argument("--once", action="store_true", help="poll one time, print what was found, and exit")
    parser.add_argument("--catalog", action="store_true", help="refresh the product catalog and exit")
    parser.add_argument("--no-web", action="store_true", help="run the tracker only, without the dashboard")
    parser.add_argument("--no-poll", action="store_true",
                        help="serve the dashboard over the existing database without scraping (see dragtrack.demo)")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args(argv)
    setup_logging()

    if args.once or args.catalog or args.no_web:
        from .db import Database
        from .notify import DiscordNotifier
        from .scraper import StoreClient
        from .tracker import Tracker

        db = Database(config.DB_PATH)
        client = StoreClient(config.STORE_URL, impersonate=config.IMPERSONATE, timeout=config.FETCH_TIMEOUT)
        tracker = Tracker(db, client, DiscordNotifier(config.DISCORD_WEBHOOK_URL, config.CURRENCY, config.STORE_NAME))
        if args.catalog:
            return 0 if tracker.refresh_catalog() else 1
        if args.once:
            if not db.products():
                tracker.refresh_catalog()
            n = tracker.poll_once()
            print(f"Recorded {n} purchase(s). Health: {tracker.health()['status']}")
            return 0
        stop = threading.Event()
        try:
            tracker.run(stop)
        except KeyboardInterrupt:
            stop.set()
        return 0

    import uvicorn

    from .api import build_app

    app = build_app(run_tracker=not args.no_poll)
    uvicorn.run(app, host=args.host, port=args.port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
