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


def _rekey(key: str) -> str:
    """Rewrite the uuid half of a "<uuid>|<rest>" composite key.

    Both the purchase fingerprint and the snapshot alignment keys are built this
    way. The uuid half is replaced and the rest kept, so the key keeps its shape
    and its stability - it still identifies the same transaction - without
    carrying the identifier that names the buyer.
    """
    if not isinstance(key, str) or "|" not in key:
        return key
    head, _, rest = key.partition("|")
    return opaque(head) + "|" + rest


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
        out["uuid"] = opaque(out.get("uuid"))
    # Two fields carry the real uuid *inside* another string, so swapping the
    # username and uuid above was not enough on its own:
    if "avatar" in out:
        # "https://minotar.net/avatar/<uuid>.png" - the whole identifier, in a URL.
        # Blanked rather than rewritten: the dashboard never reads this field, it
        # builds avatar URLs from the (now opaque) uuid itself.
        out["avatar"] = ""
    if "fingerprint" in out:
        out["fingerprint"] = _rekey(out["fingerprint"])
    return out


def scrub_all(rows):
    """Scrub a list of rows, preserving order."""
    if not config.ANONYMIZE:
        return rows
    return [scrub(r) for r in (rows or [])]


def opaque(uuid: str | None) -> str:
    """A stable opaque id for a uuid. Same construction scrub() uses for rows."""
    return hashlib.sha256((_salt() + str(uuid or "")).encode("utf-8")).hexdigest()[:32]


def scrub_health(health: dict | None) -> dict | None:
    """The health blob is diagnostics, and diagnostics leak identities two ways.

    Neither travels through the purchases table, so scrub() never saw them:

    * ``top_customer`` is the store's own widget, name and all.
    * ``snapshot`` holds the alignment keys of the last poll, and those keys are
      built from raw uuids. A uuid is worse than a username here - it is the
      permanent identifier, and one public API call turns it back into a name.
    """
    if not health or not config.ANONYMIZE:
        return health
    out = dict(health)

    top = out.get("top_customer")
    if isinstance(top, dict):
        top = dict(top)
        tag = handle(top.get("uuid"), top.get("username"))
        # "text" is the widget's own sentence; it may repeat the name inside it.
        if top.get("username") and top.get("text"):
            top["text"] = str(top["text"]).replace(str(top["username"]), tag)
        top["username"] = tag
        top.pop("uuid", None)
        out["top_customer"] = top

    snap = out.get("snapshot")
    if isinstance(snap, dict):
        snap = dict(snap)
        for field in ("strict", "loose"):
            keys = snap.get(field)
            if isinstance(keys, list):
                snap[field] = [_rekey(k) for k in keys]
        out["snapshot"] = snap

    # ignored_players is deliberately left alone. It is a control surface, not
    # analytics: the operator typed those names in themselves (their own alt
    # accounts, staff), and the key shown in the list is the key the unignore
    # endpoint matches on. Pseudonymising it would break the feature to protect
    # data the operator entered about themselves.

    return out


def scrub_events(events):
    """Drop the event kinds whose prose names a buyer.

    ``top_customer`` ("Top customer changed: X -> Y"), ``username_change``
    ("X is now known as Y", which links one person's two names) and the
    ``ignored``/``unignored`` pair, whose message and payload both carry whatever
    was typed into the ignore list.

    They are dropped rather than rewritten. The text is free-form and written in a
    dozen places, so a find-and-replace here would be a standing guess about every
    message added later; an event nobody can see is a smaller loss than a name
    nobody meant to publish.

    Note this is not inconsistent with leaving the live ignore list readable. That
    list is a control surface - the key it shows is the key the unignore endpoint
    matches on, so it has to round-trip. These events are only history; nothing
    matches on them.
    """
    if not events or not config.ANONYMIZE:
        return events
    names_a_person = {"top_customer", "username_change", "ignored", "unignored"}
    return [e for e in events if e.get("type") not in names_a_person]


def scrub_logs(records, identities=None):
    """Replace known identifiers inside free-form log lines.

    The dashboard serves the in-memory log buffer, and the tracker logs a line per
    recorded purchase with the buyer's name in it. Unlike every other surface here
    there are no fields to swap - it is a formatted sentence - so this substitutes
    the identifiers we know about, taken from the same table the rows come from.

    That makes it best-effort by nature, and it is deliberately the only surface
    handled this way: a name that never reached the players table would slip
    through. Anything structured is scrubbed field by field instead, which is why
    the log buffer is the one place worth this caveat rather than the pattern.

    The process's real log output - file, journal - is untouched, so operational
    debugging keeps the names it needs.
    """
    if not records or not config.ANONYMIZE:
        return records
    # Longest first, so "Bobby" is not partly rewritten by a match on "Bob".
    pairs = []
    for row in identities or []:
        name, uuid = row.get("username"), row.get("uuid")
        if name:
            pairs.append((str(name), handle(uuid, name)))
        if uuid:
            pairs.append((str(uuid), opaque(uuid)))
    pairs.sort(key=lambda kv: -len(kv[0]))

    out = []
    for rec in records:
        msg = rec.get("message")
        if isinstance(msg, str) and msg:
            for needle, tag in pairs:
                if needle in msg:
                    msg = msg.replace(needle, tag)
            rec = dict(rec, message=msg)
        out.append(rec)
    return out
