"""The PC's side of PANINI Relay (V2-14 §5.4, §5.9): one outbound WebSocket, reconnecting.

The relay forwards device frames as {conn, data}; this client hands each to the fabric server and sends the answer
back to the same connection. Webhook deliveries arrive as {hook: {...}} and go to the ingress handler; its answer
is returned as {hook_reply: {...}}. The relay only ever sees envelopes it cannot read.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging

import websockets

logger = logging.getLogger("panini.fabric.relay")


class RelayClient:
    def __init__(
        self, url: str, channel: str, secret: str, server, *, ingress=None, backoff=(1, 2, 5, 10, 30)
    ):
        self.url, self.channel, self.secret = url.rstrip("/"), channel, secret
        self.server, self.ingress, self.backoff = server, ingress, backoff
        self.connected = asyncio.Event()
        self._task = None
        self._locks: dict[str, asyncio.Lock] = {}  # One connection's frames are handled in order (seq).

    def start(self):
        self._task = asyncio.ensure_future(self._run())
        return self._task

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _run(self):
        attempt = 0
        while True:
            try:
                async with websockets.connect(f"{self.url}/pc/{self.channel}", max_size=1024 * 1024) as ws:
                    challenge = json.loads(await ws.recv())["challenge"]
                    digest = hmac.new(self.secret.encode(), challenge.encode(), hashlib.sha256).digest()
                    await ws.send(
                        json.dumps({"hmac": base64.urlsafe_b64encode(digest).rstrip(b"=").decode()})
                    )
                    if not json.loads(await ws.recv()).get("ok"):
                        raise ConnectionError("The relay refused the channel secret.")
                    attempt = 0
                    self.connected.set()
                    async for raw in ws:
                        asyncio.ensure_future(self._frame(ws, json.loads(raw)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- Reconnect on any failure.
                logger.info("Relay connection lost: %s", exc)
            self.connected.clear()
            await asyncio.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
            attempt += 1

    async def _frame(self, ws, msg: dict):
        if "hook" in msg:
            hook = msg["hook"]
            reply = {"id": hook["id"], "status": 503, "body": ""}
            if self.ingress is not None:
                answer = await self.ingress(hook)
                reply.update(answer)
            await ws.send(json.dumps({"hook_reply": reply}))
            return
        conn = msg.get("conn")
        if msg.get("closed"):
            self.server.close(conn)
            self._locks.pop(conn, None)
            return
        async with self._locks.setdefault(conn, asyncio.Lock()):
            answer = await self.server.handle(conn, msg.get("data") or {})
        await ws.send(json.dumps({"conn": conn, "data": answer}))
