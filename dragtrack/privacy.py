"""Pseudonymise buyer identities on the way out of the process.

The storefront widget is public and so is the revenue it implies, but a row
saying *GodIsAFurry bought DRAGON for $9.99* is still one named person's
purchase history. Aggregate revenue is fair game to republish; a named
individual's spending is not ours to hand around, and under PIPEDA it is
personal information the moment it is used commercially.

So storage keeps the real values — the tracker needs them to align snapshots
against the previous poll, and the ignore list matches on them — and everything
that LEAVES the process gets a handle instead: the API, the CSV export and the
Discord embed.

Handles are:

* **stable**, so the same buyer is the same handle across rows and days. That is
  what keeps per-player stats meaningful after scrubbing; a random handle per row
  would protect the same information and destroy the analysis with it.
* **salted**, so they cannot be reversed. Minecraft usernames are a small, public
  namespace — an unsalted hash of one is recovered by hashing a name list, which
  is no protection at all.

The salt is generated once into the data directory. Losing it only means handles
change; nothing real is lost.
"""
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from . import config

_SALT: str | None = None


def _salt() -> str:
    """Read the salt, creating it on first use. Kept beside the database."""
    global _SALT
    if _SALT is not None:
        return _SALT
    if config.ANON_SALT:
        _SALT = config.ANON_SALT
        return _SALT
    path = Path(config.DATA_DIR) / "anon.salt"
    try:
        if path.is_file():
            _SALT = path.read_text(encoding="utf-8").strip()
        if not _SALT:
            _SALT = secrets.token_hex(16)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_SALT, encoding="utf-8")
    except OSError:
        # An unwritable data dir must not take the dashboard down. A per-process
        # salt still protects the data; handles just change on restart.
        _SALT = _SALT or secrets.token_hex(16)
    return _SALT


def handle(uuid: str | None, username: str | None = None) -> str:
    """A stable, non-reversible stand-in for one buyer."""
    key = (uuid or username or "").strip().lower()
    if not key:
        return "Player-?"
    digest = hashlib.sha256((_salt() + key).encode("utf-8")).hexdigest()
    return "Player-" + digest[:6]


def scrub(row: dict | None) -> dict | None:
    """Replace identity fields in one row. Everything else is untouched."""
    if not row or not config.ANONYMIZE:
        return row
    if "username" not in row and "uuid" not in row:
        return row
    out = dict(row)
    tag = handle(out.get("uuid"), out.get("username"))
    if "username" in out:
        out["username"] = tag
    if "uuid" in out:
        # Kept as a stable opaque id so the dashboard can still group by player.
        out["uuid"] = hashlib.sha256((_salt() + str(out.get("uuid") or "")).encode()).hexdigest()[:32]
    return out


def scrub_all(rows):
    """Scrub a list of rows, preserving order."""
    if not config.ANONYMIZE:
        return rows
    return [scrub(r) for r in (rows or [])]
