"""Optional original LiveKit/Gradium audio frontend, using the same browser service."""

import asyncio
import json
import os
from uuid import uuid4

import httpx


class ConsoleBridge:
    def __init__(self, speak):
        self.speak = speak
        self.client = httpx.AsyncClient(base_url=os.getenv("PANINI_APP_URL", "http://127.0.0.1:3100"), timeout=45)
        self.token = None
        self.reader = None

    async def start(self):
        mode = os.getenv("PANINI_BROWSER_MODE", "extension")
        target = {"mode": mode}
        if mode == "extension":
            response = await self.client.get("/api/browser/extension/status")
            response.raise_for_status()
            target["target_id"] = response.json().get("target_id", "")
        response = await self.client.post("/api/voice/console", json=target)
        response.raise_for_status()
        self.token = response.json()["session_id"]
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        async with self.client.stream(
            "POST", "/api/voice/events", json={"session_id": self.token}, timeout=None
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("type") == "message":
                    self.speak(event["message"])
                if event.get("type") == "closed":
                    break

    async def handle(self, text):
        response = await self.client.post(
            "/api/voice/turn", json={"session_id": self.token, "text": text, "turn_id": uuid4().hex}
        )
        response.raise_for_status()

    async def aclose(self):
        try:
            if self.token:
                await self.client.post("/api/voice/end", json={"session_id": self.token})
        finally:
            if self.reader:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
            await self.client.aclose()
