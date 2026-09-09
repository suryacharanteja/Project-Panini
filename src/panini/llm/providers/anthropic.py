"""Anthropic Messages API provider.

Kept separate from the OpenAI-compatible base because the wire format differs
materially: system prompts are a top-level field rather than a message, tool
schemas are flat rather than nested under ``function``, and streaming uses named
SSE events instead of choice deltas.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from panini.errors import ProviderError, ProviderUnavailable
from panini.llm.auth import AuthStrategy
from panini.llm.provider import (
    Capabilities,
    Chunk,
    GenerateOptions,
    HealthStatus,
    LLMProvider,
    Message,
    ToolCall,
)

API_VERSION = "2023-06-01"
DEFAULT_BASE = "https://api.anthropic.com/v1"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str,
        *,
        auth: AuthStrategy,
        base_url: str = DEFAULT_BASE,
        max_context: int = 200_000,
        max_tokens: int = 4096,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._auth = auth
        self._base = base_url.rstrip("/")
        self._max_context = max_context
        self._max_tokens = max_tokens
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        self._owns_http = http is None

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[Chunk]:
        options = options or GenerateOptions()

        # System messages are hoisted out of the conversation for this API.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        convo = [m.to_dict() for m in messages if m.role != "system"]

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": convo,
            "max_tokens": options.max_tokens or self._max_tokens,
            "temperature": options.temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [self._flatten_tool(t) for t in tools]

        headers = {
            "content-type": "application/json",
            "anthropic-version": API_VERSION,
            **await self._auth.headers(),
        }

        current: dict[str, Any] | None = None
        buffer = ""

        try:
            async with self._http.stream(
                "POST", f"{self._base}/messages", json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise ProviderError(f"Anthropic returned HTTP {response.status_code}: {body}")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue

                    kind = event.get("type")

                    if kind == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            current = {"id": block.get("id", ""), "name": block.get("name", "")}
                            buffer = ""

                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield Chunk(delta=delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            buffer += delta.get("partial_json", "")

                    elif kind == "content_block_stop" and current:
                        try:
                            args = json.loads(buffer) if buffer else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield Chunk(
                            tool_call=ToolCall(id=current["id"], name=current["name"], args=args)
                        )
                        current, buffer = None, ""

                    elif kind == "message_stop":
                        yield Chunk(done=True)
                        return
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(self.name, str(exc)) from exc

    @staticmethod
    def _flatten_tool(schema: dict[str, Any]) -> dict[str, Any]:
        """Convert an OpenAI-shaped tool schema to Anthropic's flat form."""
        fn = schema.get("function", schema)
        return {
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        }

    async def health(self) -> HealthStatus:
        if not self._auth.is_configured():
            return HealthStatus(
                available=False,
                detail=f"Anthropic credentials are not configured ({self._auth.describe})",
            )
        try:
            headers = {
                "content-type": "application/json",
                "anthropic-version": API_VERSION,
                **await self._auth.headers(),
            }
            # A deliberately minimal request: cheapest way to verify the key.
            response = await self._http.post(
                f"{self._base}/messages",
                headers=headers,
                json={
                    "model": self._model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=15.0,
            )
        except (httpx.HTTPError, ProviderError) as exc:
            return HealthStatus(available=False, detail=str(exc))

        if response.status_code == 401:
            return HealthStatus(available=False, detail="authentication rejected (HTTP 401)")
        if response.status_code >= 500:
            return HealthStatus(available=False, detail=f"HTTP {response.status_code}")
        return HealthStatus(available=True, detail="ready")

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            supports_tools=True,
            max_context=self._max_context,
            is_local=False,
            model=self._model,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
