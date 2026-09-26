"""Dashboard authentication: a login page that sets a signed session cookie, with HTTP Basic auth
still accepted for curl / API clients. Enabled only when DASHBOARD_PASSWORD is set."""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from fastapi import Request

COOKIE = "dragtrack_session"
SESSION_DAYS = 30
MAX_FAILURES = 5          # per client IP ...
LOCKOUT_SECONDS = 60      # ... then wait this long


class Auth:
    def __init__(self, user: str, password: str, data_dir: Path):
        self.user = user
        self.password = password
        self.secret = self._load_secret(data_dir)
        self._failures: dict[str, list[float]] = defaultdict(list)

    @staticmethod
    def _load_secret(data_dir: Path) -> bytes:
        """Persistent random key so sessions survive restarts (regenerate by deleting data/session.key)."""
        path = data_dir / "session.key"
        try:
            if path.is_file():
                key = path.read_bytes().strip()
                if len(key) >= 32:
                    return key
            data_dir.mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            path.write_bytes(key)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return key
        except OSError:
            return secrets.token_bytes(32)  # unwritable data dir: sessions just won't survive a restart

    # ---- credentials ------------------------------------------------------------------
    def check_password(self, user: str, password: str) -> bool:
        return secrets.compare_digest(user.encode(), self.user.encode()) and \
            secrets.compare_digest(password.encode(), self.password.encode())

    def check_basic_header(self, header: str) -> bool:
        if not header.lower().startswith("basic "):
            return False
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8", "replace").partition(":")
        except Exception:
            return False
        return self.check_password(user, pw)

    # ---- session tokens ---------------------------------------------------------------
    def _sign(self, payload: str) -> str:
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()

    def issue_token(self) -> str:
        expires = str(int(time.time()) + SESSION_DAYS * 86400)
        return f"{expires}.{self._sign(expires)}"

    def check_token(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        expires, sig = token.rsplit(".", 1)
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(sig, self._sign(expires))

    def is_authenticated(self, request: Request) -> bool:
        return self.check_token(request.cookies.get(COOKIE)) or self.check_basic_header(request.headers.get("authorization", ""))

    # ---- brute-force throttle ---------------------------------------------------------
    def locked_out(self, ip: str) -> bool:
        now = time.time()
        recent = [t for t in self._failures[ip] if now - t < LOCKOUT_SECONDS]
        self._failures[ip] = recent
        return len(recent) >= MAX_FAILURES

    def record_failure(self, ip: str) -> None:
        self._failures[ip].append(time.time())

    def clear_failures(self, ip: str) -> None:
        self._failures.pop(ip, None)


def safe_next(path: str | None) -> str:
    """Only ever redirect back to a local path (no open redirects)."""
    p = (path or "/").strip()
    if not p.startswith("/") or p.startswith("//") or "\\" in p or ":" in p.split("?", 1)[0]:
        return "/"
    return p


def login_redirect(path: str) -> str:
    return f"/login?next={quote(safe_next(path), safe='/?=&')}"


def login_page(error: str = "", next_path: str = "/", user: str = "admin") -> str:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    next_path = html.escape(safe_next(next_path), quote=True)
    user = html.escape(user, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DragTrack · Login</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Pixelify+Sans:wght@500&display=swap" rel="stylesheet">
<style>
  :root {{ --accent:#9b5de5; --bg:#0b0a10; --card:#1a1721; --border:#2a2534; --border-2:#3a3347; --text:#ece8f4; --muted:#736c85; --bad:#e35d5d; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:16px; color:var(--text);
         font-family:"JetBrains Mono","Consolas",ui-monospace,monospace; font-size:13px;
         background: radial-gradient(900px 500px at 30% -10%, color-mix(in srgb, var(--accent) 14%, transparent), transparent 60%), var(--bg); }}
  form {{ width:min(380px,100%); background:var(--card); border:1px solid var(--border-2); border-radius:18px; padding:28px 28px 24px;
          box-shadow:0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent), 0 0 40px color-mix(in srgb, var(--accent) 16%, transparent), 0 30px 80px rgba(0,0,0,.55); }}
  .brand {{ display:flex; align-items:center; gap:10px; font-family:"Pixelify Sans","Segoe UI",sans-serif; font-size:24px; margin-bottom:6px; }}
  .brand i {{ width:9px; height:9px; border-radius:50%; background:var(--accent); box-shadow:0 0 12px var(--accent); }}
  p {{ color:var(--muted); margin:0 0 20px; }}
  label {{ display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:1px; margin:12px 0 6px; }}
  input {{ width:100%; padding:11px 14px; background:#0d0b12; border:1px solid var(--border-2); border-radius:10px; color:var(--text); font:inherit; outline:none; }}
  input:focus {{ border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent); }}
  button {{ width:100%; margin-top:18px; padding:12px; border-radius:999px; border:1px solid var(--accent); background:color-mix(in srgb, var(--accent) 30%, transparent);
            color:var(--text); font:inherit; font-weight:600; cursor:pointer; }}
  button:hover {{ background:color-mix(in srgb, var(--accent) 45%, transparent); }}
  .err {{ margin-top:14px; color:var(--bad); font-size:12px; }}
</style></head>
<body><form method="post" action="/login" autocomplete="on">
  <div class="brand"><i></i> DragTrack</div><p>Sign in to the dashboard</p>
  <input type="hidden" name="next" value="{next_path}">
  <label for="u">User</label><input id="u" name="username" autocomplete="username" autocapitalize="off" value="{user}">
  <label for="p">Password</label><input id="p" name="password" type="password" autocomplete="current-password" autofocus>
  <button type="submit">Sign in</button>{err}
</form></body></html>"""
