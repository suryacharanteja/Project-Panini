"""Owner setup only. ADB commands are argument arrays; models cannot access this interface."""

import asyncio
import ipaddress
import os
import re
import shutil
from pathlib import Path

DOWNLOAD_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
LICENSE_URL = "https://developer.android.com/tools/releases/platform-tools"


async def download_info():
    import httpx

    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.head(DOWNLOAD_URL)
        response.raise_for_status()
        size = int(response.headers.get("content-length", "0"))
        if not 1 <= size <= 100_000_000:
            raise ValueError("Could not verify the official download size. Open Google's download page.")
    return {
        "name": "platform-tools-latest-windows.zip",
        "bytes": size,
        "url": DOWNLOAD_URL,
        "license": LICENSE_URL,
    }


def executable():
    found = shutil.which("adb")
    candidates = [
        Path(os.getenv("LOCALAPPDATA", "")) / "PANINI/platform-tools/adb.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe",
    ]
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.getenv(key):
            candidates.append(Path(os.environ[key]) / "platform-tools/adb.exe")
    return found or next((str(p) for p in candidates if p.is_file()), None)


def endpoint(value):
    try:
        host, port = value.rsplit(":", 1)
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified:
            raise ValueError()
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError("Use the phone's local IPv4 address and debugging port.") from None
    return f"{address}:{int(port)}"


async def run(args, *, stdin=None, timeout=20):
    path = executable()
    if not path:
        raise ValueError("Install Android SDK Platform Tools, then retry.")
    process = await asyncio.create_subprocess_exec(
        path,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin), timeout)
        if process.returncode or len(out) > 1_000_000:
            raise ValueError("ADB setup failed. Check the debugging address and the phone's authorization.")
        return out.decode("utf-8", errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def pair(address, code):
    address = endpoint(address)
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        raise ValueError("Enter the six-digit wireless debugging pairing code.")
    # Code goes to stdin, never command lines, logs or the settings store.
    result = await run(["pair", address], stdin=(code + "\n").encode())
    if "Successfully paired" not in result:
        raise ValueError("Pairing failed. Refresh the phone's pairing code and retry.")
    return {"paired": True}


async def connect(address):
    result = await run(["connect", endpoint(address)])
    if "connected to" not in result:
        raise ValueError("Connection failed. Use the debugging port, which differs from the pairing port.")
    return {"connected": True}
