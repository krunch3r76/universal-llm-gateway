"""IDE-parity agent context for cursor-sdk bridge dispatches.

Loads the same ambient Cursor settings layers as the IDE Composer seat:
project rules (repo + parent traversal), user rules, team/plugins, and the
vortex MCP stdio surface used in ``~/.cursor/mcp.json``.

SDK dispatches use ``scripts/mcp-fastmcp-remote-bridge.py`` — a thin launcher
around upstream ``fastmcp-remote`` (Track 3). Cursor steady-state remains
direct HTTPS; stdio is fallback / SDK lane only.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from cursor_sdk.types import (
    AgentOptions,
    LocalAgentOptions,
    LocalAgentStoreConfig,
    ModelSelection,
    StdioMcpServerConfig,
)

from services.git_integration_worker.cursor_home import observed_home_kind
from services.git_integration_worker.cursor_sdk_substrate_tools import (
    SubstrateDispatchContext,
    merge_substrate_tools,
)

_VORTEX_MCP_SERVER = "user-vortex"
# IDE/docs name for the same code mount. Copied mcp.json registers this
# without a bearer and Cursor then offers mcp_auth (unsupported in SDK).
_VORTEX_MCP_ALIAS_SERVERS: tuple[str, ...] = ("vortex-code",)
_MCP_BRIDGE_RELPATH = Path("scripts/mcp-fastmcp-remote-bridge.py")
_FASTMCP_REMOTE_CMD = "fastmcp-remote"
_SETTING_SOURCES: tuple[str, ...] = ("all",)
ULG_MCP_CONTRACT_ENV = "ULG_MCP_CONTRACT"
_CONTRACT_MCP_FILTER: frozenset[str] = frozenset({"implement", "pure-mechanical"})
_MCP_YAML_REL = Path(".gateway") / "mcp.yaml"
_CURSOR_XDG_AUTH = Path(".config") / "cursor" / "auth.json"


class CursorSdkParityError(ValueError):
    """Pre-flight parity failure — fail closed at dispatch admit."""


def _operator_home(real_home: Path | str | None = None) -> Path:
    if real_home is not None:
        return Path(real_home).expanduser()
    return Path(os.environ.get("HOME") or "~").expanduser()


def _observe_home(real_home: Path | str | None = None) -> tuple[Path, str]:
    """Return ``(home, observed_home_kind)`` for the path a token/auth read uses."""
    home = _operator_home(real_home)
    return home, observed_home_kind(home)


def _read_mcp_yaml_scalar(home: Path, key: str) -> str:
    yaml_path = home / _MCP_YAML_REL
    if not yaml_path.is_file():
        return ""
    prefix = f"{key}:"
    for raw_line in yaml_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line.split(":", 1)[1].split("#", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def resolve_mcp_token(*, real_home: Path | str | None = None) -> tuple[str, str]:
    """Return ``(token, source_label)`` for vortex MCP auth."""
    for env_key in ("MCP_TOKEN", "MCP_AUTH_TOKEN"):
        value = os.environ.get(env_key, "").strip()
        if value:
            return value, f"env:{env_key}"

    home = _operator_home(real_home)
    token_env_name = _read_mcp_yaml_scalar(home, "auth_token_env").strip()
    if token_env_name:
        from_env = os.environ.get(token_env_name, "").strip()
        if from_env:
            return from_env, f"env:{token_env_name}"

    from_yaml = _read_mcp_yaml_scalar(home, "auth_token").strip()
    if from_yaml:
        return from_yaml, "yaml:auth_token"

    return "", f"miss:observed_home_kind={observed_home_kind(home)}"


def resolve_cursor_auth_source(*, real_home: Path | str | None = None) -> str:
    """Return how this dispatch will authenticate to Cursor (env key or auth.json)."""
    if os.environ.get("CURSOR_API_KEY", "").strip():
        return "env:CURSOR_API_KEY"
    auth_path = _operator_home(real_home) / _CURSOR_XDG_AUTH
    if auth_path.is_file():
        return f"file:{auth_path}"
    return ""


def resolve_fastmcp_remote_cmd() -> str:
    """Locate ``fastmcp-remote`` for parity + stdio exec.

    Prefer ``PATH``, then the active interpreter's venv ``bin/``. GIW's
    systemd unit historically omitted the venv from ``PATH`` while still
    launching via ``~/.venvs/universal/bin/python`` — ``shutil.which`` alone
    then 422s every nested cursor-sdk submit (a:26890) even when the
    package is installed next to that interpreter.
    """
    found = shutil.which(_FASTMCP_REMOTE_CMD)
    if found:
        return found
    # Prefer sys.prefix (venv root); avoid Path(sys.executable).resolve()
    # which follows the python symlink into /usr/bin and loses the venv.
    candidates = (
        Path(sys.prefix) / "bin" / _FASTMCP_REMOTE_CMD,
        Path(sys.executable).parent / _FASTMCP_REMOTE_CMD,
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise CursorSdkParityError(
        f"{_FASTMCP_REMOTE_CMD} not on PATH or under sys.prefix/bin — "
        "install fastmcp-remote==3.4.2 into the active venv"
    )


def resolve_mcp_bridge(source_repo: Path) -> Path:
    """Return the vortex stdio bridge script; fail closed at parity checks."""
    bridge = (source_repo / _MCP_BRIDGE_RELPATH).resolve()
    if not bridge.is_file():
        raise CursorSdkParityError(f"vortex MCP bridge missing: {bridge}")
    resolve_fastmcp_remote_cmd()
    return bridge


def validate_dispatch_context(
    source_repo: Path,
    *,
    real_home: Path | str | None = None,
) -> dict[str, object]:
    """Verify IDE-parity substrate before admitting a dispatch."""
    home, home_kind = _observe_home(real_home)
    bridge = resolve_mcp_bridge(source_repo)
    remote_cmd = resolve_fastmcp_remote_cmd()

    token, token_source = resolve_mcp_token(real_home=home)
    if not token:
        raise CursorSdkParityError(
            "MCP token not configured — set MCP_TOKEN/MCP_AUTH_TOKEN or "
            f"{home / _MCP_YAML_REL} auth_token/auth_token_env "
            f"(observed_home_kind={home_kind})"
        )

    cursor_auth = resolve_cursor_auth_source(real_home=home)
    if not cursor_auth:
        raise CursorSdkParityError(
            "Cursor credential not configured — set CURSOR_API_KEY or "
            f"seed {home / _CURSOR_XDG_AUTH} "
            f"(observed_home_kind={home_kind})"
        )

    user_rules = home / ".cursor" / "rules"
    return {
        "setting_sources": list(_SETTING_SOURCES),
        "mcp_server": _VORTEX_MCP_SERVER,
        "mcp_bridge": str(bridge),
        "mcp_remote_cmd": remote_cmd,
        "mcp_token_source": token_source,
        "cursor_auth_source": cursor_auth,
        "user_rules_dir_present": user_rules.is_dir(),
        "observed_home_kind": home_kind,
        "observed_home": str(home),
    }


def _resolve_mcp_token_env(*, real_home: Path | str | None = None) -> dict[str, str]:
    """Env for the stdio MCP bridge subprocess.

    cursor-sdk passes ``StdioMcpServerConfig.env`` as the *complete* subprocess
    environment (not a merge). A token-only dict breaks bridge startup: the
    launcher imports ``services.*`` and ``transport_utils``, which require the
    universal venv ``sitecustomize`` / ``PYTHONPATH`` from the parent env.
    """
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    mcp_url = os.environ.get("MCP_URL", "").strip()
    if mcp_url:
        env["MCP_URL"] = mcp_url
    token, _source = resolve_mcp_token(real_home=real_home)
    if token:
        env["MCP_TOKEN"] = token
    return env


def build_local_agent_options(
    dispatch_workspace: Path,
    *,
    workspace_root: Path | None = None,
    state_root: Path | str | None = None,
) -> LocalAgentOptions:
    """Mirror IDE Composer ambient settings; cwd = dispatch write-surface anchor.

    When ``workspace_root`` differs from ``dispatch_workspace`` (hub Lane-A with a
    shared projects-root cwd, Lane-A satellite, or any path where the workspace
    policy root != write surface), the control repo is exposed via ``local.dirs``
    — cursor-sdk's typed multi-root field for exposing a workspace's control repo
    alongside its write surface.
    """
    store = None
    if state_root is not None:
        store = LocalAgentStoreConfig(
            type="sqlite",
            root_dir=str(Path(state_root).resolve()),
        )
    cwd = str(dispatch_workspace.resolve())
    dirs: tuple[str, ...] | None = None
    if workspace_root is not None:
        repo_path = str(workspace_root.resolve())
        if repo_path != cwd:
            dirs = (repo_path,)
    return LocalAgentOptions(
        cwd=cwd,
        dirs=dirs,
        setting_sources=_SETTING_SOURCES,
        store=store,
    )


def build_mcp_servers(
    source_repo: Path,
    *,
    real_home: Path | str | None = None,
    handoff_contract: str | None = None,
) -> dict[str, StdioMcpServerConfig]:
    """Stdio vortex MCP via ``fastmcp-remote`` bridge (see module docstring).

    Registers ``user-vortex`` and the IDE alias ``vortex-code`` on the same
    bearer-injected stdio transport so seats that call either name reach
    the code mount. ``vortex-life`` is not aliased (life mount is out of
    the SDK contract).
    """
    bridge = resolve_mcp_bridge(source_repo)
    env = dict(_resolve_mcp_token_env(real_home=real_home))
    contract = (handoff_contract or "").strip().lower()
    if contract in _CONTRACT_MCP_FILTER:
        env[ULG_MCP_CONTRACT_ENV] = contract
    names = (_VORTEX_MCP_SERVER, *_VORTEX_MCP_ALIAS_SERVERS)
    return {
        name: StdioMcpServerConfig(
            command=sys.executable,
            args=[str(bridge)],
            env=dict(env) or None,
            cwd=str(source_repo.resolve()),
        )
        for name in names
    }


def build_agent_options(
    source_repo: Path,
    dispatch_workspace: Path,
    model: ModelSelection,
    *,
    workspace_root: Path | None = None,
    real_home: Path | str | None = None,
    substrate_ctx: SubstrateDispatchContext | None = None,
    state_root: Path | str | None = None,
    handoff_contract: str | None = None,
) -> AgentOptions:
    """Full create_agent options for IDE-parity cursor-sdk dispatch."""
    local = build_local_agent_options(
        dispatch_workspace,
        workspace_root=workspace_root if workspace_root is not None else source_repo,
        state_root=state_root,
    )
    local = merge_substrate_tools(local, substrate_ctx)
    return AgentOptions(
        model=model,
        mode="agent",
        local=local,
        mcp_servers=build_mcp_servers(
            source_repo,
            real_home=real_home,
            handoff_contract=handoff_contract,
        ),
    )
