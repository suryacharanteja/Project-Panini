"""The fabric server (V2-14 §5.3, §5.8): one endpoint behind every transport.

A transport (the LAN listener, Tailscale Serve, or the relay socket) hands frames from one connection to
`handle()` and sends back what it returns. The first frame is the device's hello; after the PC's welcome, every
frame is an encrypted envelope. Each request inside is replayed into PANINI's own app in-process under
`/api/remote/...`, carrying the device id and a per-process secret that only this server knows, so the remote
routes see an authenticated device (and nothing on this PC can reach them without a session).
Revocation is checked on every request, and each device is rate-limited (30 writes and 120 reads a minute).
"""

import json
import secrets
import time
from collections import defaultdict, deque

import httpx

from . import envelope
from .envelope import EnvelopeError

FABRIC_HEADER = "x-panini-fabric"
DEVICE_HEADER = "x-panini-device"
LIMITS = {"read": 120, "write": 30}
MAX_BODY = 1_000_000


class FabricServer:
    def __init__(self, app, identity, devices, *, clock=time.time):
        self.app, self.identity, self.devices, self.clock = app, identity, devices, clock
        self.secret = secrets.token_urlsafe(32)
        self.push_key = ""  # The VAPID public key, set by the runtime.
        self.sessions: dict[str, envelope.Session] = {}
        self._calls: dict[tuple[str, str], deque] = defaultdict(deque)

    def verify(self, headers) -> str:
        """For the remote routes: the device id when the request came through this server."""
        if not secrets.compare_digest(headers.get(FABRIC_HEADER, ""), self.secret):
            raise PermissionError("Remote requests arrive only through a fabric session.")
        device_id = headers.get(DEVICE_HEADER, "")
        if self.devices.active(device_id) is None:
            raise PermissionError("This device is not paired or was revoked.")
        return device_id

    def install(self) -> "FabricServer":
        """Turn on the remote routes for this app, including the live view's remote router (V2-12 grants)."""
        from fastapi import HTTPException

        from ..routes import live as live_routes

        self.app.state.fabric = self

        def verifier(request):
            try:
                self.verify(request.headers)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from None

        live_routes.remote_verifier = verifier
        return self

    def close(self, conn: str) -> None:
        self.sessions.pop(conn, None)

    def revoke(self, device_id: str) -> None:
        """Revocation closes every open session of the device at once."""
        self.devices.revoke(device_id)
        for conn, session in list(self.sessions.items()):
            if session.device_id == device_id:
                session.revoked = True
                self.sessions.pop(conn, None)

    async def handle(self, conn: str, frame: dict) -> dict:
        try:
            if frame.get("type") == "pair":
                # Pairing runs on the same socket, so it also works through the relay (the PC still needs the
                # person to confirm the SAS before the device can open a session).
                from .devices import PairingError

                p = frame.get("message") or {}
                try:
                    answer = self.devices.pair(str(p.get("code", "")), str(p.get("name", "Phone"))[:60],
                                               str(p.get("sign_pub", "")), str(p.get("agree_pub", "")))  # fmt: skip
                except (PairingError, ValueError) as exc:
                    return {"type": "error", "error": str(exc)}
                return {"type": "paired", "message": answer}
            if frame.get("type") == "hello":
                welcome, session = envelope.welcome(self.identity, self.devices, frame.get("message") or {})
                self.sessions[conn] = session
                return {"type": "welcome", "message": welcome}
            session = self.sessions.get(conn)
            if session is None or frame.get("type") != "env":
                return {"type": "error", "error": "Say hello first."}
            if self.devices.active(session.device_id) is None:
                self.close(conn)
                return {"type": "error", "error": "This device was revoked."}
            kind, msg_id, request = session.open(frame.get("envelope") or {})
            response = await self._dispatch(session.device_id, request) if kind == "req" else {"status": 204}
            return {"type": "env", "envelope": session.seal("res", response, msg_id=msg_id)}
        except EnvelopeError as exc:
            self.close(conn)
            return {"type": "error", "error": str(exc)}

    def _limited(self, device_id: str, method: str) -> bool:
        bucket = "read" if method == "GET" else "write"
        calls = self._calls[(device_id, bucket)]
        now = self.clock()
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= LIMITS[bucket]:
            return True
        calls.append(now)
        return False

    async def _dispatch(self, device_id: str, request: dict) -> dict:
        method = str(request.get("method", "GET")).upper()
        path = str(request.get("path", "/"))
        if method not in {"GET", "POST", "PATCH", "DELETE"} or not path.startswith("/") or ".." in path:
            return {"status": 400, "body": {"error": "Unsupported request."}}
        if self._limited(device_id, method):
            return {"status": 429, "body": {"error": "Too many requests from this device. Slow down."}}
        body = request.get("body")
        content = json.dumps(body).encode() if body is not None else b""
        if len(content) > MAX_BODY:
            return {"status": 413, "body": {"error": "Request too large."}}
        headers = {FABRIC_HEADER: self.secret, DEVICE_HEADER: device_id, "content-type": "application/json"}
        transport = httpx.ASGITransport(app=self.app, client=("127.0.0.1", 0))
        async with httpx.AsyncClient(transport=transport, base_url="http://panini.fabric") as client:
            response = await client.request(method, f"/api/remote{path}", content=content, headers=headers)
        kind = response.headers.get("content-type", "")
        if kind.startswith("image/"):  # Live frames travel as base64 inside the envelope.
            import base64

            body = {"content_type": kind.split(";")[0], "base64": base64.b64encode(response.content).decode()}
            return {"status": response.status_code, "body": body}
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text[:MAX_BODY]}
        return {"status": response.status_code, "body": payload}
