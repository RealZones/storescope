"""Optional Discord webhook notifications for new purchases."""
from __future__ import annotations

from . import config, privacy

import logging
from typing import Any

from curl_cffi import requests as cffi_requests

log = logging.getLogger("dragtrack.notify")


def fmt_money(amount: float | None, currency: str) -> str:
    if amount is None:
        return "?"
    symbol = {"USD": "$", "CAD": "CA$", "EUR": "€", "GBP": "£", "AUD": "A$"}.get(currency.upper(), currency.upper() + " ")
    return f"{symbol}{amount:,.2f}"


class DiscordNotifier:
    def __init__(self, webhook_url: str, currency: str, store_name: str):
        self.webhook_url = webhook_url
        self.currency = currency
        self.store_name = store_name

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def purchase(self, p: dict[str, Any], avatar: str) -> None:
        if not self.enabled:
            return
        # The avatar goes too, not just the name: its URL is built from the
        # player's UUID, so leaving it in hands over the identity the handle
        # was meant to withhold.
        if config.ANONYMIZE:
            p = privacy.scrub(p)
            avatar = ""

        if p.get("ambiguous"):
            price_txt = f"{fmt_money(p.get('price_min'), self.currency)} – {fmt_money(p.get('price_max'), self.currency)} (ambiguous)"
        else:
            price_txt = fmt_money(p.get("price"), self.currency)
        embed = {
            "title": f"New purchase on {self.store_name}",
            "description": f"**{p['username']}** bought **{p.get('product_name') or p['product_raw']}**",
            "color": 0x9B5DE5,
            "fields": [
                {"name": "Price", "value": price_txt, "inline": True},
                {"name": "Category", "value": p.get("category") or "—", "inline": True},
                {"name": "Widget time", "value": p.get("widget_time") or "—", "inline": True},
            ],
            "thumbnail": {"url": avatar} if avatar else None,
            "timestamp": p["purchased_at"],
            "footer": {"text": "DragTrack"},
        }
        embed = {k: v for k, v in embed.items() if v is not None}
        try:
            r = cffi_requests.post(self.webhook_url, json={"embeds": [embed]}, timeout=15)
            if r.status_code >= 300:
                log.warning("Discord webhook returned %s: %s", r.status_code, r.text[:200])
        except Exception as exc:  # never let notifications break tracking
            log.warning("Discord webhook failed: %s", exc)
