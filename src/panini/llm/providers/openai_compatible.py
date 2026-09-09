"""OpenAI-compatible chat provider.

Covers OpenAI, Azure OpenAI, Google's OpenAI-compatible endpoint, OpenRouter,
Groq, and most self-hosted gateways — they share the ``/chat/completions``
schema and differ only in base URL and authentication.

Authentication is supplied by an ``AuthStrategy``, so an API-key vendor and an
OAuth one (Azure Entra ID, Google user accounts) use the same code path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from panini.errors import ProviderError, ProviderUnavailable
from panini.llm.auth import AuthStrategy, NoAuth
from panini.llm.provider import (
    Capabilities,
    Chunk,
    GenerateOptions,
    HealthStatus,
    LLMProvider,
    Message,
    ToolCall,
)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        auth: AuthStrategy | None = None,
        name: str = "openai",
        max_context: int = 128_000,
        is_local: bool = False,
        http: httpx.AsyncClient | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self._base = base_url.rstrip("/")
        self._auth = auth or NoAuth()
        self._max_context = max_context
        self._is_local = is_local
        self._extra_body = extra_body or {}
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
        self._owns_http = http is None

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[Chunk]:
        options = options or GenerateOptions()
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "temperature": options.temperature,
            **self._extra_body,
            **options.extra,
        }
        if options.max_tokens:
            payload["max_tokens"] = options.max_tokens
        if tools:
            payload["tools"] = tools

        headers = {"Content-Type": "application/json", **await self._auth.headers()}

        # Tool-call arguments arrive fragmented across deltas and must be
        # reassembled before the call is dispatched.
        pending: dict[int, dict[str, Any]] = {}

        try:
            async with self._http.stream(
                "POST", f"{self._base}/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise ProviderError(
                        f"{self.name} returned HTTP {response.status_code}: {body}"
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        for call in self._flush(pending):
                            yield call
                        yield Chunk(done=True)
                        return

                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    for chunk in self._handle_event(event, pending):
                        yield chunk
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(self.name, str(exc)) from exc

    def _handle_event(
        self, event: dict[str, Any], pending: dict[int, dict[str, Any]]
    ) -> list[Chunk]:
        out: list[Chunk] = []
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}

            if content := delta.get("content"):
                out.append(Chunk(delta=content))

            for call in delta.get("tool_calls") or []:
                idx = int(call.get("index", 0))
                slot = pending.setdefault(idx, {"id": "", "name": "", "args": ""})
                if call.get("id"):
                    slot["id"] = call["id"]
                fn = call.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]

            if choice.get("finish_reason"):
                out.extend(self._flush(pending))
        return out

    @staticmethod
    def _flush(pending: dict[int, dict[str, Any]]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for idx in sorted(pending):
            slot = pending[idx]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError:
                # A malformed argument blob must not abort the turn; the tool
                # will report a validation error the model can respond to.
                args = {}
            chunks.append(
                Chunk(tool_call=ToolCall(id=slot["id"] or f"call-{idx}",
                                         name=slot["name"], args=args))
            )
        pending.clear()
        return chunks

    async def health(self) -> HealthStatus:
        if not self._auth.is_configured():
            return HealthStatus(
                available=False,
                detail=f"{self.name} credentials are not configured ({self._auth.describe})",
            )
        try:
            headers = await self._auth.headers()
            response = await self._http.get(f"{self._base}/models", headers=headers, timeout=10.0)
        except (httpx.HTTPError, ProviderError) as exc:
            return HealthStatus(available=False, detail=str(exc))

        if response.status_code == 401:
            return HealthStatus(available=False, detail="authentication rejected (HTTP 401)")
        if response.status_code >= 400:
            # Some gateways do not expose /models; treat reachability as enough.
            return HealthStatus(available=True, detail=f"reachable (HTTP {response.status_code})")

        models = [m.get("id", "") for m in response.json().get("data", [])]
        return HealthStatus(available=True, detail="ready", models=models)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            supports_tools=True,
            max_context=self._max_context,
            is_local=self._is_local,
            model=self._model,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
