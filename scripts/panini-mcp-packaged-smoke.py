"""Exercise packaged MCP transports with isolated data and no live provider calls."""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx2
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


def native_clients(url, executable, issued, directory):
    """Use installed client CLIs without invoking their agent models or global configuration."""
    claude = shutil.which("claude.cmd")
    codex = shutil.which("codex.exe")
    if not claude or not codex:
        raise RuntimeError("Install both Claude Code and Codex CLIs for native client qualification.")
    config = directory / "claude-native"
    config.mkdir()
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config.resolve())}
    spec = {
        "type": "stdio",
        "command": str(executable),
        "args": [
            "--url",
            url + "/mcp",
            "--client",
            issued["id"],
            "--token-file",
            issued["token_file"],
        ],
    }
    added = subprocess.run(
        [claude, "mcp", "add-json", "--scope", "user", "panini-native", json.dumps(spec)],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert added.returncode == 0 and "panini-native" in (config / ".claude.json").read_text(encoding="utf-8")
    health = subprocess.run(
        [claude, "mcp", "list"],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    )
    assert any(
        "panini-native:" in line and "Connected" in line and "Failed" not in line
        for line in health.stdout.splitlines()
    ), "Claude Code did not connect to PANINI's packaged stdio bridge."
    command = json.dumps(str(executable).replace("\\", "/"))
    args = json.dumps(spec["args"])
    listed = subprocess.run(
        [
            codex,
            "mcp",
            "list",
            "-c",
            f"mcp_servers.panini_native.command={command}",
            "-c",
            f"mcp_servers.panini_native.args={args}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert "panini_native" in listed.stdout and str(executable).replace("\\", "/") in listed.stdout
    return [
        "Claude Code CLI connected to packaged stdio bridge and listed server health",
        "Codex CLI parsed isolated packaged stdio configuration (agent tool discovery unqualified)",
    ]


async def exercise(url, executable, owner, *, native=False, live_browser=False, directory=None):
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
        if native:
            checks.extend(await asyncio.to_thread(native_clients, url, executable, issued, directory))
        listed = (await admin.get(url + "/api/admin/v1/mcp/clients")).json()
        assert issued["token"] not in json.dumps(listed)
        assert (await admin.delete(url + "/api/admin/v1/mcp/clients/" + issued["id"])).status_code == 200
        assert not Path(issued["token_file"]).exists()
        async with httpx2.AsyncClient() as http:
            assert (
                await http.post(url + "/mcp", headers={"Authorization": "Bearer " + issued["token"]}, json={})
            ).status_code == 401
        checks.append("hash-only listing/revoke/token file removal")
        isolated = await admin.post(
            url + "/api/admin/v1/mcp/clients", json={"name": "Packaged sandbox", "grants": ["sandbox"]}
        )
        isolated.raise_for_status()
        sandbox = isolated.json()

        async def approve(call, *, expected_error=False):
            async with asyncio.timeout(15):
                while True:
                    pending = (await admin.get(url + "/api/harness/interrupts")).json()["interrupts"]
                    if pending:
                        break
                    await asyncio.sleep(0.02)
                item = pending[0]
                response = await admin.post(
                    url + "/api/harness/interrupts/" + item["id"],
                    json={
                        "nonce": item["nonce"],
                        "action_digest": item["action_digest"],
                        "status": "resolved",
                        "payload": {"approved": True},
                    },
                )
                response.raise_for_status()
                result = await call
                assert result.is_error == expected_error, result.content[0].text
                return json.loads(result.content[0].text)

        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + sandbox["token"]}) as http:
            async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    assert "sandbox_exec" in names and not {"sandbox_import", "sandbox_export"} & names
                    await client.call_tool("sandbox_status", {})
                    await approve(
                        asyncio.create_task(
                            client.call_tool(
                                "sandbox_files_write",
                                {"path": "packaged.txt", "contents": "Packaged MCP café", "append": False},
                            )
                        )
                    )
                    result = await client.call_tool("sandbox_files_read", {"path": "packaged.txt"})
                    assert not result.is_error and "Packaged MCP café" in result.content[0].text
                    executed = await approve(
                        asyncio.create_task(
                            client.call_tool(
                                "sandbox_exec", {"command": "echo packaged-native-mcp", "timeoutMs": 5000}
                            )
                        )
                    )
                    assert executed["receipts"] and "packaged-native-mcp" in executed["result"]["stdout"]
                    assert executed["result"]["exitCode"] == 0
                    checks.append("packaged native AppContainer files/approved command/receipts")
        assert (await admin.delete(url + "/api/admin/v1/mcp/clients/" + sandbox["id"])).status_code == 200
        if live_browser:
            created = await admin.post(
                url + "/api/admin/v1/mcp/clients",
                json={"name": "Packaged browser", "grants": ["browser"]},
            )
            created.raise_for_status()
            browser_client = created.json()
            async with httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + browser_client["token"]}
            ) as http:
                async with streamable_http_client(url + "/mcp", http_client=http) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        names = {tool.name for tool in (await client.list_tools()).tools}
                        assert {"browser_navigate", "browser_snapshot", "browser_click"} <= names
                        refused = await approve(
                            asyncio.create_task(client.call_tool("browser_navigate", {"url": url + "/"})),
                            expected_error=True,
                        )
                        assert refused["outcome"] == "blocked"
                        checks.append("packaged MCP isolated browser refuses local/private navigation")
                        navigated = await approve(
                            asyncio.create_task(
                                client.call_tool("browser_navigate", {"url": "https://example.com/"})
                            )
                        )
                        assert navigated["receipts"]
                        snapshot = await client.call_tool("browser_snapshot", {})
                        assert not snapshot.is_error, snapshot.content[0].text
                        content = json.loads(snapshot.content[0].text)["result"]["page"]
                        assert "Example Domain" in content, content[:2000]
                        match = re.search(
                            r'link "(?:More information|Learn more)[^"]*" \[ref=([^\]]+)\]', content
                        )
                        assert match, content[:2000]
                        clicked = await approve(
                            asyncio.create_task(
                                client.call_tool(
                                    "browser_click",
                                    {"target": match[1], "element": "More information"},
                                )
                            )
                        )
                        assert clicked["receipts"]
                        after = await client.call_tool("browser_snapshot", {})
                        assert (
                            not after.is_error
                            and "iana.org" in json.loads(after.content[0].text)["result"]["page"].lower()
                        )
                        checks.append(
                            "packaged isolated Edge navigation/click with owner approvals and receipts"
                        )
            assert (
                await admin.delete(url + "/api/admin/v1/mcp/clients/" + browser_client["id"])
            ).status_code == 200
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("artifacts/panini-runtime"))
    parser.add_argument("--evidence", type=Path, default=Path("artifacts/mcp-server/packaged-smoke.json"))
    parser.add_argument(
        "--native-clients", action="store_true", help="Check installed Claude/Codex CLIs without model calls"
    )
    parser.add_argument(
        "--live-browser",
        action="store_true",
        help="Check real isolated Edge navigation on a public test page",
    )
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
            "PANINI_BROWSER_DRIVER": "playwright" if args.live_browser else "legacy",
            "PANINI_HARNESS_ENABLED": "true",
            "PANINI_V2_LEDGER": "true",
            "PANINI_V2_INTERRUPTS": "true",
            "PANINI_V2_POLICY": "off",
            "PANINI_SANDBOX": "true",
            "PANINI_SANDBOX_ENGINE": "native",
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
                checks = asyncio.run(
                    asyncio.wait_for(
                        exercise(
                            url,
                            bridge,
                            owner,
                            native=args.native_clients,
                            live_browser=args.live_browser,
                            directory=Path(data),
                        ),
                        180 if args.live_browser else 90,
                    )
                )
                args.evidence.write_text(
                    json.dumps(
                        {
                            "passed": True,
                            "sourceCommit": json.loads(
                                (runtime / "release.json").read_text(encoding="utf-8")
                            )["sourceCommit"],
                            "runtime": str(runtime),
                            "checks": checks,
                            "qualification": "Packaged transports and real Windows AppContainer execution; no live model, desktop/browser CLI agent or physical Android qualification.",
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
