import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx

from backend.harness.connectors import Connectors
from backend.harness.mcp_http import _challenge
from backend.harness.secrets_store import SecretStore

MCP = "https://mcp.example.com/mcp"


class Provider:
    """A mock OAuth authorization server plus a protected MCP server."""

    def __init__(self):
        self.codes, self.tokens, self.registered, self.seen = {}, set(), [], []

    def __call__(self, request):
        url = str(request.url)
        self.seen.append((request.method, url.split("?")[0]))
        if url.startswith(MCP):
            token = request.headers.get("authorization", "")[7:]
            if token not in self.tokens:
                return httpx.Response(
                    401,
                    headers={
                        "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'
                    },
                )
            body = json.loads(request.content)
            if "id" not in body:
                return httpx.Response(202)
            if body["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {}}
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": body["id"], "result": result},
                    headers={"Mcp-Session-Id": "s1"},
                )
            if body["method"] == "tools/list":
                result = {"tools": [{"name": "list_issues", "annotations": {"readOnlyHint": True}}]}
            else:
                result = {"content": [{"type": "text", "text": "3 open issues"}]}
            # Answer as an event stream, as Streamable HTTP servers may.
            stream = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})}\n\n"
            return httpx.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})
        if url.endswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(
                200, json={"resource": MCP, "authorization_servers": ["https://auth.example.com"]}
            )
        if url == "https://auth.example.com/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                },
            )
        if url == "https://auth.example.com/register":
            self.registered.append(json.loads(request.content))
            return httpx.Response(201, json={"client_id": "panini-client"})
        if url == "https://auth.example.com/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                verifier = form["code_verifier"][0]
                if self.codes.get(form["code"][0]) != _challenge(verifier):
                    return httpx.Response(400, json={"error": "invalid_grant"})
                token = "access-1"
            else:
                token = "access-2"
            self.tokens.add(token)
            return httpx.Response(
                200, json={"access_token": token, "refresh_token": "r1", "expires_in": 3600}
            )
        return httpx.Response(404)


def test_sign_in_flow_then_use_and_refresh(tmp_path):
    provider = Provider()
    path = tmp_path / "connectors.json"
    path.write_text(json.dumps({"servers": {"issues": {"url": MCP}}}))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            connectors = Connectors(path, secrets=SecretStore(tmp_path / "s.bin"), http_client=http)
            [row] = await connectors.status()
            assert row["state"] == "sign_in" and row["kind"] == "remote"

            link = await connectors.sign_in(
                "issues", "http://localhost:3200/api/harness/connectors/oauth/callback"
            )
            query = parse_qs(urlsplit(link).query)
            assert query["code_challenge_method"] == ["S256"] and query["resource"] == [MCP]
            assert provider.registered[0]["token_endpoint_auth_method"] == "none"
            # The provider redirects back with a code bound to the PKCE challenge.
            provider.codes["code-1"] = query["code_challenge"][0]
            try:
                await connectors.complete("wrong-state", "code-1")
                raise AssertionError("a forged state must be refused")
            except Exception as exc:  # noqa: BLE001
                assert "expired" in str(exc)
            assert await connectors.complete(query["state"][0], "code-1") == "issues"

            [row] = await connectors.status()
            assert row["state"] == "ready" and row["signed_in"] and row["tools"][0]["read_only"]
            server, tool = connectors.tool("issues", "list_issues")
            assert (await server.call("list_issues", {}))["text"] == "3 open issues"

            # The access token expires: PANINI refreshes it and retries once.
            provider.tokens.discard("access-1")
            assert (await server.call("list_issues", {}))["text"] == "3 open issues"
            assert ("POST", "https://auth.example.com/token") in provider.seen

            connectors.sign_out("issues")
            assert SecretStore(tmp_path / "s.bin").get("oauth:issues") is None

    asyncio.run(go())


def test_secrets_are_encrypted_at_rest(tmp_path):
    store = SecretStore(tmp_path / "s.bin")
    store.set("oauth:x", {"access_token": "very-secret-token"})
    assert b"very-secret-token" not in (tmp_path / "s.bin").read_bytes()
    assert store.get("oauth:x")["access_token"] == "very-secret-token"


def test_plain_http_remote_servers_are_refused(tmp_path):
    path = tmp_path / "connectors.json"
    path.write_text(json.dumps({"servers": {"bad": {"url": "http://evil.example.com/mcp"}}}))
    rows = asyncio.run(Connectors(path, secrets=SecretStore(tmp_path / "s.bin")).status())
    assert rows[0]["state"] == "error" and "https" in rows[0]["error"]
