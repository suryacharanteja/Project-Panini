"""Runtime configuration. The runtime always enables the coordinator with a per-launch bootstrap token."""

import os
from dataclasses import dataclass, field
from uuid import uuid4


def enabled(name):
    return os.getenv(name, "false").lower() in {"true", "1", "yes"}


def enabled_any(*names):
    return any(enabled(name) for name in names)


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    desktop_enabled: bool = False
    code_enabled: bool = False
    bootstrap_token: str = field(default="", repr=False)
    instance_id: str = field(default_factory=lambda: str(uuid4()))
    profile_id: str = "panini"
    release_id: str = "development"
    lease_seconds: float = 15
    session_seconds: float = 3600
    retention_seconds: float = 600

    def __post_init__(self):
        if (self.desktop_enabled or self.code_enabled) and not self.enabled:
            raise ValueError("Desktop and Code require the capability coordinator")
        if self.enabled and len(self.bootstrap_token) < 32:
            raise ValueError("PANINI requires a private per-launch bootstrap token")

    @classmethod
    def from_env(cls):
        return cls(
            enabled=enabled("PANINI_CAPABILITY_ROUTER_ENABLED"),
            desktop_enabled=enabled("JEV_REV_DESKTOP_ENABLED"),
            code_enabled=enabled_any("PANINI_CLI_ENABLED", "PANINI_CLI_ENABLED"),
            bootstrap_token=os.getenv("PANINI_BOOTSTRAP_TOKEN", ""),
            instance_id=os.getenv("PANINI_INSTANCE_ID") or str(uuid4()),
            profile_id=os.getenv("PANINI_PROFILE_ID", "panini"),
            release_id=os.getenv("PANINI_RELEASE_ID", "development"),
        )
