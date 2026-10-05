"""Device pairing for LAN access (Phase 18).

The API has no user accounts yet, so reaching it from the network (the Quest headset)
must not mean anyone on the Wi-Fi can read the workspace or speak as the operator.
When a pairing code is configured, every request from a non-loopback address needs a
pairing cookie, obtained once by opening ``/pair`` and typing the code shown on the
laptop. Loopback requests (the laptop itself) are unaffected.

Failed attempts are rate-limited per address; codes are compared in constant time.
"""
import hashlib
import hmac
import html
import secrets
import time
from typing import Dict, List, Optional
from urllib.parse import parse_qs

COOKIE = "orbit_pair"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: typed on a headset keyboard


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _norm(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch in ALPHABET)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair with ORBIT</title><style>
body{{font:18px system-ui,sans-serif;background:#0f1216;color:#e7eaee;display:grid;place-content:center;min-height:100vh;margin:0;padding:16px}}
form{{display:grid;gap:12px;max-width:420px}}input{{font:28px ui-monospace,monospace;letter-spacing:4px;text-transform:uppercase;padding:10px;border-radius:10px;border:1px solid #2a3038;background:#171b21;color:#e7eaee}}
button{{font:20px system-ui;padding:10px;border-radius:10px;border:0;background:#7aa2ff;color:#0f1216}}p{{color:#9aa4b2}}.err{{color:#f87171}}
</style></head><body><form method="post" action="/pair"><h1>◎ Pair with ORBIT</h1>
<p>Type the pairing code shown on your computer (in the terminal, or on the laptop's Quest page).</p>
{error}<input name="code" autocomplete="off" autofocus placeholder="ABCD-EFGH" aria-label="Pairing code">
<input type="hidden" name="next" value="{next}"><button type="submit">Pair this device</button></form></body></html>"""


class Pairing:
    """Pure ASGI middleware; ``code=None`` disables it (local-only use)."""

    def __init__(self, app, code: Optional[str], max_failures: int = 8, lockout_seconds: float = 60.0):
        self.app = app
        self.code = _norm(code) if code else None
        self.cookie_value = hashlib.sha256(f"orbit-pair:{self.code}".encode()).hexdigest() if self.code else None
        self.max_failures = max_failures
        self.lockout = lockout_seconds
        self.failures: Dict[str, List[float]] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or self.code is None:
            return await self.app(scope, receive, send)
        host = (scope.get("client") or ("", 0))[0]
        if host in LOOPBACK:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/pair":
            return await self._pair(scope, receive, send, host)
        if self._paired(scope):
            return await self.app(scope, receive, send)
        if path.startswith("/ui") or path in ("/", "/dashboard"):
            return await self._redirect(send, f"/pair?next={path}")
        await self._respond(send, 401, b'{"detail":"this device is not paired: open /pair"}', b"application/json")

    def _paired(self, scope) -> bool:
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                for part in v.decode("latin-1").split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == COOKIE and hmac.compare_digest(value, self.cookie_value):
                        return True
        return False

    def _locked(self, host: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.failures.get(host, []) if now - t < self.lockout]
        self.failures[host] = recent
        return len(recent) >= self.max_failures

    async def _pair(self, scope, receive, send, host: str):
        query = parse_qs(scope.get("query_string", b"").decode())
        nxt = query.get("next", ["/ui/xr.html"])[0]
        if scope["method"] == "GET":
            return await self._page(send, nxt, "")
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        form = parse_qs(body.decode("utf-8", "replace"))
        nxt = form.get("next", [nxt])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/ui/xr.html"  # never redirect off-site
        if self._locked(host):
            return await self._page(send, nxt, "Too many wrong codes. Wait a minute and try again.", 429)
        given = _norm(form.get("code", [""])[0])
        if not hmac.compare_digest(given, self.code):
            self.failures.setdefault(host, []).append(time.monotonic())
            return await self._page(send, nxt, "That code is not right. Check the code on your computer.", 403)
        self.failures.pop(host, None)
        cookie = f"{COOKIE}={self.cookie_value}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict"
        if scope.get("scheme") == "https":
            cookie += "; Secure"
        await send({"type": "http.response.start", "status": 303,
                    "headers": [(b"location", nxt.encode()), (b"set-cookie", cookie.encode()), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

    async def _page(self, send, nxt: str, error: str, status: int = 200):
        err = f'<p class="err">{html.escape(error)}</p>' if error else ""
        await self._respond(send, status, PAGE.format(error=err, next=html.escape(nxt, quote=True)).encode(), b"text/html; charset=utf-8")

    async def _redirect(self, send, location: str):
        await send({"type": "http.response.start", "status": 303, "headers": [(b"location", location.encode()), (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _respond(send, status: int, body: bytes, ctype: bytes):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

