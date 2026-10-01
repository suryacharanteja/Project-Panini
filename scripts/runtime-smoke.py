"""End-to-end check of the unified PANINI runtime without provider calls or browser input.

Starts the real runtime (gateway, backend and search) with a throwaway data
directory, then verifies every surface through real HTTP and WebSocket traffic:
console sign-in, the console boundary, extension pairing and the mutual
handshake, impostor rejection, and the PANINI shell API. Run: npm run test:runtime
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.getenv("FRONTEND_PORT", "3200"))
BASE = f"http://127.0.0.1:{PORT}"
BACKEND = f"http://127.0.0.1:{os.getenv('BACKEND_PORT', '8200')}"
EXTENSION = "chrome-extension://runtimesmoke"


def proof(secret, role, first, second):
    message = f"panini-extension-v1|{role}|{first}|{second}".encode()
    return hmac.new(bytes.fromhex(secret), message, hashlib.sha256).hexdigest()


def check(condition, label):
    if not condition:
        raise AssertionError(label)
    print("PASS", label, flush=True)


async def handshake(device, *, secret=None, origin=EXTENSION):
    """Connect as the extension; returns the server's final message or close reason."""
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/extension", origin=origin) as ws:
        nonce = secrets.token_urlsafe(32)
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "source": "jev-rev-browxtension",
                    "protocol": 4,
                    "version": "3.0.0",
                    "device_id": device["device_id"],
                    "nonce": nonce,
                    "tab": 7,
                    "attachment_id": "smoke",
                    "tab_title": "Smoke tab",
                }
            )
        )
        server_ok = None
        try:
            challenge = json.loads(await ws.recv())
            server_ok = challenge["proof"] == proof(device["secret"], "server", nonce, challenge["nonce"])
            answer = proof(secret or device["secret"], "client", challenge["nonce"], nonce)
            await ws.send(json.dumps({"type": "auth", "proof": answer}))
            return server_ok, json.loads(await ws.recv())
        except websockets.ConnectionClosed as closed:
            return server_ok, {"closed": closed.rcvd.code if closed.rcvd else None}


async def main():
    data = tempfile.mkdtemp(prefix="panini-smoke-")
    env = {**os.environ, "PANINI_DATA_DIR": data}
    runtime = subprocess.Popen(
        ["node", str(ROOT / "frontend/server/main.mjs")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    link = None
    try:
        deadline = time.monotonic() + 60
        for line in runtime.stdout:
            match = re.search(r"(http://localhost:\d+/auth/login\?ticket=\S+)", line)
            if match:
                link = match.group(1)
                break
            if time.monotonic() > deadline:
                break
        check(link is not None, "runtime starts and prints a one-time console sign-in link")
        async with httpx.AsyncClient(timeout=10) as http:
            for _ in range(60):
                try:
                    if (await http.get(f"{BASE}/api/panini/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            health = (await http.get(f"{BASE}/api/panini/health")).json()
            check(health["status"] == "ok", "PANINI coordinator is always on in the runtime")

            # Console boundary: nothing controls capabilities without signing in.
            check(
                (await http.post(f"{BASE}/api/browser/stop")).status_code == 401, "gateway requires sign-in"
            )
            check(
                (await http.post(f"{BACKEND}/api/browser/stop")).status_code == 401,
                "backend refuses console routes without the gateway token",
            )
            login = await http.get(link.replace("localhost", "127.0.0.1"))
            check(
                login.status_code == 303 and "panini_console" in login.headers["set-cookie"],
                "sign-in link works",
            )
            reused = await http.get(link.replace("localhost", "127.0.0.1"))
            check("expired" in reused.headers["location"], "sign-in link works only once")
            cookie = {"cookie": login.headers["set-cookie"].split(";")[0]}
            check(
                (await http.post(f"{BASE}/api/browser/stop", headers=cookie)).status_code == 200,
                "console works",
            )
            observer = (await http.post(f"{BASE}/api/console/panini-observer", headers=cookie)).json()
            check(observer["session_id"] is None, "console can watch PANINI (no conversation yet)")

            # Extension: pair once, then a mutually authenticated connection.
            code = (await http.post(f"{BASE}/api/extension/pairing-code", headers=cookie)).json()["code"]
            wrong = await http.post(
                f"{BASE}/api/extension/pair", json={"code": "AAAA-AAAA"}, headers={"origin": EXTENSION}
            )
            check(wrong.status_code == 403, "a wrong pairing code is refused")
            device = (
                await http.post(
                    f"{BASE}/api/extension/pair", json={"code": code}, headers={"origin": EXTENSION}
                )
            ).json()
            check(len(device["secret"]) == 64, "extension pairs with a one-time code")
            stranger = {"device_id": secrets.token_hex(16), "secret": secrets.token_hex(32)}
            _, refused = await handshake(stranger)
            check(refused.get("closed") == 1008, "an unpaired client cannot pose as the extension")
            server_ok, refused = await handshake(device, secret=secrets.token_hex(32))
            check(server_ok and refused.get("closed") == 1008, "a client without the secret fails the proof")
            _, refused = await handshake(device, origin="chrome-extension://someoneelse")
            check(refused.get("closed") == 1008, "another extension cannot use this pairing")
            async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/extension", origin=EXTENSION) as ws:
                nonce = secrets.token_urlsafe(32)
                await ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "source": "jev-rev-browxtension",
                            "protocol": 4,
                            "version": "3.0.0",
                            "device_id": device["device_id"],
                            "nonce": nonce,
                            "tab": 7,
                            "attachment_id": "smoke",
                            "tab_title": "Smoke tab",
                        }
                    )
                )
                challenge = json.loads(await ws.recv())
                check(
                    challenge["proof"] == proof(device["secret"], "server", nonce, challenge["nonce"]),
                    "the runtime proves the pairing to the extension",
                )
                await ws.send(
                    json.dumps(
                        {
                            "type": "auth",
                            "proof": proof(device["secret"], "client", challenge["nonce"], nonce),
                        }
                    )
                )
                check(json.loads(await ws.recv())["type"] == "connected", "paired extension connects")
                status = (await http.get(f"{BASE}/api/browser/extension/status", headers=cookie)).json()
                check(
                    status["connected"]
                    and status["paired"]
                    and status["compatible"]
                    and status["attached_tab"] == 7,
                    "web console sees the attached Chrome tab",
                )
            devices = (await http.get(f"{BASE}/api/extension/devices", headers=cookie)).json()["devices"]
            check(len(devices) == 1, "pairing persists in the runtime data directory")
            # The Vite dev server may still be optimising dependencies on a cold start.
            for _ in range(30):
                page = await http.get(f"{BASE}/search/", headers=cookie)
                if page.status_code != 502:
                    break
                await asyncio.sleep(1)
            check(
                page.status_code == 200, f"search pages are served by the same runtime ({page.status_code})"
            )
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(runtime.pid), "/T", "/F"], capture_output=True)
        else:
            runtime.terminate()
        runtime.wait(timeout=15)
    print("Unified runtime smoke passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as error:
        print("FAIL", error)
        sys.exit(1)
