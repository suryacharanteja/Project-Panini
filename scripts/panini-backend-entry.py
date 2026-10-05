"""Entry point for the private, frozen backend sidecar."""

import os
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--check-local-stt"]:
        import asyncio
        import io
        import wave

        from backend import local_stt

        if not local_stt.readiness()["ready"]:
            raise RuntimeError("The packaged offline speech engine or model is missing.")
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0" * 32000)
        result = asyncio.run(local_stt.transcribe_audio_bytes(audio.getvalue()))
        if result["engine"] != "faster-whisper-local" or result["text"]:
            raise RuntimeError("Packaged offline speech smoke failed.")
        print("PANINI_LOCAL_STT_SMOKE_OK")
        raise SystemExit(0)
    if sys.argv[1:2] == ["panini-cli"]:
        # The frozen backend doubles as PANINI-CLI's runtime (panini-cli app-server --stdio).
        from panini_cli.agent.cli import main

        raise SystemExit(main(sys.argv[2:]))
    import uvicorn

    from backend.app import app

    if sys.argv[1:2] == ["--check-runtime"]:
        from backend.channels.telegram import Telegram  # noqa: F401
        from backend.fabric.passkeys import Passkeys  # noqa: F401
        from backend.fabric.transports.listener import PHONE
        from backend.harness.learning import persona
        from backend.harness.runtime import Harness  # noqa: F401

        if not persona() or not (PHONE / "lib/passkeys.mjs").is_file():
            raise RuntimeError("The packaged identity or phone application is missing.")
        for table in (
            "schema_migrations",
            "pages",
            "device_passkeys",
            "passkey_ceremonies",
            "channel_accounts",
            "channel_jobs",
            "channel_outbox",
            "channel_state",
            "outbound_settings",
            "outbound_briefs",
            "outbound_do_not_call",
            "outbound_reports",
            "outbound_reads",
            "mcp_clients",
        ):
            if not app.state.harness.store.one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ):
                raise RuntimeError(f"The packaged database is missing {table}.")
        from backend.telephony.numbers import calling_window
        from backend.telephony.schedule import CallSchedule

        if not calling_window("+442079460018", 1791100800)["timezones"]:
            raise RuntimeError("Packaged phone-number or timezone metadata is missing.")
        if not app.state.harness.store.one(
            "SELECT name FROM pragma_table_info('outbound_briefs') WHERE name='schedule'"
        ):
            raise RuntimeError("The packaged outbound schedule migration is missing.")
        if not CallSchedule(mode="next_window").resolve("+442079460018", 1791100800)["latest_start"]:
            raise RuntimeError("The packaged outbound scheduler is missing.")
        from backend.registry.registry import Registry

        registry = Registry()
        if registry.errors or not {"browser_v2", "sandbox", "live"}.issubset(
            {manifest.id for manifest in registry.manifests}
        ):
            raise RuntimeError(f"The packaged capability inventory is incomplete: {registry.errors}")
        print("PANINI_BACKEND_RUNTIME_IMPORTS_OK", flush=True)
        raise SystemExit(0)

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("BACKEND_PORT", "8200")), access_log=False)
