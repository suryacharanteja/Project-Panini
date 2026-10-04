"""Serialized, cancellable Node transport. Failed effects are never replayed automatically."""

import asyncio
import json
import os
from pathlib import Path

from . import adb
from .playwright_mcp import node_executable


class AndroidDriver:
    def __init__(self):
        self.process = None
        self.lock = asyncio.Lock()

    async def call(self, op, **arguments):
        async with self.lock:
            if self.process is None or self.process.returncode is not None:
                from ..config import ROOT

                node, path = node_executable(), adb.executable()
                script = ROOT / "panini-core/capabilities/android/sidecar/android.mjs"
                if not node or not path or not script.is_file():
                    raise ValueError("Install Node.js and Android SDK Platform Tools.")
                await adb.run(["start-server"])
                env = {**os.environ, "PATH": str(Path(path).parent) + os.pathsep + os.environ.get("PATH", "")}
                drivers = ROOT / "panini-core/capabilities/android/drivers"
                if drivers.is_dir():
                    env["PLAYWRIGHT_BROWSERS_PATH"] = str(drivers)
                self.process = await asyncio.create_subprocess_exec(
                    node,
                    str(script),
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=4_000_000,
                )
            try:
                self.process.stdin.write((json.dumps({"op": op, **arguments}) + "\n").encode())
                await self.process.stdin.drain()
                line = await asyncio.wait_for(self.process.stdout.readline(), 25)
                result = json.loads(line)
                if result.get("ok") is not True:
                    raise ValueError(result.get("error", "Android driver failed."))
                return result["result"]
            except BaseException:
                await self.close()
                raise

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        self.process = None
