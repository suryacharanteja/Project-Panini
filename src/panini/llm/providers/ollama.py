"""Ollama provider — the local default (DD-041, DD-102..104).

``num_gpu`` is asserted at construction. Omitting it is a silent ~60% throughput
loss (22.09 → 35.26 tok/s measured) that no functional test would catch, because
the assistant still works — just slowly enough to feel broken.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from panini.errors import ProviderError, ProviderUnavailable
from panini.llm.provider import (
    Capabilities,
    Chunk,
    GenerateOptions,
    HealthStatus,
    LLMProvider,
    Message,
    ToolCall,
)

DEFAULT_HOST = "http://localhost:11434"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        model: str,
        *,
        host: str = DEFAULT_HOST,
        options: dict[str, Any] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        opts = dict(options or {})
        if "num_gpu" not in opts:
            # Refuse rather than default: a caller that forgot this would get a
            # working-but-slow assistant and no signal as to why.
            raise ProviderError(
                "OllamaProvider requires an explicit num_gpu. Ollama's automatic "
                "layer placement left ~1.8 GB of VRAM unused in measurement, "
                "costing ~60% throughput (DD-102)."
            )
        self._model = model
        self._host = host.rstrip("/")
        self._options = opts
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0))
        self._owns_http = http is None

    # ── generation ───────────────────────────────────────────────────────────

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[Chunk]:
        opts = dict(self._options)
        if options:
            opts["temperature"] = options.temperature
            if options.max_tokens:
                opts["num_predict"] = options.max_tokens
            opts.update(options.extra)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "options": {k: v for k, v in opts.items() if k != "keep_alive"},
        }
        if (keep_alive := self._options.get("keep_alive")) is not None:
            payload["keep_alive"] = keep_alive
        if tools:
            payload["tools"] = tools

        try:
            async with self._http.stream(
                "POST", f"{self._host}/api/chat", json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")[:400]
                    raise ProviderError(f"Ollama returned HTTP {response.status_code}: {body}")

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    for chunk in self._parse_line(line):
                        yield chunk
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(self.name, str(exc)) from exc

    @staticmethod
    def _parse_line(line: str) -> list[Chunk]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []

        chunks: list[Chunk] = []
        message = data.get("message") or {}

        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            chunks.append(
                Chunk(
                    tool_call=ToolCall(
                        id=str(call.get("id") or f"call-{i}"),
                        name=str(fn.get("name", "")),
                        args=dict(args or {}),
                    )
                )
            )

        if content := message.get("content"):
            chunks.append(Chunk(delta=content))

        if data.get("done"):
            chunks.append(Chunk(done=True, finish_reason=data.get("done_reason")))

        return chunks

    # ── health ───────────────────────────────────────────────────────────────

    async def health(self) -> HealthStatus:
        try:
            response = await self._http.get(f"{self._host}/api/tags", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return HealthStatus(available=False, detail=f"Ollama unreachable at {self._host}: {exc}")

        models = [m.get("name", "") for m in response.json().get("models", [])]
        if self._model not in models:
            return HealthStatus(
                available=False,
                detail=f"model {self._model!r} is not pulled. Run: ollama pull {self._model}",
                models=models,
            )
        return HealthStatus(available=True, detail="ready", models=models)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            supports_tools=True,
            max_context=int(self._options.get("num_ctx", 8192)),
            is_local=True,
            model=self._model,
        )

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
