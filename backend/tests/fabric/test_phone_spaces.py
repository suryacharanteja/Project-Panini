"""Phone notebooks use authenticated owner routes, CAS and bounded binary exports."""

import asyncio
import base64

from fastapi.responses import Response
from fastapi.testclient import TestClient

from backend.fabric.server import MAX_BODY
from backend.spaces import Spaces

from .conftest import paired
from .test_decisions import Phone
from .test_decisions import world as world


def test_phone_notebook_auth_revision_checks_and_no_owner_destroy(world, monkeypatch):
    monkeypatch.setenv("PANINI_SPACES", "true")
    app, devices, identity, _ = world
    app.state.harness.spaces = Spaces(devices.store)
    app.state.fabric.install()
    assert TestClient(app).get("/api/remote/spaces").status_code == 403

    async def scenario():
        phone = Phone(app, identity, paired(devices))
        await phone.connect()
        space = (await phone.call("POST", "/spaces", {"name": "Phone notes"}))["body"]
        page = (await phone.call("POST", "/spaces/pages", {"space_id": space["id"], "title": "Plan"}))["body"]
        changed = await phone.call(
            "PATCH", f"/spaces/pages/{page['id']}", {"expected_revision": 1, "content": "Saved"}
        )
        assert changed["status"] == 200 and changed["body"]["revision"] == 2
        conflict = await phone.call(
            "PATCH", f"/spaces/pages/{page['id']}", {"expected_revision": 1, "content": "Stale"}
        )
        assert conflict["status"] == 409
        assert (await phone.call("GET", f"/spaces/pages/{page['id']}"))["body"]["content"] == "Saved"
        assert (
            await phone.call(
                "POST",
                f"/spaces/pages/{page['id']}/destroy-request",
                {"expected_revision": 2, "confirmation": "Plan"},
            )
        )["status"] == 404
        monkeypatch.setenv("PANINI_SPACES", "false")
        assert (await phone.call("GET", "/spaces"))["status"] == 404

    asyncio.run(scenario())


def test_fabric_pdf_binary_encoding_and_explicit_size_refusal(world):
    app, devices, identity, _ = world

    @app.get("/api/remote/test-pdf")
    async def pdf(large: bool = False):
        return Response(
            b"%PDF-" + (b"x" * (MAX_BODY // 2) if large else b"fixture"), media_type="application/pdf"
        )

    async def scenario():
        phone = Phone(app, identity, paired(devices))
        await phone.connect()
        result = await phone.call("GET", "/test-pdf")
        assert result["body"]["content_type"] == "application/pdf"
        assert base64.b64decode(result["body"]["base64"]) == b"%PDF-fixture"
        assert (await phone.call("GET", "/test-pdf?large=true"))["status"] == 413

    asyncio.run(scenario())
