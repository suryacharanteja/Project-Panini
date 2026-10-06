"""Verify owned process cleanup and real broker cancellation without provider calls."""

import asyncio
import ctypes
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def executable(name):
    return str(
        ROOT / "panini-core/capabilities/desktop" / name / "bin/Release/net10.0-windows" / f"{name}.exe"
    )


def gone(pid):
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return True
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


async def main():
    from backend.capabilities.desktop import DesktopPipe
    from backend.panini.contracts import Fault

    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], creationflags=subprocess.CREATE_NO_WINDOW
    )
    program = "import subprocess,sys,os,time,json; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(json.dumps([os.getpid(),child.pid]),flush=True); time.sleep(60)"
    host = subprocess.Popen(
        [executable("Panini.RuntimeHost"), sys.executable, "-u", "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        pids = json.loads(await asyncio.wait_for(asyncio.to_thread(host.stdout.readline), 5))
        host.stdin.close()
        await asyncio.to_thread(host.wait, 5)
        await asyncio.sleep(0.3)
        assert all(gone(pid) for pid in pids), "Owned descendants survived supervisor EOF"
        assert sentinel.poll() is None, "An unrelated process was terminated"
    finally:
        if host.poll() is None:
            host.kill()
        sentinel.terminate()
        sentinel.wait(timeout=3)
    os.environ["PANINI_DESKTOP_PIPE"] = "panini-cancel-test-" + str(uuid4())
    os.environ["PANINI_DESKTOP_SECRET"] = secrets.token_urlsafe(32)
    # The fixture intentionally never writes a worker response. It models a hung UIA provider.
    os.environ["PANINI_DESKTOP_WORKER"] = executable("Panini.Desktop.Fixture")
    broker = subprocess.Popen(
        [executable("Panini.Desktop.Broker")],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import ctypes

        # WaitNamedPipe fails at once while the pipe does not exist yet: wait until the broker listens.
        for _ in range(100):
            if ctypes.windll.kernel32.WaitNamedPipeW(
                "\\\\.\\pipe\\" + os.environ["PANINI_DESKTOP_PIPE"], 100
            ):
                break
            await asyncio.sleep(0.1)
        pipe = DesktopPipe()
        task_id = str(uuid4())
        pending = asyncio.create_task(pipe.call({"op": "observe", "task_id": task_id}))
        await asyncio.sleep(0.4)
        started = time.monotonic()
        stopped = await pipe.call({"op": "stop", "task_id": task_id})
        assert stopped["stopped"] and time.monotonic() - started < 3
        try:
            await pending
            raise AssertionError("Hung worker reported success")
        except Fault as exc:
            assert exc.code == "UNKNOWN_EFFECT", (exc.code, exc.message)
        try:
            await pipe.call({"op": "action", "task_id": task_id})
            raise AssertionError("A fenced task restarted a worker")
        except Fault as exc:
            assert exc.code == "UNKNOWN_EFFECT", (exc.code, exc.message)
        wrong = DesktopPipe()
        wrong.secret = secrets.token_urlsafe(32)
        try:
            await wrong.call({"op": "health"})
            raise AssertionError("Wrong pipe credential was accepted")
        except (Fault, OSError):
            pass
    finally:
        broker.terminate()
        broker.wait(timeout=3)
    print(
        json.dumps(
            {
                "passed": [
                    "supervisor EOF kills owned process tree",
                    "unrelated process survives",
                    "Stop interrupts hung worker",
                    "cancelled task cannot dispatch later",
                    "wrong IPC credential rejected",
                ]
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
