"""Mobile companion: a small phone web app on your home network, off until you enable it.

- A separate listener (default port 3210) serves only the phone app and its API; the
  console, the shell API and the extension stay on localhost.
- Only private-network addresses (home Wi-Fi, 10/172.16/192.168, link-local) may connect.
- A phone pairs with a one-time code (valid 5 minutes, 5 attempts per minute) shown in
  the console, and then holds a device cookie (HttpOnly, SameSite=Strict); tokens are
  stored only as hashes and can be revoked from the console.
- From the phone you can see status and the inbox, decide reviews, dismiss notices and
  send PANINI a request, which runs as a background task (least privilege).
Traffic is plain HTTP on your LAN, so enable it only on networks you trust.
"""

import asyncio
import hashlib
import ipaddress
import json
import secrets
import socket
import time
from pathlib import Path
from uuid import uuid4

from ..extension_pairing import data_directory

PUBLIC = Path(__file__).parent / "mobile_app"
COOKIE = "panini_phone"


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def lan_addresses():
    """IPv4 addresses of this PC on private networks; the default-route (Wi-Fi/Ethernet) one first.

    Virtual adapters (WSL, Hyper-V, Docker) also have private addresses that a phone
    cannot reach, so they are listed after the address of the interface with the route.
    """
    primary = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # No packet is sent; this picks the LAN interface.
            primary = probe.getsockname()[0]
    except OSError:
        pass
    others = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            others.add(info[4][0])
    except OSError:
        pass
    usable = lambda a: ipaddress.ip_address(a).is_private and not a.startswith("127.")  # noqa: E731
    first = [primary] if primary and usable(primary) else []
    return first + sorted(a for a in others if usable(a) and a != primary)


def private_client(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


class Devices:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        store.execute(
            "CREATE TABLE IF NOT EXISTS mobile_devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "token_hash TEXT NOT NULL UNIQUE, created REAL NOT NULL, last_seen REAL)"
        )
        self.code = None  # (code, expires)
        self.attempts = []

    def new_code(self):
        code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(8))
        self.code = (code, self.clock() + 300)
        return {"code": code, "expires": self.code[1]}

    def pair(self, code, name):
        now = self.clock()
        self.attempts = [t for t in self.attempts if now - t < 60] + [now]
        if len(self.attempts) > 5:
            raise PermissionError("Too many attempts. Wait a minute and try again.")
        if not self.code or now > self.code[1]:
            raise PermissionError("No pairing code is active. Create one in the PANINI console.")
        if not secrets.compare_digest(str(code).strip().upper(), self.code[0]):
            raise PermissionError("That code is not correct.")
        self.code = None  # One use only.
        token = secrets.token_urlsafe(32)
        device = {"id": uuid4().hex[:12], "name": " ".join(str(name).split())[:60] or "Phone"}
        self.store.execute(
            "INSERT INTO mobile_devices(id, name, token_hash, created) VALUES (?,?,?,?)",
            (device["id"], device["name"], _hash(token), now),
        )
        return device, token

    def authenticate(self, token):
        if not token:
            return None
        row = self.store.one("SELECT id, name FROM mobile_devices WHERE token_hash=?", (_hash(token),))
        if row:
            self.store.execute("UPDATE mobile_devices SET last_seen=? WHERE id=?", (self.clock(), row["id"]))
        return row

    def list(self):
        return self.store.all("SELECT id, name, created, last_seen FROM mobile_devices ORDER BY created DESC")

    def revoke(self, device_id):
        return self.store.execute("DELETE FROM mobile_devices WHERE id=?", (device_id,)).rowcount


