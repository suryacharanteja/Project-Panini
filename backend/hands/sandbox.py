"""Sandbox computers (V2-11): PANINI's own isolated, persistent computers on Docker (OpenBot supervisor).

Python port of OpenDots `src/server/computer-service.ts` and `src/shared/computer-types.ts` (MIT), with PANINI's
ledger, policy pause and host file rules. One rule set, carried over one to one:

- each computer is reached with its own credential, HMAC-SHA256(master, "panini-computer:" + id); the master and
  the supervisor token never reach a computer;
- the supervisor must report the expected identity (`<namespace>-computer-<id>`) and an endpoint bound to that
  computer (a loopback port, or the container's own name on port 4100); anything else is refused;
- status never provisions; agents never get human controls; permissions are checked before, during (a 50 ms
  watcher cancels the request) and after every action; responses are capped at 4 MB and any reflected secret
  is redacted; HTTP 409 tells the agent to take a fresh snapshot and never to bypass a take-over;
- `exec` results never echo the command.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets as secrets_module
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .. import live
from ..core_contracts import Readiness, ToolResult

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PREFIX = "panini-computer:"
DEFAULT_COMPUTER = "panini"
DEFAULT_PERMISSIONS = {"enabled": False, "browser": False, "files": False, "shell": False}
RESPONSE_CAP = 4_000_000
CONFLICT = (
    "The sandbox browser changed or the owner has control (HTTP 409). Take sandbox_snapshot again before "
    "retrying; if the owner has control, wait for them to release it. Do not bypass a take-over."
)
MAX_IMPORT_BYTES = 100_000


class SandboxError(RuntimeError):
    pass


# Inputs (port of computer-types.ts) --------------------------------------------------------------
def _path(value, *, required) -> str:
    text = "" if value is None else str(value)
    if len(text) > 1024 or text.startswith("/") or "\\" in text or "\0" in text or ".." in text.split("/"):
        raise SandboxError("Use a relative workspace path without traversal.")
    if required and not text:
        raise SandboxError("A path is required.")
    return text


def _ref(args):
    ref, snapshot = args.get("ref"), args.get("snapshotId")
    if not isinstance(ref, str) or not 1 <= len(ref) <= 100:
        raise SandboxError("ref must come from the latest sandbox_snapshot.")
    if not isinstance(snapshot, int) or isinstance(snapshot, bool) or snapshot < 0:
        raise SandboxError("snapshotId must come from the latest sandbox_snapshot.")
    return {"ref": ref, "snapshotId": snapshot}


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise SandboxError(f"{name} must be between {low} and {high}.")
    return value


def _exact(args, allowed):
    extra = set(args) - set(allowed)
    if extra:
        raise SandboxError(f"Unexpected input: {', '.join(sorted(extra))}.")


def validate(action: str, args: dict) -> dict:
    args = dict(args or {})
    if action == "navigate":
        _exact(args, {"url"})
        url = str(args.get("url", ""))
        parts = urlsplit(url)
        if (
            len(url) > 2048
            or parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            raise SandboxError("Only http(s) addresses without credentials can be opened.")
        return {"url": url}
    if action in ("read", "snapshot", "screenshot"):
        _exact(args, set())
        return {}
    if action == "click":
        _exact(args, {"ref", "snapshotId"})
        return _ref(args)
    if action == "type":
        _exact(args, {"ref", "snapshotId", "text", "submit"})
        text = args.get("text", "")
        if not isinstance(text, str) or len(text) > 16000:
            raise SandboxError("text must be at most 16,000 characters.")
        out = {**_ref(args), "text": text}
        if "submit" in args:
            out["submit"] = bool(args["submit"])
        return out
    if action in ("key", "human_key"):
        _exact(args, {"key"})
        key = args.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise SandboxError("key must be 1-100 characters.")
        return {"key": key}
    if action in ("scroll", "human_scroll"):
        _exact(args, {"deltaY"})
        return {"deltaY": _number(args.get("deltaY"), -10000, 10000, "deltaY")}
    if action == "files_list":
        _exact(args, {"path"})
        return {"path": _path(args.get("path", ""), required=False)}
    if action == "files_read":
        _exact(args, {"path"})
        return {"path": _path(args.get("path"), required=True)}
    if action == "files_write":
        _exact(args, {"path", "contents", "append"})
        contents = args.get("contents", "")
        if not isinstance(contents, str) or len(contents) > 100000:
            raise SandboxError("contents must be at most 100,000 characters.")
        out = {"path": _path(args.get("path"), required=True), "contents": contents}
        if "append" in args:
            out["append"] = bool(args["append"])
        return out
    if action == "exec":
        _exact(args, {"command", "timeoutMs"})
        command = str(args.get("command", "")).strip()
        if not 1 <= len(command) <= 8000:
            raise SandboxError("command must be 1-8,000 characters.")
        timeout = args.get("timeoutMs", 30000)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1000 <= timeout <= 60000:
            raise SandboxError("timeoutMs must be 1000-60000.")
        return {"command": command, "timeoutMs": timeout}
    if action == "human_click":
        _exact(args, {"x", "y"})
        return {"x": _number(args.get("x"), 0, 16000, "x"), "y": _number(args.get("y"), 0, 16000, "y")}
    if action == "human_type":
        _exact(args, {"text"})
        text = args.get("text", "")
        if not isinstance(text, str) or len(text) > 16000:
            raise SandboxError("text must be at most 16,000 characters.")
        return {"text": text}
    raise SandboxError("Unknown computer action.")


ACTIONS = {
    "navigate", "read", "snapshot", "screenshot", "click", "type", "key", "scroll",
    "files_list", "files_read", "files_write", "exec",
    "human_click", "human_type", "human_key", "human_scroll",
}  # fmt: skip


def kind_of(action: str) -> str:
    return "shell" if action == "exec" else "files" if action.startswith("files_") else "browser"


# Configuration ----------------------------------------------------------------------------------
class SandboxConfig:
    """Supervisor address and namespace from settings; the two tokens from the DPAPI secret store."""

    def __init__(self, secrets=None):
        self._secrets = secrets

    @property
    def secrets(self):
        if self._secrets is None:
            from ..extension_pairing import data_directory
            from ..harness.secrets_store import SecretStore

            self._secrets = SecretStore(data_directory() / "sandbox-secrets.bin")
        return self._secrets

    @property
    def supervisor_url(self) -> str:
        return os.getenv("PANINI_SANDBOX_SUPERVISOR_URL", "http://127.0.0.1:4312").strip()

    @property
    def namespace(self) -> str:
        return os.getenv("PANINI_SANDBOX_NAMESPACE", "panini").strip()

    @property
    def supervisor_token(self) -> str:
        return (
            os.getenv("PANINI_SANDBOX_SUPERVISOR_TOKEN") or self.secrets.get("supervisor_token") or ""
        ).strip()

    @property
    def computer_token(self) -> str:
        return (
            os.getenv("PANINI_SANDBOX_COMPUTER_TOKEN") or self.secrets.get("computer_token") or ""
        ).strip()

    def generate(self) -> None:
        """First-time setup: random tokens, kept only in the secret store."""
        if not self.secrets.get("supervisor_token"):
            self.secrets.set("supervisor_token", secrets_module.token_urlsafe(32))
        if not self.secrets.get("computer_token"):
            self.secrets.set("computer_token", secrets_module.token_urlsafe(32))


# Service -------------------------------------------------------------------------------------------
class SandboxService:
    def __init__(
        self,
        store,
        *,
        config=None,
        transport=None,
        deadline_s=70.0,
        paused=None,
        clock=time.time,
        runtime=None,
        native=None,
        native_browser=None,
    ):
        self.store, self.clock = store, clock
        self.config = config or SandboxConfig()
        self.transport = transport
        self.deadline_s = deadline_s
        self._paused = paused
        self._runtime = runtime
        self._native = native
        self._native_browser = native_browser

    # Native mode (ADR-0001 follow-up): with no container engine, shell and files run in a Windows AppContainer.
    @property
    def native(self):
        """The native sandbox when PANINI_SANDBOX_ENGINE=native, or when auto finds no engine; else None."""
        if self._native is not None:
            return self._native or None
        from . import native_sandbox
        from .sandbox_runtime import detect

        choice = os.getenv("PANINI_SANDBOX_ENGINE", "auto").strip().lower()
        if native_sandbox.available() and (choice == "native" or (choice == "auto" and not detect())):
            self._native = native_sandbox.NativeSandbox()
        else:
            self._native = False
        return self._native or None

    async def _native_action(self, computer_id, action, parsed, stop=None):
        box = self.native
        if action == "exec":
            return await asyncio.to_thread(
                box.run,
                computer_id,
                parsed["command"],
                timeout_s=parsed["timeoutMs"] / 1000,
                cancel=stop,
            )
        if action == "files_list":
            return await asyncio.to_thread(box.files_list, computer_id, parsed["path"])
        if action == "files_read":
            return await asyncio.to_thread(box.files_read, computer_id, parsed["path"])
        if action == "files_write":
            return await asyncio.to_thread(
                box.files_write, computer_id, parsed["path"], parsed["contents"], parsed.get("append", False)
            )
        return await self.native_browser.action(computer_id, action, parsed)

    async def live_frame(self, computer_id: str):
        """Live view source for container computers (1 frame / 2 s while watched). Native computers stream
        through their browser's screencast instead, so this returns None for them."""
        if self.native:
            return None
        shot = await self.action(computer_id, "screenshot", {}, "owner")
        if not isinstance(shot, dict) or not shot.get("base64"):
            return None
        meta = {"url": shot.get("url", ""), "width": shot.get("width", 0), "height": shot.get("height", 0)}
        return base64.b64decode(shot["base64"]), meta

    async def live_input(self, computer_id: str, action: str, args: dict) -> dict:
        """Owner input from the live view while the owner holds the computer (click, type, key, scroll)."""
        result = await self.action(computer_id, f"human_{action}", args, "owner")
        return result if isinstance(result, dict) else {"result": result}

    def register_live(self) -> None:
        from .. import live
        from ..routes import live as live_routes

        live.hub().register_poller("sandbox", self.live_frame, 2.0)

        async def follow(hand, target, view):
            # Take / give back from the live view also moves the computer's own control (OpenBot's API).
            if hand == "sandbox" and not self.native:
                try:
                    await self.control(target, "take" if view["holder"] == "human" else "release")
                except SandboxError:
                    pass

        live.takeover().listen(follow)
        live_routes.input_handlers["sandbox"] = self.live_input

    async def frame(self, computer_id: str) -> bytes:
        """The owner's live view of a native computer's browser (PNG)."""
        self.require(computer_id)
        if not self.native:
            raise SandboxError("Live frames for container computers come with V2-12.")
        self.allowed(computer_id, "browser", "owner")
        from .native_browser import NativeBrowserError

        try:
            return await self.native_browser.frame(computer_id)
        except NativeBrowserError as exc:
            raise SandboxError(str(exc)) from None

    async def _native_guarded(self, computer_id, action, parsed, kind, actor, cancel):
        """Runs a native action, ending it if its permission is revoked, the owner pauses, or the run cancels."""
        from .native_browser import NativeBrowserError
        from .native_sandbox import NativeSandboxError

        stop = threading.Event()
        work = asyncio.ensure_future(self._native_action(computer_id, action, parsed, stop))
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=0.05)
                if work.done():
                    break
                try:
                    self.allowed(computer_id, kind, actor)
                except SandboxError:
                    raise
                if cancel is not None and cancel.is_set():
                    raise SandboxError("Computer request was cancelled or timed out.")
            result = work.result()
        except (NativeSandboxError, NativeBrowserError) as exc:
            raise SandboxError(str(exc)) from None
        finally:
            stop.set()
            if action not in {"exec", "files_list", "files_read", "files_write"}:
                work.cancel()
            # Cancelling to_thread alone leaves its OS process alive. Exec polls stop and
            # terminates its entire job; bounded file operations must finish before release.
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue  # Repeated revoke signals cannot abandon the OS worker.
                except Exception:
                    break
            await asyncio.gather(work, return_exceptions=True)
        self.allowed(computer_id, kind, actor)
        return result

    @property
    def native_browser(self):
        if self._native_browser is None:
            from .native_browser import NativeBrowser

            self._native_browser = NativeBrowser(self.native.workspace)
        return self._native_browser

    @property
    def runtime(self):
        """The engine, images and supervisor behind the sandbox, started on demand (ADR-0001)."""
        if self._runtime is None:
            from .sandbox_runtime import SandboxRuntime

            self._runtime = SandboxRuntime(self.config)
        return self._runtime

    async def ready(self, consent=None) -> None:
        from .sandbox_runtime import RuntimeUnavailable

        try:
            await self.runtime.ensure_ready(consent)
        except RuntimeUnavailable as exc:
            raise SandboxError(str(exc)) from None

    # Computers and permissions ----------------------------------------------------------------
    def ensure_row(self, computer_id: str) -> None:
        if not NAME.match(computer_id or ""):
            raise SandboxError("Computer not found.")
        self.store.execute(
            "INSERT OR IGNORE INTO sandbox_computers(id, persona_id, created, permissions) VALUES (?,?,?,?)",
            (computer_id, computer_id, self.clock(), json.dumps(DEFAULT_PERMISSIONS)),
        )

    def require(self, computer_id: str) -> None:
        if (
            not NAME.match(computer_id or "")
            or self.store.one("SELECT id FROM sandbox_computers WHERE id=?", (computer_id,)) is None
        ):
            raise SandboxError("Computer not found.")

    def computers(self) -> list[dict]:
        return [dict(r) for r in self.store.all("SELECT * FROM sandbox_computers ORDER BY created")]

    def permissions(self, computer_id: str) -> dict:
        row = self.store.one("SELECT permissions FROM sandbox_computers WHERE id=?", (computer_id,))
        return {**DEFAULT_PERMISSIONS, **(json.loads(row["permissions"]) if row else {})}

    def patch_permissions(self, computer_id: str, patch: dict) -> dict:
        self.require(computer_id)
        unknown = set(patch) - set(DEFAULT_PERMISSIONS)
        if unknown or not all(isinstance(v, bool) for v in patch.values()):
            raise SandboxError("Permissions are enabled, browser, files and shell (true or false).")
        value = {**self.permissions(computer_id), **patch}
        self.store.execute(
            "UPDATE sandbox_computers SET permissions=? WHERE id=?", (json.dumps(value), computer_id)
        )
        return value

    @property
    def configured(self) -> bool:
        if self.native:
            return True
        return bool(
            self.config.supervisor_url and self.config.supervisor_token and self.config.computer_token
        )

    def token(self, computer_id: str) -> str:
        return hmac.new(
            self.config.computer_token.encode(), (PREFIX + computer_id).encode(), hashlib.sha256
        ).hexdigest()

    def paused(self) -> bool:
        if self._paused is not None:
            return self._paused()
        from .. import policy

        current = policy.current() if hasattr(policy, "current") else None
        try:
            return bool(current and current.paused())
        except Exception:  # noqa: BLE001
            return False

    def allowed(self, computer_id: str, kind: str | None, actor: str) -> None:
        self.require(computer_id)
        if not self.configured:
            raise SandboxError("The sandbox is not set up. Set it up in PANINI Admin → Computers.")
        policy = self.permissions(computer_id)
        if not policy["enabled"] or (kind and not policy[kind]):
            raise SandboxError("Computer permission is disabled.")
        if actor == "agent" and self.paused():
            raise SandboxError("Agents are paused.")

    # HTTP -------------------------------------------------------------------------------------------
    async def _json(self, url, token, body=None, computer_id=None, cancel: asyncio.Event | None = None):
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        if computer_id:
            headers["x-openbot-bot-id"] = computer_id

        async def request():
            async with httpx.AsyncClient(
                transport=self.transport, follow_redirects=False, timeout=self.deadline_s
            ) as client:
                method = "GET" if body is None else "POST"
                async with client.stream(method, url, headers=headers, json=body) as response:
                    if response.status_code == 409:
                        raise SandboxError(CONFLICT)
                    if response.is_redirect or not response.is_success:
                        raise SandboxError(f"Computer service returned HTTP {response.status_code}.")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > RESPONSE_CAP:
                            raise SandboxError("Computer response exceeded its size limit.")
                        chunks.append(chunk)
            text = b"".join(chunks).decode("utf-8", errors="replace")
            for secret in (token, self.config.computer_token, self.config.supervisor_token):
                if secret:
                    text = text.replace(secret, "[redacted]")
            return json.loads(text) if text.strip() else {}

        task = asyncio.ensure_future(request())
        waiters = {task}
        cancelled = None
        if cancel is not None:
            cancelled = asyncio.ensure_future(cancel.wait())
            waiters.add(cancelled)
        try:
            done, _ = await asyncio.wait(
                waiters, timeout=self.deadline_s, return_when=asyncio.FIRST_COMPLETED
            )
            if task not in done:
                task.cancel()
                raise SandboxError("Computer request was cancelled or timed out.")
            return task.result()
        except SandboxError:
            raise
        except (httpx.HTTPError, ValueError, OSError) as exc:
            raise SandboxError("Computer service is unavailable or returned an invalid response.") from exc
        finally:
            if cancelled is not None:
                cancelled.cancel()
            if not task.done():
                task.cancel()

    async def _supervisor(self, path, body=None, cancel=None):
        return await self._json(
            self.config.supervisor_url.rstrip("/") + path, self.config.supervisor_token, body, cancel=cancel
        )

    def endpoint(self, computer_id: str, raw) -> str:
        if not isinstance(raw, dict):
            raise SandboxError("Computer identity mismatch.")
        namespace = self.config.namespace
        if not NAME.match(namespace):
            raise SandboxError("Invalid computer namespace.")
        expected = f"{namespace}-computer-{computer_id}"
        if raw.get("botId") != computer_id or raw.get("container") != expected:
            raise SandboxError("Computer identity mismatch.")
        port = raw.get("port")
        if port is not None and (
            isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535
        ):
            raise SandboxError("Computer identity mismatch.")
        url = raw.get("url") or (f"http://127.0.0.1:{port}" if port else f"http://{expected}:4100")
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        try:
            actual_port = parts.port
        except ValueError:
            actual_port = None
        network = host == expected.lower() and actual_port == 4100
        local = (
            host == "127.0.0.1"
            and bool(port)
            and actual_port == port
            and urlsplit(self.config.supervisor_url).hostname == "127.0.0.1"
        )
        if (
            parts.scheme != "http"
            or parts.username
            or parts.password
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or not (network or local)
        ):
            raise SandboxError("Computer endpoint is not bound to this computer.")
        return f"http://{host}:{actual_port}"

    async def _existing(self, computer_id, cancel=None):
        listing = await self._supervisor("/computers", cancel=cancel)
        computers = listing.get("computers") if isinstance(listing, dict) else None
        if not isinstance(computers, list):
            raise SandboxError("Computer service is unavailable or returned an invalid response.")
        return next((c for c in computers if isinstance(c, dict) and c.get("botId") == computer_id), None)

    async def _running(self, computer_id, cancel=None) -> str:
        state = await self._existing(computer_id, cancel)
        if not state or state.get("status") != "running":
            raise SandboxError("Start this computer first (sandbox_start).")
        return self.endpoint(computer_id, state)

    # Operations ------------------------------------------------------------------------------------
    async def status(self, computer_id: str) -> dict:
        self.require(computer_id)
        base = {
            "computer": computer_id,
            "configured": self.configured,
            "permissions": self.permissions(computer_id),
        }
        try:
            base["runtime"] = self.runtime.describe()
        except Exception:  # noqa: BLE001 -- Describing the runtime never breaks status.
            pass
        if self.native:
            return {
                **base,
                "state": "running",
                "engine": "native",
                "tools": ["shell", "files", "browser"],
                "control": live.takeover().state("sandbox", computer_id).view(),
                "browserOpen": computer_id in self.native_browser.sessions,
                "browserIsolation": getattr(self.native_browser, "isolation", "none"),
            }
        if not self.configured:
            return {**base, "state": "not_configured"}
        try:
            state = await self._existing(computer_id)
            if not state or state.get("status") != "running":
                self._seen(computer_id, "stopped")
                return {**base, "state": "stopped"}
            url = self.endpoint(computer_id, state)
            control = await self._json(f"{url}/control", self.token(computer_id), computer_id=computer_id)
            self._seen(computer_id, "running")
            return {**base, "state": "running", "control": control}
        except SandboxError:
            return {
                **base,
                "state": "unavailable",
                "error": "The sandbox service is unavailable. Check Docker Desktop and the PANINI sandbox service.",
            }

    def _seen(self, computer_id, state):
        self.store.execute(
            "UPDATE sandbox_computers SET last_state=?, last_seen=? WHERE id=?",
            (state, self.clock(), computer_id),
        )

    async def start(self, computer_id: str, actor="owner", consent=None) -> dict:
        self.allowed(computer_id, None, actor)
        if self.native:
            return await self.status(computer_id)
        try:
            ensured = await self._supervisor(f"/computers/{computer_id}/ensure", {})
        except SandboxError as exc:
            if "unavailable" not in str(exc):
                raise
            # The engine or supervisor is down: PANINI starts them itself (asking once), then tries again.
            await self.ready(consent if actor == "agent" else _owner_consent)
            ensured = await self._supervisor(f"/computers/{computer_id}/ensure", {})
        self.endpoint(computer_id, ensured)
        return await self.status(computer_id)

    async def stop(self, computer_id: str) -> dict:
        self.require(computer_id)
        if self.native:
            # Commands end with themselves; the computer's throwaway browser is closed with its profile.
            await self.native_browser.close(computer_id)
            live.takeover().release("sandbox", computer_id, notify=False)
            return await self.status(computer_id)
        if not self.configured:
            raise SandboxError("The sandbox is not set up.")
        await self._supervisor(f"/computers/{computer_id}/stop", {})
        return await self.status(computer_id)

    async def control(self, computer_id: str, verb: str) -> dict:
        """The owner takes or releases the sandbox browser. Releasing works even with permissions revoked."""
        if verb not in ("take", "release"):
            raise SandboxError("Use take or release.")
        self.require(computer_id)
        if verb == "take":
            self.allowed(computer_id, "browser", "owner")
        # One takeover state for every surface (Admin, the live view, the phone): V2-12.
        takeover = live.takeover()
        (takeover.take if verb == "take" else takeover.release)("sandbox", computer_id, notify=False)
        if self.native:
            return await self.status(computer_id)
        if not self.configured:
            raise SandboxError("The sandbox is not set up.")
        if verb == "take":
            self.allowed(computer_id, "browser", "owner")
        url = await self._running(computer_id)
        token = self.token(computer_id)
        control = await self._json(f"{url}/control", token, computer_id=computer_id)
        request = (control or {}).get("request")
        if verb == "take" and (not request or request.get("status") not in ("waiting", "taken")):
            control = await self._json(
                f"{url}/control/request", token, {"reason": "Owner requested control."}, computer_id
            )
            request = (control or {}).get("request")
        if not request:
            raise SandboxError("There is no active control request.")
        await self._json(f"{url}/control/{verb}", token, {"requestId": request["id"]}, computer_id)
        return await self.status(computer_id)

    async def action(self, computer_id: str, action: str, args: dict, actor: str = "owner", cancel=None):
        if action not in ACTIONS:
            raise SandboxError("Unknown computer action.")
        parsed = validate(action, args)
        if actor == "agent" and action.startswith("human_"):
            raise SandboxError("Human controls are owner-only.")
        kind = kind_of(action)
        self.allowed(computer_id, kind, actor)
        if actor == "agent":
            try:
                live.takeover().gate(
                    "sandbox", computer_id, observe=action in ("read", "snapshot", "screenshot")
                )
            except live.ControlBlocked as exc:
                raise SandboxError(str(exc)) from None
        if self.native:
            return await self._native_guarded(computer_id, action, parsed, kind, actor, cancel)
        stop = asyncio.Event()

        async def watch():
            while not stop.is_set():
                await asyncio.sleep(0.05)
                try:
                    self.allowed(computer_id, kind, actor)
                except SandboxError:
                    stop.set()
                if cancel is not None and cancel.is_set():
                    stop.set()

        watcher = asyncio.ensure_future(watch())
        try:
            url = await self._running(computer_id, stop)
            self.allowed(computer_id, kind, actor)
            if stop.is_set():
                raise SandboxError("Computer request was cancelled or timed out.")
            path = action.replace("files_", "files/", 1).replace("human_", "human/", 1)
            body = None if action in ("read", "screenshot") else parsed
            result = await self._json(
                f"{url}/{path}", self.token(computer_id), body, computer_id, cancel=stop
            )
            self.allowed(computer_id, kind, actor)
            if action == "exec" and isinstance(result, dict):
                result = {k: v for k, v in result.items() if k != "command"}
            return result
        finally:
            stop.set()
            watcher.cancel()


