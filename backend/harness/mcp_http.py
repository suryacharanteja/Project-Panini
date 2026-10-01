"""Remote MCP servers over Streamable HTTP, with OAuth 2.1 sign-in.

connectors.json:
    {"servers": {"linear": {"url": "https://mcp.linear.app/mcp"},
                 "internal": {"url": "https://mcp.example.com/mcp",
                              "headers": {"Authorization": "Bearer ..."}}}}

When a server answers 401, PANINI follows the MCP authorization spec: it reads the
protected-resource metadata, discovers the authorization server, registers itself as
a public client (dynamic client registration) when the server allows it, and builds
a PKCE sign-in link. You open that link from the console; the provider redirects back
to PANINI's local callback and the tokens are stored encrypted (secrets_store.py).
Access tokens are refreshed automatically. Only https URLs are accepted, except for
localhost during development.
"""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx

from .connectors import PROTOCOL, ConnectorError


class SignInRequired(ConnectorError):
    pass


def _allowed(url):
    parts = urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")
    )


def _challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _resource_metadata_url(response, url):
    header = response.headers.get("www-authenticate", "")
    for part in header.replace(",", " ").split():
        if part.startswith("resource_metadata="):
            return part.split("=", 1)[1].strip('"')
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/.well-known/oauth-protected-resource"


