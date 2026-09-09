"""Command-line entry points.

``chat`` exists because the text adapter has full parity with voice (DD-035):
the entire pipeline — orchestrator, tool gateway, security kernel, audit — is
exercised without a microphone. It is the primary way to test the system.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from panini.app import Application, build_application
from panini.core.turn import Modality, Turn, TurnInput
from panini.errors import PaniniError, ProviderUnavailable

log = logging.getLogger("panini")

RESET, DIM, BOLD = "\x1b[0m", "\x1b[2m", "\x1b[1m"
CYAN, YELLOW, RED, GREEN = "\x1b[36m", "\x1b[33m", "\x1b[31m", "\x1b[32m"


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


# ── approval prompt ─────────────────────────────────────────────────────────

async def _approval_loop(app: Application) -> None:
    """Render approval requests at the terminal.

    Mirrors the dialog's rules (DD-134): Deny is the default, a bare Enter
    denies, and approving takes a deliberate keystroke.
    """
    async for event in app.bus.subscribe({"approval.requested"}, replay=False):
        p = event.payload
        colour = RED if p["risk"] == "high" else YELLOW

        print(f"\n{colour}{BOLD}── Approval required ──{RESET}")
        print(f"  {p['human_action']}")

        if p["taint_sources"]:
            print(f"\n  {YELLOW}⚠ This follows content read from:{RESET}")
            for src in p["taint_sources"]:
                print(f"      • {src}")

        if p["destination"]:
            mark = "" if p["destination_known"] else f"  {YELLOW}⚠ not a known destination{RESET}"
            print(f"\n  Sending to: {p['destination']}{mark}")

        if p["payload_preview"]:
            print(f"\n  {DIM}Payload:{RESET}")
            for line in str(p["payload_preview"]).splitlines()[:12]:
                print(f"    {DIM}{line}{RESET}")

        if p["escalated"]:
            print(f"\n  {DIM}Tier raised T{p['declared_tier']} → T{p['effective_tier']}"
                  f" ({p['reason']}){RESET}")

        answer = await asyncio.to_thread(
            input, f"\n  Approve? [y/{BOLD}N{RESET}] "
        )
        granted = answer.strip().lower() in ("y", "yes")
        app.approvals.resolve(p["invocation_id"], p["token"], granted)
        print(f"  {GREEN if granted else RED}{'approved' if granted else 'denied'}{RESET}\n")


async def _stream_output(app: Application) -> asyncio.Task[None]:
    """Print segments as they arrive."""

    async def pump() -> None:
        async for event in app.bus.subscribe(
            {"segment.emitted", "avatar.emotion", "error"}, replay=False
        ):
            if event.type == "segment.emitted":
                p = event.payload
                if p["content_type"] == "code":
                    lang = p["metadata"].get("language", "")
                    print(f"\n{DIM}```{lang}{RESET}\n{p['content']}\n{DIM}```{RESET}")
                else:
                    print(p["content"])
            elif event.type == "avatar.emotion":
                log.debug("emotion: %s", event.payload["emotion"])
            elif event.type == "error":
                print(f"{RED}error: {event.payload}{RESET}", file=sys.stderr)

    return asyncio.create_task(pump())


# ── commands ────────────────────────────────────────────────────────────────

async def cmd_chat(args: argparse.Namespace) -> int:
    app, warnings = await build_application(
        Path(args.config) if args.config else None,
        Path(args.data_dir) if args.data_dir else None,
    )
    for w in warnings:
        print(f"{YELLOW}config: {w}{RESET}", file=sys.stderr)

    try:
        orchestrator = app.require_orchestrator()
    except ProviderUnavailable as exc:
        # No fallback: report and exit rather than quietly using another
        # provider (FR-214).
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        print(f"{DIM}Start Ollama, or change llm.provider in your config.{RESET}",
              file=sys.stderr)
        await app.stop()
        return 1

    caps = app.provider.capabilities  # type: ignore[union-attr]
    locality = f"{GREEN}local{RESET}" if caps.is_local else f"{YELLOW}cloud{RESET}"
    print(f"{BOLD}Panini{RESET} — {caps.model} ({locality})")
    print(f"{DIM}Ctrl-C or 'exit' to quit.{RESET}\n")

    approvals = asyncio.create_task(_approval_loop(app))
    output = await _stream_output(app)
    session_id = "cli"

    try:
        while True:
            try:
                text = await asyncio.to_thread(input, f"{CYAN}you ▸ {RESET}")
            except EOFError:
                break
            if not text.strip():
                continue
            if text.strip().lower() in ("exit", "quit"):
                break

            turn = Turn(session_id=session_id, input=TurnInput(text, Modality.TEXT))

            # Memory is assembled before generation, not during: a recalled fact
            # of untrusted origin taints the turn, and that must be set before
            # the first tool call is considered (DD-121 M-2).
            assembled = await app.context.assemble(app.system_prompt, turn)

            print(f"{DIM}panini ▸{RESET}", end=" ", flush=True)
            try:
                await orchestrator.run(turn, assembled.messages)
            except PaniniError as exc:
                print(f"\n{RED}{exc}{RESET}")

            await app.history.append_turn(turn)

            if assembled.was_trimmed:
                print(f"{DIM}(earlier context trimmed: "
                      f"{assembled.turns_dropped} turns){RESET}")
            print()
    except KeyboardInterrupt:
        print()
    finally:
        for task in (approvals, output):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await app.stop()

    return 0


async def cmd_serve(args: argparse.Namespace) -> int:
    """Run the web UI.

    Preferred over ``chat`` for testing: a terminal reads input on one thread
    while output streams on another, so typing during a reply interleaves with
    it and corrupts both.
    """
    import uvicorn

    from panini.api.server import create_app

    app, warnings = await build_application(
        Path(args.config) if args.config else None,
        Path(args.data_dir) if args.data_dir else None,
    )
    for w in warnings:
        print(f"{YELLOW}config: {w}{RESET}", file=sys.stderr)

    if app.provider is None:
        print(f"{RED}{app.provider_error}{RESET}", file=sys.stderr)
        print(f"{DIM}The UI will start, but chat is unavailable until this is "
              f"fixed.{RESET}", file=sys.stderr)
    else:
        caps = app.provider.capabilities
        loc = f"{GREEN}local{RESET}" if caps.is_local else f"{YELLOW}cloud{RESET}"
        print(f"{BOLD}Panini{RESET} — {caps.model} ({loc})")

    url = f"http://{args.host}:{args.port}"
    print(f"{BOLD}Open{RESET} {CYAN}{url}{RESET}")
    print(f"{DIM}Ctrl-C to stop.{RESET}\n")

    config = uvicorn.Config(
        create_app(app), host=args.host, port=args.port,
        log_level="warning", access_log=False,
    )
    await uvicorn.Server(config).serve()
    return 0


async def cmd_health(args: argparse.Namespace) -> int:
    app, warnings = await build_application(
        Path(args.config) if args.config else None,
        Path(args.data_dir) if args.data_dir else None,
    )
    for w in warnings:
        print(f"{YELLOW}config: {w}{RESET}")

    health = app.health()
    provider = health["provider"]  # type: ignore[index]

    status = f"{GREEN}ready{RESET}" if provider["ready"] else f"{RED}unavailable{RESET}"
    print(f"{BOLD}Provider{RESET}  {provider['name']} — {status}")
    if provider["ready"]:
        loc = "local" if provider["is_local"] else "cloud"
        print(f"          model: {provider['model']} ({loc})")
    else:
        print(f"          {DIM}{provider['error']}{RESET}")

    print(f"\n{BOLD}Tools{RESET}")
    for name, enabled in sorted(health["tools"].items()):  # type: ignore[union-attr]
        mark = f"{GREEN}on {RESET}" if enabled else f"{DIM}off{RESET}"
        print(f"  {mark} {name}")

    print(f"\n{BOLD}Audit{RESET}     {health['audit']['path']}")  # type: ignore[index]

    await app.stop()
    return 0 if provider["ready"] else 1


async def cmd_audit(args: argparse.Namespace) -> int:
    app, _ = await build_application(
        Path(args.config) if args.config else None,
        Path(args.data_dir) if args.data_dir else None,
    )
    entries = await app.audit.query(limit=args.limit)
    if not entries:
        print(f"{DIM}No audit entries.{RESET}")
    for e in reversed(entries):
        flags = []
        if e.tainted:
            flags.append("tainted")
        if e.was_escalated:
            flags.append(f"T{e.declared_tier}→T{e.effective_tier}")
        if e.is_egress:
            flags.append("egress")
        suffix = f" {DIM}[{', '.join(flags)}]{RESET}" if flags else ""
        colour = GREEN if e.outcome == "ok" else RED
        print(f"{DIM}{e.ts_begin[:19]}{RESET} {colour}{e.outcome:<7}{RESET} "
              f"{e.tool_name}{suffix}")
    await app.stop()
    return 0


# ── entry point ─────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="panini", description="Local-first AI assistant")
    parser.add_argument("--config", help="path to panini.yaml")
    parser.add_argument("--data-dir", help="override the data directory")
    parser.add_argument("--log-level", default="WARNING")

    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web UI (recommended)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8760)

    sub.add_parser("chat", help="interactive text session (terminal)")
    sub.add_parser("health", help="report provider and tool status")
    audit = sub.add_parser("audit", help="show recent tool invocations")
    audit.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    _setup_logging(args.log_level)

    handlers = {"serve": cmd_serve, "chat": cmd_chat,
                "health": cmd_health, "audit": cmd_audit}
    try:
        return asyncio.run(handlers[args.command](args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
