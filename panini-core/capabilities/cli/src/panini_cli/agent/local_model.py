"""Loopback OpenAI-compatible chat model for opt-in local and private runs."""

import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .model import ModelError, ModelTurn, ToolCall


def endpoint(raw):
    parts = urlsplit((raw or "").strip().rstrip("/"))
    if (
        parts.scheme != "http"
        or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/v1"}
    ):
        raise ModelError(
            "Set PANINI_LOCAL_BASE_URL to a loopback HTTP model server, such as http://127.0.0.1:11434/v1."
        )
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is None or not 1 <= port <= 65535:
        raise ModelError("PANINI_LOCAL_BASE_URL needs an explicit loopback port.")
    return parts.geturl().rstrip("/") + ("" if parts.path == "/v1" else "/v1") + "/chat/completions"


def messages_from_items(instructions, items):
    messages = [{"role": "system", "content": instructions}]
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id", "")),
                    "content": str(item.get("output", "")),
                }
            )
        elif kind == "function_call":
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": str(item.get("call_id", "")),
                            "type": "function",
                            "function": {
                                "name": str(item.get("name", "")),
                                "arguments": str(item.get("arguments", "{}")),
                            },
                        }
                    ],
                }
            )
        elif item.get("role") in {"user", "assistant"}:
            content = item.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") in {"output_text", "input_text", "text"}
                )
            if isinstance(content, str) and content:
                messages.append({"role": item["role"], "content": content})
    return messages


class LocalModel:
    def __init__(self, model=None, base_url=None, timeout=180.0, client=None):
        self.model = (model or os.getenv("PANINI_LOCAL_MODEL", "")).strip()
        if not self.model:
            raise ModelError("Set PANINI_LOCAL_MODEL before using local mode.")
        self.url = endpoint(base_url if base_url is not None else os.getenv("PANINI_LOCAL_BASE_URL"))
        self.client = client or httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)
        self.owns_client = client is None

    def switch_model(self, _model):
        # Cloud tier names must never replace the configured local model.
        return None

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def probe(self):
        """Check the configured model's tool-call response without sending user data."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Call the ping tool with an empty object."},
                {"role": "user", "content": "ping"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "ping",
                        "description": "Connection test",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": "required",
            "stream": False,
            "max_tokens": 64,
        }
        try:
            response = await self.client.post(self.url, json=payload)
        except (httpx.TimeoutException, httpx.TransportError):
            raise ModelError("The local model server is unavailable.") from None
        if response.status_code != 200 or len(response.content) > 100_000:
            raise ModelError(f"The local model tool test failed (HTTP {response.status_code}).")
        try:
            calls = response.json()["choices"][0]["message"].get("tool_calls") or []
            if len(calls) != 1 or calls[0]["function"]["name"] != "ping":
                raise ValueError("Missing ping call")
            if json.loads(calls[0]["function"]["arguments"]) != {}:
                raise ValueError("Invalid ping arguments")
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelError("The local model did not produce a valid tool call.") from None
        return {"model": self.model, "tool_calls": True}

    async def respond(
        self,
        instructions,
        items,
        tools,
        on_text=None,
        output_schema=None,
        cache_key=None,
        max_output_tokens=None,
    ):
        if len(items) > 500 or len(tools) > 200:
            raise ModelError("The local model request is too large.")
        payload = {
            "model": self.model,
            "messages": messages_from_items(instructions, items),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
                for tool in tools
                if tool.get("type") == "function"
            ],
            "stream": False,
        }
        if not payload["tools"]:
            del payload["tools"]
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "final_answer", "schema": output_schema, "strict": True},
            }
        try:
            response = await self.client.post(self.url, json=payload)
        except (httpx.TimeoutException, httpx.TransportError):
            raise ModelError("The local model server is unavailable. Check PANINI_LOCAL_BASE_URL.") from None
        if response.status_code != 200:
            raise ModelError(f"The local model rejected the request (HTTP {response.status_code}).")
        if len(response.content) > 4_000_000:
            raise ModelError("The local model response is too large.")
        try:
            body = response.json()
            message = body["choices"][0]["message"]
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise ValueError("Invalid local model text")
            output = []
            if text:
                output.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            calls = []
            for tool in message.get("tool_calls") or []:
                function = tool["function"]
                call_id = str(tool.get("id") or "call_local_" + uuid4().hex[:16])
                arguments = function["arguments"]
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments)
                json.loads(arguments)  # Reject malformed tool arguments at the provider boundary.
                name = function["name"]
                calls.append(ToolCall(call_id, name, arguments))
                output.append(
                    {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
                )
            usage = body.get("usage") or {}
            usage = {
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "cost_usd": 0.0,
            }
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise ModelError("The local model returned an invalid chat response.") from None
        if text and on_text:
            await on_text("msg_local_" + uuid4().hex[:16], text)
        return ModelTurn(output, text, calls, usage)


class FallbackModel:
    """Use a local model for the rest of a run after a transient cloud failure."""

    def __init__(self, cloud, local_factory):
        self.cloud, self.local_factory, self.local = cloud, local_factory, None

    @property
    def model(self):
        return self.local.model if self.local is not None else self.cloud.model

    def switch_model(self, name):
        if self.local is None:
            self.cloud.switch_model(name)

    async def respond(self, *args, on_text=None, **kwargs):
        if self.local is not None:
            return await self.local.respond(*args, on_text=on_text, **kwargs)
        from .model import TransientModelError

        pending = []

        async def buffer(item_id, delta):
            pending.append((item_id, delta))

        try:
            result = await self.cloud.respond(*args, on_text=buffer if on_text else None, **kwargs)
        except TransientModelError:
            self.local = self.local_factory()
            return await self.local.respond(*args, on_text=on_text, **kwargs)
        if on_text:
            for item_id, delta in pending:
                await on_text(item_id, delta)
        return result