async def _owner_consent(summary, detail) -> bool:
    return True  # The owner pressed Start in Admin: that is the consent.


# Host file exchange --------------------------------------------------------------------------------
def import_text(host_path: str) -> str:
    """A text file from this PC for the sandbox: an existing file, not secret-looking, at most 100 KB."""
    from ..harness.computer import SECRET

    path = Path(host_path).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise SandboxError(f"{host_path} is not a file on this PC.")
    if SECRET.search(str(path)):
        raise SandboxError(f"{path.name} looks like a secret or key file; PANINI does not copy it.")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise SandboxError("Only text files up to 100 KB can be copied into the sandbox.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise SandboxError("Only text files can be copied into the sandbox.") from None


def export_destination(name: str) -> Path:
    """Where a file from the sandbox lands on this PC: Downloads\\PANINI sandbox, never overwriting."""
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name) or "sandbox-file.txt"
    folder = Path.home() / "Downloads" / "PANINI sandbox"
    folder.mkdir(parents=True, exist_ok=True)
    target, stem, suffix, n = folder / safe, Path(safe).stem, Path(safe).suffix, 1
    while target.exists():
        target, n = folder / f"{stem} ({n}){suffix}", n + 1
    return target


# The registry hand ----------------------------------------------------------------------------------
TOOL_ACTIONS = {
    "sandbox_navigate": "navigate",
    "sandbox_read": "read",
    "sandbox_snapshot": "snapshot",
    "sandbox_screenshot": "screenshot",
    "sandbox_click": "click",
    "sandbox_type": "type",
    "sandbox_key": "key",
    "sandbox_scroll": "scroll",
    "sandbox_files_list": "files_list",
    "sandbox_files_read": "files_read",
    "sandbox_files_write": "files_write",
    "sandbox_exec": "exec",
}


