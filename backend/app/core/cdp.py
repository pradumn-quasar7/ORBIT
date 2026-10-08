"""Minimal Chrome DevTools Protocol client (Phase 19.3) — standard library only.

Used to control a browser tab ORBIT opened for the user (YouTube on the Mac or in the
Quest browser): navigate, run the page's own player API, toggle full screen. A tiny
RFC 6455 WebSocket client over a plain socket: debugging endpoints are local
(``localhost`` on the Mac, or the Quest forwarded over USB by adb), so no TLS.
"""
import base64
import json
import os
import socket
import struct
import urllib.request
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse


class CDPError(RuntimeError):
    pass


def targets(port: int, host: str = "127.0.0.1") -> List[Dict[str, Any]]:
    with urllib.request.urlopen(f"http://{host}:{port}/json", timeout=3) as r:  # noqa: S310 (local)
        return json.loads(r.read())


def new_tab(port: int, url: str, host: str = "127.0.0.1") -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(f"http://{host}:{port}/json/new?{quote(url, safe=':/?=&%')}", method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 (local)
            return json.loads(r.read())
    except Exception:
        return None


class CDP:
    def __init__(self, ws_url: str, timeout: float = 10.0):
        u = urlparse(ws_url)
        if u.scheme != "ws" or u.hostname not in ("127.0.0.1", "localhost"):
            raise CDPError("only local debugging connections are allowed")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("debugging connection closed during handshake")
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise CDPError("debugging handshake refused: " + status.decode(errors="replace"))
        self.buffer = head.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    # ------------------------------------------------------------- framing
    def _send(self, payload: bytes, opcode: int = 1) -> None:
        mask = os.urandom(4)
        n = len(payload)
        header = bytes([0x80 | opcode])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("debugging connection closed")
            self.buffer += chunk
        out, self.buffer = self.buffer[:n], self.buffer[n:]
        return out

    def _frame(self) -> Optional[str]:
        message = b""
        while True:
            b0, b1 = self._read(2)
            fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 9:  # ping
                self._send(data, opcode=10)
                continue
            if opcode == 8:
                raise CDPError("debugging connection closed by the browser")
            message += data
            if fin:
                return message.decode("utf-8", "replace")

    # ----------------------------------------------------------------- API
    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.next_id += 1
        mid = self.next_id
        self._send(json.dumps({"id": mid, "method": method, "params": params or {}}).encode())
        while True:
            msg = json.loads(self._frame())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(msg["error"].get("message", str(msg["error"])))
                return msg.get("result", {})

    def evaluate(self, expression: str, gesture: bool = False) -> Any:
        """Run JavaScript in the page. ``gesture`` counts as a user action (full screen needs one)."""
        r = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                           "returnByValue": True, "userGesture": gesture})
        if "exceptionDetails" in r:
            raise CDPError(r["exceptionDetails"].get("exception", {}).get("description", "script error")[:200])
        return r.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self._send(b"", opcode=8)
        except OSError:
            pass
        self.sock.close()
