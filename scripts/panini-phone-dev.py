"""Developer preview of the phone app (V2-15) on this PC only. Not the product path.

It runs the real fabric (pairing, sessions, remote API, real interrupts) against a throwaway database, on loopback
only, outside PANINI_REMOTE_MODE (which stays off until the V2-13 red-team gate passes). Open the printed pairing
link in a phone-sized browser; the control port confirms pairing and opens sample approvals:

    POST http://127.0.0.1:3212/confirm         confirm the newest pending device (as if on the PC)
    POST http://127.0.0.1:3212/interrupt       open a sample approval (the phone gets it in its Inbox)
    GET  http://127.0.0.1:3212/state           devices, interrupts, asks and shares so far

Run: .venv\\Scripts\\python scripts\\panini-phone-dev.py
"""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--phone-port", type=int, default=3211)
parser.add_argument("--control-port", type=int, default=3212)
args = parser.parse_args()
PHONE_PORT, CONTROL_PORT = args.phone_port, args.control_port
if not (1024 <= PHONE_PORT <= 65535 and 1024 <= CONTROL_PORT <= 65535) or PHONE_PORT == CONTROL_PORT:
    parser.error("Use two distinct ports between 1024 and 65535.")


class MemorySecrets(dict):
    def set(self, key, value):
        self[key] = value


async def main():
    import os

    os.environ.setdefault(
        "PANINI_LIVE_VIEW", "true"
    )  # The preview shows the Live screen with a sample frame.
    import uvicorn
    from fastapi import FastAPI

    from backend import live, policy
    from backend.fabric.router import router as remote_router
    from backend.fabric.runtime import Fabric
    from backend.fabric.transports.listener import build
    from backend.harness.store import Store
    from backend.interrupts import Interrupts, InterruptSpec
    from backend.routes import live as live_routes

    store = Store(Path(tempfile.mkdtemp()) / "phone-dev.sqlite3")
    interrupts = Interrupts(store)
    from backend.harness.tasks import Tasks

    tasks = Tasks(store)
    asks = []

    def submit(goal, **kw):
        asks.append({"goal": goal, **{k: v for k, v in kw.items() if k in ("origin", "title")}})
        return tasks.submit(goal, **kw)

    app = FastAPI()
    app.include_router(remote_router)
    app.include_router(live_routes.remote_router)
    app.state.harness = SimpleNamespace(
        interrupts=interrupts,
        status=lambda: {"running": 0, "queued": len(asks)},
        notices=SimpleNamespace(
            list=lambda: [{"title": "Welcome", "body": "This is the phone app preview."}]
        ),
        tasks=SimpleNamespace(
            submit=submit, list=tasks.list, get=tasks.get, events=tasks.events, cancel=tasks.cancel
        ),
    )
    policy.install(policy.Policy())
    fabric = Fabric(app, store, MemorySecrets(), settings_path=Path(tempfile.mkdtemp()) / "fabric.json")
    live.hub().publish(None, "sandbox", "panini", (ROOT / "panini-core/apps/phone/icons/icon-512.png").read_bytes(),
                       {"url": "https://example.com/", "title": "Sample frame"})  # fmt: skip

    control = FastAPI()

    @control.post("/confirm")
    async def confirm():
        pending = [d for d in fabric.devices.list() if d["status"] == "pending"]
        if not pending:
            return {"confirmed": None}
        return {"confirmed": fabric.devices.confirm(pending[-1]["id"])["name"]}

    @control.post("/revoke")
    async def revoke():
        for device in fabric.devices.list():
            if device["status"] == "active":
                fabric.server.revoke(device["id"])
        return {"revoked": True}

    @control.post("/interrupt")
    async def sample():
        card = interrupts.open(
            InterruptSpec("approval", "tool_call", "Send the weekly report to Asha",
                          "PANINI wants to send report.pdf to asha@example.com.",
                          preview={"to": "asha@example.com", "attachment": "report.pdf"},
                          action={"tool": "email_send", "to": "asha@example.com"}, level="tap", ttl_s=3600),
            run_id="dev",
        )  # fmt: skip
        return {"id": card["id"]}

    @control.get("/state")
    async def state():
        return {"devices": fabric.devices.list(), "interrupts": interrupts.list(status=None), "asks": asks}

    code = fabric.devices.new_code()
    origin = f"http://127.0.0.1:{PHONE_PORT}"
    print("Pairing link (valid 5 minutes):", fabric.devices.qr_url(origin, code), flush=True)
    phone = uvicorn.Server(
        uvicorn.Config(build(fabric, "lan"), host="127.0.0.1", port=PHONE_PORT, log_level="warning")
    )
    admin = uvicorn.Server(uvicorn.Config(control, host="127.0.0.1", port=CONTROL_PORT, log_level="warning"))
    await asyncio.gather(phone.serve(), admin.serve())


if __name__ == "__main__":
    asyncio.run(main())
