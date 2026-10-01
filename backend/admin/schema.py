"""Every user-facing setting, described once. The Admin UI renders forms from this catalogue.

Adding a setting here makes it editable, validated and documented in PANINI Admin with
no UI code. `restart` marks settings that only take effect after PANINI restarts;
everything else is applied immediately.
"""

import os
from dataclasses import asdict, dataclass, field

REASONING = ["", "minimal", "low", "medium", "high"]


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    type: str = "text"  # text | secret | bool | int | enum | paths
    help: str = ""
    default: str = ""
    restart: bool = False
    options: list = field(default_factory=list)
    minimum: int | None = None
    maximum: int | None = None
    test: str | None = None  # Name of a live test for secrets.
    advanced: bool = False

    def view(self):
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "") or k in ("default", "help")}


SETTINGS = [
    # Models & keys
    Setting(
        "OPENAI_API_KEY",
        "keys",
        "OpenAI API key",
        "secret",
        "Used for the agent, planning, voice, search ranking and PANINI-CLI.",
        test="openai",
    ),
    Setting(
        "AI_GATEWAY_API_KEY",
        "keys",
        "AI Gateway API key",
        "secret",
        "Used by Search and Compare to reach other model providers.",
    ),
    Setting(
        "ASSEMBLYAI_API_KEY",
        "keys",
        "AssemblyAI API key",
        "secret",
        "Speech-to-text for dictation.",
        test="assemblyai",
    ),
    Setting(
        "PANINI_AGENT_MODEL",
        "models",
        "Agent model",
        help="Model for the PANINI agent. Empty uses the CLI model.",
        default="",
    ),
    Setting(
        "PANINI_PLANNER_MODEL",
        "models",
        "Planner model",
        help="Model that routes each request to a capability.",
        default="",
    ),
    Setting(
        "PANINI_CLI_MODEL",
        "models",
        "CLI model",
        default="",
        help="Model for PANINI-CLI. Empty uses JEV_REV_CODE_MODEL, PANINI_AGENT_MODEL, planner model, then gpt-4.1.",
    ),
    Setting(
        "PANINI_CLI_REASONING_EFFORT",
        "models",
        "Coding reasoning effort",
        "enum",
        "Higher is slower and more thorough.",
        default="low",
        options=REASONING,
    ),
    Setting(
        "EXTENSION_PLANNER_MODEL",
        "models",
        "Browser planner model",
        help="Model that plans actions in your Chrome tab.",
        advanced=True,
    ),
    Setting(
        "PANINI_FAST_MODEL",
        "models",
        "Fast model",
        help="Small model for simple extraction, such as turning a browser request into a task plan.",
        default="gpt-4.1-mini",
        restart=True,
        advanced=True,
    ),
    Setting(
        "PANINI_BROWSER_FALLBACK",
        "browser",
        "When your Chrome isn't connected",
        "enum",
        "separate: carry on in a separate browser and say why. ask: stop and explain how to connect Chrome.",
        default="separate",
        options=["separate", "ask"],
    ),
    Setting(
        "PANINI_CLEANUP",
        "models",
        "Close windows a task opened",
        "enum",
        "auto: Jev closes windows used only to do the work and keeps what you asked to open. "
        "always: close every window the task opened. never: leave everything open.",
        default="auto",
        options=["auto", "always", "never"],
    ),
    Setting(
        "PANINI_APPROVALS",
        "models",
        "Ask before",
        "enum",
        "risky: only paying, sending, posting, deleting, signing in, applying or risky commands. "
        "all: every browser, desktop and coding action.",
        default="risky",
        options=["risky", "all"],
    ),
    Setting(
        "PANINI_JEV_GATES",
        "models",
        "Jev decides routing and completion",
        "bool",
        "Jev routes plain requests without an LLM call and confirms the agent's success claims.",
        default="true",
    ),
    Setting(
        "PANINI_SCREENSHOT_DETAIL",
        "models",
        "Browser screenshot detail",
        "enum",
        "Auto sends low detail and switches to high only when an action failed or changed nothing.",
        default="auto",
        options=["auto", "low", "high"],
        restart=True,
        advanced=True,
    ),
    # Voice
    Setting("VOICE_MODEL", "voice", "Voice model", help="Realtime model for live conversation."),
    Setting(
        "VOICE_NAME", "voice", "Voice", help="The voice PANINI speaks with (for example marin or cedar)."
    ),
    Setting(
        "VOICE_SESSION_SECONDS",
        "voice",
        "Longest voice session (seconds)",
        "int",
        default="3600",
        minimum=60,
        maximum=14400,
    ),
    # Browser
    Setting(
        "BROWSER_HEADLESS",
        "browser",
        "Run the separate browser without a window",
        "bool",
        "Only for the separate (managed) browser; your Chrome tab is always visible.",
        default="false",
    ),
    # Desktop
    Setting(
        "JEV_REV_DESKTOP_ENABLED",
        "desktop",
        "Desktop control",
        "bool",
        "Let PANINI operate supported Windows applications (asks before each action).",
        default="false",
        restart=True,
    ),
    Setting(
        "PANINI_DESKTOP_VISUAL",
        "desktop",
        "Screenshot-aware actions",
        "bool",
        "Off uses the accessibility tree only.",
        default="true",
    ),
    Setting("PANINI_DESKTOP_FEEDBACK", "desktop", "Show action feedback on screen", "bool", default="true"),
    # Code
    Setting(
        "PANINI_CLI_ENABLED",
        "code",
        "Coding",
        "bool",
        "Let PANINI write and run code in the workspaces below. Replaces `npm run panini:code`.",
        default="false",
        restart=True,
    ),
    Setting(
        "PANINI_CLI_WORKSPACES",
        "code",
        "Workspaces",
        "paths",
        "Folders PANINI-CLI may work in. Nothing outside them is accessible.",
        default="[]",
        restart=True,
    ),
    Setting(
        "PANINI_CLI_SANDBOX",
        "code",
        "Command sandbox",
        "enum",
        "none runs locally; native uses PANINI's experimental Windows AppContainer sandbox; codex and docker are optional adapters.",
        default="native" if os.name == "nt" else "none",
        options=["none", "native", "codex", "docker"],
    ),
    Setting("PANINI_CLI_SANDBOX_IMAGE", "code", "Sandbox image", default="python:3.12-slim", advanced=True),
    Setting(
        "PANINI_CLI_COMMAND_TIMEOUT",
        "code",
        "Command time limit (seconds)",
        "int",
        default="120",
        minimum=5,
        maximum=600,
        advanced=True,
    ),
    Setting(
        "PANINI_CLI_MAX_STEPS",
        "code",
        "Step budget per task",
        "int",
        default="60",
        minimum=1,
        maximum=500,
        advanced=True,
    ),
    # Agent
    Setting(
        "PANINI_HARNESS_ENABLED",
        "agent",
        "Agent harness",
        "bool",
        "Memory, skills, background tasks, connectors and the inbox.",
        default="1",
        restart=True,
    ),
    # Network
    Setting(
        "FRONTEND_PORT",
        "network",
        "Console port",
        "int",
        default="3200",
        minimum=1024,
        maximum=65535,
        restart=True,
        advanced=True,
    ),
    Setting(
        "BACKEND_PORT",
        "network",
        "Backend port",
        "int",
        default="8200",
        minimum=1024,
        maximum=65535,
        restart=True,
        advanced=True,
    ),
    Setting(
        "SEARCH_PORT",
        "network",
        "Search port",
        "int",
        default="3230",
        minimum=1024,
        maximum=65535,
        restart=True,
        advanced=True,
    ),
]

BY_KEY = {s.key: s for s in SETTINGS}
GROUPS = {
    "keys": "Keys",
    "models": "Models",
    "voice": "Voice",
    "browser": "Browser",
    "desktop": "Desktop",
    "code": "Code",
    "agent": "Agent",
    "network": "Network",
}