class SandboxHand:
    lenient_required = False

    def __init__(self, manifest, runtime):
        self.manifest, self.runtime = manifest, runtime
        self._service = None

    @property
    def service(self) -> SandboxService:
        harness = self.runtime.get("harness")
        service = getattr(harness, "sandbox", None)
        if service is None:
            if self._service is None:
                self._service = SandboxService(harness.store)
            service = self._service
        return service

    @staticmethod
    def computer_for(ctx) -> str:
        """One computer per persona; until personas arrive (V2-18) PANINI has one, named panini."""
        principal = getattr(ctx, "principal", None)
        if getattr(principal, "kind", None) == "mcp_client":
            client = ctx.mcp_service.clients.get(principal.client_id)
            if not client or client["revoked"] is not None or "sandbox" not in client["grants"]:
                raise SandboxError("This client has no sandbox permission.")
            return "mcp_" + client["id"]
        if getattr(principal, "kind", None) == "persona":
            harness = getattr(getattr(ctx, "agent", None), "h", None)
            if harness is None:
                raise SandboxError("A specialist sandbox requires its authenticated run context.")
            persona = harness.personas.get(principal.client_id)
            computer = persona["computer_id"]
            if not computer or not NAME.fullmatch(computer):
                raise SandboxError("This persona has no assigned computer.")
            return computer
        persona = getattr(principal, "persona", None)
        return persona if isinstance(persona, str) and NAME.match(persona) else DEFAULT_COMPUTER

    async def readiness(self, capability=None):
        from .. import v2flags

        if not v2flags.enabled("PANINI_SANDBOX"):
            return Readiness("unavailable", "Sandbox computers are off (PANINI_SANDBOX).")
        if not self.service.configured:
            return Readiness("needs_setup", "Set up the sandbox in PANINI Admin → Computers.")
        return Readiness("ready")

    async def call(self, ctx, spec, args):
        name = spec.name
        computer = self.computer_for(ctx)
        service = self.service
        try:
            if getattr(ctx.principal, "kind", None) == "mcp_client":
                if name in {"sandbox_import", "sandbox_export"}:
                    return ToolResult("blocked", "Host file transfer is unavailable to MCP clients.")
                service.store.execute(
                    "INSERT OR IGNORE INTO sandbox_computers(id, persona_id, created, permissions) VALUES (?,?,?,?)",
                    (
                        computer,
                        computer,
                        service.clock(),
                        json.dumps({"enabled": True, "browser": True, "files": True, "shell": True}),
                    ),
                )  # Explicit owner sandbox scope initializes once; revoked permissions stay off.
            else:
                service.ensure_row(computer)
            if name == "sandbox_status":
                status = await service.status(computer)
                return ToolResult("ok", json.dumps(status), data=status)
            if name == "sandbox_start":
                status = await service.start(computer, actor="agent", consent=getattr(ctx, "confirm", None))
                return ToolResult("ok", f"The sandbox computer is {status['state']}.", data=status)
            if name == "sandbox_stop":
                status = await service.stop(computer)
                return ToolResult("ok", "The sandbox computer stopped.", data=status)
            if name == "sandbox_import":
                contents = import_text(str(args.get("host_path", "")))
                dest = _path(args.get("dest") or Path(str(args.get("host_path"))).name, required=True)
                await service.action(computer, "files_write", {"path": dest, "contents": contents}, "agent")
                digest = hashlib.sha256(contents.encode()).hexdigest()
                evidence = [
                    {"kind": "file", "summary": f"Copied into the sandbox: {dest}", "receipt_sha256": digest}
                ]
                return ToolResult(
                    "ok", f"Copied to {dest} in the sandbox.", data={"path": dest}, evidence=evidence
                )
            if name == "sandbox_export":
                path = _path(args.get("path"), required=True)
                result = await service.action(computer, "files_read", {"path": path}, "agent")
                text = (
                    result.get("contents", result.get("text", ""))
                    if isinstance(result, dict)
                    else str(result)
                )
                target = export_destination(str(args.get("name") or path))
                target.write_text(str(text), encoding="utf-8")
                digest = hashlib.sha256(str(text).encode()).hexdigest()
                evidence = [
                    {"kind": "file", "summary": f"Saved on this PC: {target}", "receipt_sha256": digest}
                ]
                return ToolResult("ok", f"Saved to {target}.", data={"saved": str(target)}, evidence=evidence)
            action = TOOL_ACTIONS.get(name)
            if action is None:
                return ToolResult("blocked", f"{name} is not available.")
            cancel = asyncio.Event()
            result = await service.action(computer, action, args, "agent", cancel=cancel)
        except SandboxError as exc:
            message = str(exc)
            outcome = (
                "failed"
                if "unavailable" in message or "HTTP" in message or "cancelled" in message
                else "blocked"
            )
            return ToolResult(outcome, message)
        evidence = []
        data = result if isinstance(result, dict) else {"result": result}
        if action == "exec" and isinstance(result, dict) and "exitCode" in result:
            evidence.append(
                {"kind": "command", "summary": "Sandbox command ran.", "exit_code": result["exitCode"]}
            )
        elif action == "files_write":
            digest = hashlib.sha256(args.get("contents", "").encode()).hexdigest()
            evidence.append(
                {
                    "kind": "file",
                    "summary": f"Wrote {args.get('path')} in the sandbox",
                    "receipt_sha256": digest,
                }
            )
        elif action in ("snapshot", "read", "screenshot"):
            evidence.append({"kind": "page", "summary": "Observed the sandbox browser."})
        artifacts = []
        if (
            action == "screenshot"
            and getattr(ctx.principal, "kind", None) == "mcp_client"
            and data.get("base64")
        ):
            from ..mcp_server.tools import image_bytes

            if len(data["base64"]) > 5_333_336:
                raise SandboxError("Screenshot exceeds the input size limit.")
            raw = image_bytes(base64.b64decode(data["base64"], validate=True))
            artifact = ctx.mcp_service.h.artifacts.put("screenshot", raw, "image/jpeg", run_id=ctx.run_id)
            artifacts.append(artifact)
            data = {k: v for k, v in data.items() if k != "base64"}
        outcome = "ok"
        if action == "exec" and isinstance(result, dict):
            if result.get("cancelled"):
                outcome = "cancelled"
            elif result.get("timedOut") or result.get("exitCode") not in {None, 0}:
                outcome = "failed"
        return ToolResult(
            outcome,
            json.dumps(data, ensure_ascii=False)[:12000],
            data=data,
            evidence=evidence,
            artifact_ids=artifacts,
        )