def build_app(harness, devices):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel, ConfigDict, Field

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    class Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class Pair(Strict):
        code: str = Field(min_length=6, max_length=12)
        name: str = Field(default="Phone", max_length=60)

    class Ask(Strict):
        text: str = Field(min_length=1, max_length=2000)

    class Decide(Strict):
        decision: str = Field(pattern="^(rerun|dismiss)$")
        answer: str = Field(default="", max_length=1000)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        client = request.client.host if request.client else ""
        if not private_client(client):
            return JSONResponse({"detail": "Only devices on your home network can connect."}, 403)
        if request.method not in ("GET", "HEAD") and request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests are refused."}, 403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def device(request):
        found = devices.authenticate(request.cookies.get(COOKIE, ""))
        if not found:
            raise HTTPException(401, "Pair this phone first.")
        return found

    @app.get("/")
    async def index():
        return FileResponse(PUBLIC / "index.html")

    @app.get("/{name}")
    async def asset(name: str):
        if name not in ("app.js", "app.css", "manifest.webmanifest", "icon.svg"):
            raise HTTPException(404)
        types = {"manifest.webmanifest": "application/manifest+json", "icon.svg": "image/svg+xml"}
        return FileResponse(PUBLIC / name, media_type=types.get(name))

    @app.post("/api/pair")
    async def pair(payload: Pair):
        try:
            paired, token = devices.pair(payload.code, payload.name)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        response = JSONResponse({"device": paired})
        response.set_cookie(
            COOKIE, token, max_age=180 * 24 * 3600, httponly=True, samesite="strict", path="/"
        )
        return response

    @app.get("/api/me")
    async def me(request: Request):
        return {"device": device(request), "status": harness.status()}

    @app.get("/api/inbox")
    async def inbox(request: Request):
        device(request)
        return {
            "status": harness.status(),
            "reviews": harness.tasks.reviews(),
            "notices": harness.notices.list(limit=30),
            "tasks": [
                {k: t.get(k) for k in ("id", "title", "status", "kind", "every_minutes", "result")}
                for t in harness.tasks.list(limit=20)
            ],
        }

    @app.post("/api/ask")
    async def ask(payload: Ask, request: Request):
        found = device(request)
        task = harness.tasks.submit(payload.text, origin=f"mobile:{found['id']}", title=payload.text[:120])
        return {"task": {"id": task["id"], "status": task["status"]}}

    @app.post("/api/reviews/{review_id}")
    async def decide(review_id: str, payload: Decide, request: Request):
        device(request)
        try:
            task = harness.tasks.resolve(review_id, payload.decision, payload.answer)
        except KeyError as exc:
            raise HTTPException(409, str(exc).strip("'")) from None
        return {"task": {"id": task["id"], "status": task["status"]}}

    @app.post("/api/notices/{notice_id}")
    async def dismiss(notice_id: str, request: Request):
        device(request)
        try:
            return {"notice": harness.notices.mark(notice_id, "dismissed")["id"]}
        except KeyError:
            raise HTTPException(404, "Unknown notice") from None

    return app


class Mobile:
    """Starts and stops the LAN listener; the setting persists across restarts."""

    def __init__(self, harness, *, settings_path=None, port=None):
        self.harness = harness
        self.devices = Devices(harness.store)
        self.settings_path = settings_path or data_directory() / "mobile.json"
        self.port = port or int(self._settings().get("port") or 3210)
        self.server, self.job, self.error = None, None, ""

    def _settings(self):
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, enabled):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps({"enabled": enabled, "port": self.port}), encoding="utf-8")

    @property
    def running(self):
        return self.server is not None and self.server.started

    def status(self):
        return {
            "enabled": self.running,
            "port": self.port,
            "urls": [f"http://{a}:{self.port}/" for a in lan_addresses()] if self.running else [],
            "error": self.error,
            "devices": self.devices.list(),
            "code_active": bool(self.devices.code and time.time() < self.devices.code[1]),
        }

    async def start_if_enabled(self):
        if self._settings().get("enabled"):
            await self.enable()

    async def enable(self):
        import uvicorn

        if self.running:
            return self.status()
        config = uvicorn.Config(
            build_app(self.harness, self.devices),
            host="0.0.0.0",
            port=self.port,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            server_header=False,
            ws="none",  # The phone app needs no WebSockets.
        )
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None  # The main server owns signals.
        self.job = asyncio.create_task(self.server.serve())
        for _ in range(50):
            if self.server.started or self.job.done():
                break
            await asyncio.sleep(0.1)
        if not self.server.started:
            self.error = f"Could not listen on port {self.port}; is another program using it?"
            await self.disable(save=False)
            return self.status()
        self.error = ""
        self._save(True)
        return self.status()

    async def disable(self, save=True):
        if self.server:
            self.server.should_exit = True
            if self.job:
                try:
                    await asyncio.wait_for(self.job, 10)
                except (TimeoutError, asyncio.CancelledError, SystemExit):
                    self.job.cancel()
        self.server, self.job = None, None
        if save:
            self._save(False)
        return self.status()
