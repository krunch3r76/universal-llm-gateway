"""Shared constants for the cursorbuild dispatch surface.

Single source of truth for values consumed by ``cursorbuild.argv`` and
``cursorbuild.home`` (and the runner/envelope/registry modules that land in
later phases). Mirrors the shape of ``grokbuild.constants`` — env-driven
dirs, derived sets, resolver helpers — retargeted to the ``cursor-agent`` CLI.

Derived sets (``_VALID_TIERS``) are computed from their canonical map so the
two cannot diverge.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# cursor-agent binary. Env override lets the worker point at a pinned build.
CURSOR_AGENT_BIN: Final[str] = os.getenv("CURSORBUILD_CURSOR_AGENT_BIN", "cursor-agent")

# Sidecar location — runner appends NDJSON here; fetch_result reads from it.
# Env-driven, XDG-compliant default; expanduser handles the tilde when the
# env var is absent. Distinct from grokbuild's dir so a co-resident worker
# never shares sidecar state (see pre-flight §2 registry two-writer note).
_SIDECAR_DIR: Final[Path] = Path(
    os.getenv(
        "CURSORBUILD_SIDECAR_DIR",
        "~/.local/share/cursorbuild-worker/sidecars",
    )
).expanduser()

# cursor-agent native worktree root: ~/.cursor/worktrees/<repo>/<name>.
# With the per-dispatch HOME override this resolves under the dispatch home.
WORKTREE_ROOT: Final[Path] = Path("~/.cursor/worktrees").expanduser()

# Caller-facing dispatch wall-clock limit when ``timeout_seconds`` is omitted.
DEFAULT_TIMEOUT_SECONDS: Final[int] = 3600

# Standalone default model when neither an explicit model nor a tier resolves.
DEFAULT_MODEL: Final[str] = "composer-2.5-fast"

# cursor read-only execution modes (`cursor-agent --mode`). Both are
# non-mutating; ``plan`` is the default per pre-flight D4.
READ_ONLY_MODES: Final[frozenset[str]] = frozenset({"plan", "ask"})
DEFAULT_READ_ONLY_MODE: Final[str] = "plan"

# Headless MCP-execution permission flags. All three are required together
# for headless MCP tool execution (validation B3 Finding 2).
HEADLESS_MCP_FLAGS: Final[tuple[str, ...]] = (
    "--approve-mcps",
    "--force",
    "--trust",
)

# Output framing — always ``stream-json`` (hyphen, NOT "streaming-json").
OUTPUT_FORMAT: Final[str] = "stream-json"

# Tokens that MUST NEVER appear in a built argv. ``-m`` / ``--cwd`` are grok
# habits with no cursor-agent equivalent; ``acp`` and
# ``--dangerously-skip-permissions`` are not part of cursor-agent's surface.
FORBIDDEN_ARGV_TOKENS: Final[tuple[str, ...]] = (
    "-m",
    "acp",
    "--dangerously-skip-permissions",
    "--cwd",
)

# cursor config-dir filenames. Login/auth lives in cli-config.json (the racy
# atomic-rename file, validation B4) — there is NO separate auth.json like
# grok. The vortex MCP server is configured in mcp.json (stdio proxy, B3).
CURSOR_CONFIG_DIRNAME: Final[str] = ".cursor"
CURSOR_AUTH_FILENAME: Final[str] = "cli-config.json"
CURSOR_MCP_FILENAME: Final[str] = "mcp.json"

# Tier -> candidate model ids (spec §4 / validation B2). First element is the
# tier default. ``_VALID_TIERS`` is derived from the keys, never hand-mirrored.
# gpt-5.5-* / claude-opus-4-8-* family wildcards in the spec are represented
# by their concrete ``-high`` variant pending a model-resolution phase.
_TIER_MODELS: Final[dict[str, tuple[str, ...]]] = {
    "reasoning": (
        "claude-opus-4-8-thinking-high",
        "claude-opus-4-8-thinking-max",
        "claude-opus-4-8-max",
    ),
    "default": ("claude-opus-4-8-high",),
    "bulk": ("composer-2.5-fast", "grok-build-0.1"),
    "code": (
        "gpt-5.3-codex-high",
        "gpt-5.2-codex-max",
        "gpt-5.1-codex-max",
        "grok-build-0.1",
    ),
    "verify": (
        "claude-opus-4-8-high",
        "gpt-5.5-high",
        "gemini-3.1-pro",
        "gemini-3.5-flash",
        "gemini-3-flash",
        "grok-build-0.1",
        "kimi-k2.5",
    ),
}
_VALID_TIERS: Final[frozenset[str]] = frozenset(_TIER_MODELS)


def default_model_for_tier(tier: str) -> str:
    """Resolve a tier to its default (first-listed) model id.

    Caller MUST validate ``tier in _VALID_TIERS`` before calling; this helper
    indexes ``_TIER_MODELS`` directly.
    """
    return _TIER_MODELS[tier][0]
