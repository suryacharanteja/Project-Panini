"""Composition root.

Everything is wired here and nowhere else, so the dependency direction is
visible in one file. In particular this is the only place that holds both a
``ToolRegistry`` and an ``Orchestrator`` — and it hands the orchestrator a
``ToolGateway``, never the registry (HLD §1.3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from panini.core.context import ContextAssembler, ContextBudget
from panini.core.memory.retrieval import FactRetriever
from panini.core.orchestrator import SYSTEM_PROMPT, Orchestrator, OrchestratorConfig
from panini.errors import ProviderUnavailable
from panini.events.bus import EventBus
from panini.llm.factory import build_provider
from panini.llm.provider import LLMProvider
from panini.security.approvals import ApprovalManager
from panini.security.credentials import CredentialStore
from panini.security.egress import EgressClassifier, SyncedRootRegistry
from panini.security.permissions import DestinationAllowlist, PermissionResolver
from panini.security.tool_gateway import ToolGateway
from panini.store.audit import AuditStore
from panini.store.config import Config, load_config
from panini.store.facts import FactStore, MainStore
from panini.store.history import HistoryStore
from panini.tools.impl.filesystem import build_filesystem_tools
from panini.tools.impl.shell import ShellExec
from panini.tools.paths import PathGuard, default_deny_roots
from panini.tools.registry import ToolRegistry

log = logging.getLogger("panini")


@dataclass(frozen=True, slots=True)
class Paths:
    """Filesystem layout. Audit and credentials live apart from project data so
    the deny list names them precisely (DD-139, FR-541)."""

    data_dir: Path
    config_file: Path

    @property
    def audit_db(self) -> Path:
        return self.data_dir / "audit.db"

    @property
    def main_db(self) -> Path:
        return self.data_dir / "panini.db"

    @property
    def credentials(self) -> Path:
        return Path.home() / ".config" / "panini" / "credentials.enc"

    @classmethod
    def default(cls, root: Path | None = None) -> Paths:
        base = root or Path.home() / "AppData" / "Local" / "Panini"
        return cls(data_dir=base, config_file=Path("config/panini.yaml"))


class Application:
    """Owns the wired object graph and its lifecycle."""

    def __init__(self, config: Config, paths: Paths) -> None:
        self.config = config
        self.paths = paths
        self.bus = EventBus()

        self.audit = AuditStore(paths.audit_db)
        self.main = MainStore(paths.main_db)
        self.facts = FactStore(self.main)
        self.history = HistoryStore(self.main)
        self.credentials = CredentialStore(paths.credentials)

        self._guard = self._build_path_guard()
        self.registry = self._build_registry()

        self.approvals = ApprovalManager(
            self.bus, timeout_s=float(config.security.approval_timeout_s)
        )
        self.gateway = ToolGateway(
            self.registry,
            permissions=PermissionResolver(
                DestinationAllowlist(frozenset(config.security.egress_allowlist))
            ),
            egress=EgressClassifier(
                SyncedRootRegistry([Path(p) for p in config.security.synced_roots])
            ),
            approvals=self.approvals,
            audit=self.audit,
            credentials=self.credentials,
            bus=self.bus,
        )

        self.context = ContextAssembler(
            self.history,
            self.facts,
            FactRetriever(score_threshold=config.memory.facts.score_threshold),
            self.bus,
            budget=ContextBudget(
                total=config.llm.ollama.options.num_ctx,
                facts_tokens=config.memory.facts.budget_tokens,
            ),
            working_turns=config.memory.working_turns,
        )

        self.provider: LLMProvider | None = None
        self.orchestrator: Orchestrator | None = None
        self.provider_error: str | None = None

    # ── construction helpers ─────────────────────────────────────────────────

    def _build_path_guard(self) -> PathGuard:
        allow = [Path(r).expanduser() for r in self.config.tools.filesystem.allow_roots]
        # Deny roots are code-owned. A user-editable deny list could be emptied,
        # so they are never read from configuration (DD-145).
        deny = default_deny_roots(self.paths.audit_db, self.paths.credentials)
        synced = [Path(p).expanduser() for p in self.config.security.synced_roots]
        return PathGuard(allow_roots=allow, deny_roots=deny, synced_roots=synced)

    def _build_registry(self) -> ToolRegistry:
        registry = ToolRegistry()

        for tool in build_filesystem_tools(
            self._guard, self.config.tools.filesystem.max_read_bytes
        ):
            registry.register(tool)

        registry.register(
            ShellExec(
                self._guard,
                Path(self.config.tools.shell.working_dir).expanduser(),
                timeout_s=float(self.config.tools.shell.timeout_s),
                max_output_bytes=self.config.tools.shell.max_output_bytes,
            )
        )

        registry.apply_enabled_map(self.config.tools.enabled)
        return registry

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)
        await self.audit.open()
        await self.main.open()
        await self.registry.check_health()
        await self._connect_provider()

    async def _connect_provider(self) -> None:
        """Connect to the configured provider.

        A failure is recorded and surfaced, never worked around: substituting a
        different provider could send personal content somewhere the user did
        not choose (FR-214).
        """
        name = self.config.llm.provider
        cfg = getattr(self.config.llm, name, None)
        provider_cfg = cfg.model_dump() if cfg is not None else {}

        try:
            provider = build_provider(name, provider_cfg, self.credentials)
        except Exception as exc:
            self.provider_error = str(exc)
            log.error("Provider %s could not be constructed: %s", name, exc)
            return

        status = await provider.health()
        if not status.available:
            self.provider_error = status.detail
            await provider.aclose()
            log.error("Provider %s unavailable: %s", name, status.detail)
            self.bus.publish("provider.unavailable", {"provider": name, "detail": status.detail})
            return

        self.provider = provider
        self.orchestrator = Orchestrator(
            self.gateway,
            provider,
            self.bus,
            config=OrchestratorConfig(
                max_tool_calls=self.config.llm.max_tool_calls_per_turn,
                temperature=self.config.llm.ollama.options.temperature,
            ),
        )
        self.provider_error = None
        self.bus.publish(
            "provider.changed",
            {
                "provider": provider.name,
                "model": provider.capabilities.model,
                "is_local": provider.capabilities.is_local,
            },
        )
        log.info("Provider ready: %s (%s)", provider.name, provider.capabilities.model)

    async def stop(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()
        await self.main.close()
        await self.audit.close()

    # ── introspection ────────────────────────────────────────────────────────

    def require_orchestrator(self) -> Orchestrator:
        if self.orchestrator is None:
            raise ProviderUnavailable(
                self.config.llm.provider, self.provider_error or "not connected"
            )
        return self.orchestrator

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def health(self) -> dict[str, object]:
        return {
            "provider": {
                "name": self.config.llm.provider,
                "ready": self.provider is not None,
                "model": self.provider.capabilities.model if self.provider else None,
                "is_local": self.provider.capabilities.is_local if self.provider else None,
                "error": self.provider_error,
            },
            "tools": {
                name: self.registry.is_enabled(name) for name in self.registry.names()
            },
            "audit": {"path": str(self.paths.audit_db)},
        }


async def build_application(
    config_path: Path | None = None, data_root: Path | None = None
) -> tuple[Application, list[str]]:
    """Load configuration and construct the application."""
    paths = Paths.default(data_root)
    if config_path is not None:
        paths = Paths(data_dir=paths.data_dir, config_file=config_path)

    config, warnings = load_config(paths.config_file)
    app = Application(config, paths)
    await app.start()
    return app, warnings
