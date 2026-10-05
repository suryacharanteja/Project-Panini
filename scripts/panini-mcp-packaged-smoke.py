"""Exercise packaged MCP transports with isolated data and no live provider calls."""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


async def exercise(url, executable, owner):
    checks = []
    expires, nonce = int(time.time() * 1000) + 60_000, secrets.token_urlsafe(16)
    payload = f"v1.{expires}.{nonce}"
    mac = (
        base64.urlsafe_b64encode(hmac.digest(owner.encode(), payload.encode(), hashlib.sha256))
        .decode()
        .rstrip("=")
    )
    async with httpx2.AsyncClient() as admin:
        login = await admin.get(url + "/auth/login", params={"ticket": payload + "." + mac})
        assert login.status_code == 303 and "panini_console" in login.headers["set-cookie"]
        checks.append("packaged gateway owner sign-in")
        created = await admin.post(
            url + "/api/admin/v1/mcp/clients", json={"name": "Packaged smoke", "grants": []}
        )
        created.raise_for_status()
        issued = created.json()
        assert issued["stdio"]["command"] == str(executable)
        assert issued["endpoint"] == url + "/mcp"
        checks.append("owner registration and installation-specific setup")
        assert Path(issued["token_file"]).is_file()
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + issued["token"]}) as http:
            assert (await http.get(url + "/api/admin/v1/mcp/clients")).status_code == 401
            assert (await http.post(url + "/mcp", headers={"Origin": url}, json={})).status_code == 403
            checks.append("independent bearer and Origin boundary")
            async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    assert {t.name for t in (await client.list_tools()).tools} == {
                        "panini_status",
                        "receipts_search",
                        "panini_ask",
                    }
                    progress = []

                    async def report(value, total, message):
                        progress.append(value)

                    result = await client.call_tool("panini_status", {}, progress_callback=report)
                    assert not result.is_error and progress
                    payload = json.loads(result.content[0].text)
                    assert payload["receipts"] and payload["result"]["client"]["id"] == issued["id"]
                    checks.append("official SDK HTTP initialize/list/status/progress/receipts")
            async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.discover()
                    assert not (await client.call_tool("panini_status", {})).is_error
                    checks.append("official SDK modern protocol discover/status")
        parameters = StdioServerParameters(
            command=str(executable),
            args=["--url", url + "/mcp", "--client", issued["id"], "--token-file", issued["token_file"]],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                assert "panini_status" in {t.name for t in (await client.list_tools()).tools}
                assert not (await client.call_tool("panini_status", {})).is_error
                checks.append("packaged stdio EXE initialize/list/status")
        listed = (await admin.get(url + "/api/admin/v1/mcp/clients")).json()
        assert issued["token"] not in json.dumps(listed)
        assert (await admin.delete(url + "/api/admin/v1/mcp/clients/" + issued["id"])).status_code == 200
        assert not Path(issued["token_file"]).exists()
        async with httpx2.AsyncClient() as http:
            assert (
                await http.post(url + "/mcp", headers={"Authorization": "Bearer " + issued["token"]}, json={})
            ).status_code == 401
        checks.append("hash-only listing/revoke/token file removal")
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("artifacts/panini-runtime"))
    parser.add_argument("--evidence", type=Path, default=Path("artifacts/mcp-server/packaged-smoke.json"))
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    backend = runtime / "backend/Panini.Backend.exe"
    bridge = runtime / "cli/panini-mcp.exe"
    assert backend.is_file() and bridge.is_file()
    with socket.socket() as sock, socket.socket() as backend_sock:
        sock.bind(("127.0.0.1", 0))
        backend_sock.bind(("127.0.0.1", 0))
        port, backend_port = sock.getsockname()[1], backend_sock.getsockname()[1]
    url, owner = f"http://127.0.0.1:{port}", secrets.token_urlsafe(32)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mcp-packaged-", dir=args.evidence.parent) as data:
        env = {
            **os.environ,
            "PANINI_DATA_DIR": str(Path(data).resolve()),
            "PANINI_RUNTIME_ROOT": str(runtime),
            "FRONTEND_PORT": str(port),
            "BACKEND_PORT": str(backend_port),
            "PANINI_CONSOLE_SECRET": owner,
            "PANINI_BACKEND_EXECUTABLE": str(backend),
            "PANINI_MCP_SERVER": "true",
            "PANINI_HARNESS_ENABLED": "true",
            "PANINI_V2_LEDGER": "true",
            "PANINI_V2_INTERRUPTS": "true",
            "PANINI_V2_POLICY": "off",
            "PANINI_REMOTE_MODE": "off",
            "PANINI_PHONE_LINE": "false",
            "PANINI_CLI_ENABLED": "false",
            "JEV_REV_DESKTOP_ENABLED": "false",
            "PANINI_CAPABILITY_ROUTER_ENABLED": "false",
            "PANINI_JEV_GATES": "false",
            "PANINI_LOCAL_AGENT_ENABLED": "false",
            "AI_GATEWAY_API_KEY": "",
            "OPENAI_API_KEY": "",
        }
        with (args.evidence.parent / "packaged-backend.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [
                    str(runtime / "node/node.exe"),
                    str(runtime / "scripts/panini-runtime.mjs"),
                    "--production",
                    "--managed",
                ],
                cwd=runtime,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                deadline = time.monotonic() + 60
                with httpx2.Client() as client:
                    while time.monotonic() < deadline:
                        assert process.poll() is None, (
                            "Packaged backend stopped; inspect packaged-backend.log"
                        )
                        try:
                            if client.get(url + "/api/panini/health").status_code == 200:
                                break
                        except httpx2.TransportError:
                            pass
                        time.sleep(0.2)
                    else:
                        raise TimeoutError("Packaged backend did not become ready")
                checks = asyncio.run(asyncio.wait_for(exercise(url, bridge, owner), 45))
                args.evidence.write_text(
                    json.dumps(
                        {
                            "passed": True,
                            "sourceCommit": json.loads(
                                (runtime / "release.json").read_text(encoding="utf-8")
                            )["sourceCommit"],
                            "runtime": str(runtime),
                            "checks": checks,
                            "qualification": "Packaged transports only; no live model, hands, CLI agent or hardware qualification.",
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print(f"PANINI_MCP_PACKAGED_SMOKE_OK ({len(checks)} checks)")
            finally:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=True
                    )
                else:
                    process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    main()