class HttpServer:
    """Same interface as the stdio Server: start, tools, call, close, error."""

    def __init__(self, name, spec, secrets_store, *, client=None):
        import asyncio

        self.name, self.spec, self.secrets = name, spec, secrets_store
        self.url = spec["url"]
        self.client = client
        self.tools, self.error, self.session_id = [], "", None
        self.sequence = 0
        self.lock = asyncio.Lock()
        self.started = False
        self.pending_auth = None  # {"state", "verifier", "redirect", "token_endpoint", ...}
        self.sign_in_url = ""

    # -- HTTP ---------------------------------------------------------------
    def _http(self):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=60, follow_redirects=False)
        return self.client

    def _tokens(self):
        return self.secrets.get(f"oauth:{self.name}") or {}

    def _headers(self):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL,
            **{k: str(v) for k, v in (self.spec.get("headers") or {}).items()},
        }
        tokens = self._tokens()
        if tokens.get("access_token"):
            headers["Authorization"] = f"Bearer {tokens['access_token']}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(self, message, *, retried=False):
        if not _allowed(self.url):
            raise ConnectorError(f"Connector {self.name} must use https.")
        response = await self._http().post(self.url, json=message, headers=self._headers())
        if response.status_code == 401:
            if not retried and await self._refresh():
                return await self._post(message, retried=True)
            await self._prepare_sign_in(response)
            raise SignInRequired(f"Connector {self.name} needs you to sign in.")
        if response.status_code == 404 and self.session_id and not retried:
            self.session_id = None  # The server forgot the session; start again.
            self.started = False
            raise ConnectorError(f"Connector {self.name} restarted; retry.")
        if response.status_code >= 400:
            raise ConnectorError(f"Connector {self.name} answered HTTP {response.status_code}.")
        if session := response.headers.get("mcp-session-id"):
            self.session_id = session
        if "id" not in message:
            return None
        return self._result(response, message["id"])

    def _result(self, response, identifier):
        kind = response.headers.get("content-type", "")
        messages = []
        if "text/event-stream" in kind:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    try:
                        messages.append(json.loads(line[5:].strip()))
                    except ValueError:
                        continue
        else:
            body = response.json()
            messages = body if isinstance(body, list) else [body]
        for message in messages:
            if isinstance(message, dict) and message.get("id") == identifier:
                if "error" in message:
                    raise ConnectorError(str(message["error"].get("message", "error"))[:500])
                return message.get("result")
        raise ConnectorError(f"Connector {self.name} gave no answer.")

    async def request(self, method, params):
        self.sequence += 1
        return await self._post({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params})

    # -- Lifecycle ------------------------------------------------------------
    async def start(self):
        if self.started:
            return
        try:
            await self.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "PANINI", "version": "1.0.0"},
                },
            )
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            listed = await self.request("tools/list", {})
        except SignInRequired as exc:
            self.error = str(exc)
            raise
        except (httpx.HTTPError, ValueError) as exc:
            self.error = f"Could not reach the server: {str(exc)[:200]}"
            raise ConnectorError(self.error) from None
        self.tools = [
            {
                "name": t["name"],
                "description": (t.get("description") or "")[:500],
                "read_only": bool((t.get("annotations") or {}).get("readOnlyHint")),
                "input_schema": t.get("inputSchema") or {"type": "object"},
            }
            for t in (listed or {}).get("tools", [])[:100]
        ]
        self.started, self.error, self.sign_in_url = True, "", ""

    async def call(self, tool, arguments):
        async with self.lock:
            await self.start()
            try:
                result = await self.request("tools/call", {"name": tool, "arguments": arguments})
            except httpx.HTTPError as exc:
                raise ConnectorError(f"Connector {self.name} failed: {str(exc)[:200]}") from None
        parts = [
            p.get("text", "") if p.get("type") == "text" else f"[{p.get('type', 'content')} omitted]"
            for p in (result or {}).get("content", [])
        ]
        return {"is_error": bool((result or {}).get("isError")), "text": "\n".join(parts)[:20000]}

    async def close(self):
        if self.client is not None and not self.spec.get("_shared_client"):
            await self.client.aclose()
            self.client = None

    # -- OAuth ----------------------------------------------------------------
    async def _prepare_sign_in(self, response):
        """Discover the provider and register PANINI; sign_in() then returns the link."""
        http = self._http()
        metadata_url = _resource_metadata_url(response, self.url)
        resource = (await http.get(metadata_url)).json()
        issuer = (resource.get("authorization_servers") or [None])[0]
        if not issuer:
            raise ConnectorError(f"Connector {self.name} did not name an authorization server.")
        server = None
        base = issuer.rstrip("/")
        for well_known in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            answer = await http.get(base + well_known)
            if answer.status_code == 200:
                server = answer.json()
                break
        if not server or not _allowed(server.get("authorization_endpoint", "")):
            raise ConnectorError(f"Connector {self.name}: its sign-in service could not be discovered.")
        self.pending_auth = {
            "authorization_endpoint": server["authorization_endpoint"],
            "token_endpoint": server["token_endpoint"],
            "registration_endpoint": server.get("registration_endpoint"),
            "resource": resource.get("resource") or self.url,
            "scopes": resource.get("scopes_supported") or [],
        }
        self.error = f"Connector {self.name} needs you to sign in."

    async def sign_in(self, redirect_uri):
        """A fresh PKCE sign-in link for the person to open in their browser."""
        if not self.pending_auth:
            try:
                await self.start()
                return None  # Already signed in.
            except SignInRequired:
                pass
        auth = self.pending_auth
        client = self.secrets.get(f"client:{self.name}") or {}
        if client.get("redirect_uri") != redirect_uri:
            client = {}
        if not client and self.spec.get("client_id"):
            client = {"client_id": self.spec["client_id"], "redirect_uri": redirect_uri}
        if not client:
            if not auth["registration_endpoint"]:
                raise ConnectorError(
                    f"{self.name} does not allow automatic registration; add client_id to connectors.json."
                )
            registered = await self._http().post(
                auth["registration_endpoint"],
                json={
                    "client_name": "PANINI",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
            if registered.status_code >= 400:
                raise ConnectorError(
                    f"{self.name} refused to register PANINI (HTTP {registered.status_code})."
                )
            client = {"client_id": registered.json()["client_id"], "redirect_uri": redirect_uri}
            self.secrets.set(f"client:{self.name}", client)
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
        auth.update(state=state, verifier=verifier, client_id=client["client_id"], redirect=redirect_uri)
        query = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "resource": auth["resource"],
        }
        if auth["scopes"]:
            query["scope"] = " ".join(auth["scopes"])
        self.sign_in_url = f"{auth['authorization_endpoint']}?{urlencode(query)}"
        return self.sign_in_url

    async def complete(self, state, code):
        auth = self.pending_auth
        if not auth or not state or not secrets.compare_digest(state, auth.get("state", "")):
            raise ConnectorError("This sign-in link is not the latest one; start again.")
        answer = await self._http().post(
            auth["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": auth["redirect"],
                "client_id": auth["client_id"],
                "code_verifier": auth["verifier"],
                "resource": auth["resource"],
            },
        )
        if answer.status_code >= 400:
            raise ConnectorError(f"{self.name} did not accept the sign-in (HTTP {answer.status_code}).")
        self._save_tokens(answer.json(), auth)
        self.pending_auth, self.started, self.session_id = None, False, None
        await self.start()

    def _save_tokens(self, body, auth):
        self.secrets.set(
            f"oauth:{self.name}",
            {
                "access_token": body["access_token"],
                "refresh_token": body.get("refresh_token") or self._tokens().get("refresh_token"),
                "expires_at": time.time() + int(body.get("expires_in") or 3600),
                "token_endpoint": auth["token_endpoint"],
                "client_id": auth["client_id"],
                "resource": auth["resource"],
            },
        )

    async def _refresh(self):
        tokens = self._tokens()
        if not tokens.get("refresh_token"):
            return False
        answer = await self._http().post(
            tokens["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": tokens["client_id"],
                "resource": tokens["resource"],
            },
        )
        if answer.status_code >= 400:
            self.secrets.set(f"oauth:{self.name}", None)
            return False
        self._save_tokens(answer.json(), tokens)
        return True

    def sign_out(self):
        self.secrets.set(f"oauth:{self.name}", None)
        self.started, self.session_id, self.tools = False, None, []
