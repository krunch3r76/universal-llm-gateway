"""Conclusion-side propagation residue for cursor-auto CLOSEOUT."""

from __future__ import annotations

import json
from collections.abc import Sequence

_MAX_RESIDUE_LINES = 12
_PLUGIN_PREFIX = "cursor-plugins/ulg-ecosystem/"
_PLUGIN_CENSUS = ("SKILLS_CENSUS.txt", "RULES_ULG_CENSUS.txt")

_SERVICE_SLUGS = {
    "services/agent-bus/": "agent_bus",
    "services/cortex-api/": "cortex_api",
    "services/event-service/": "event_service",
    "services/git_integration_worker/": "git_integration_worker",
    "services/mcp-server/": "mcp",
    "services/rag/": "rag",
    "services/universal_cloud_proxy/": "cloud_proxy",
    "services/_universal-llm-gateway/": "gateway",
    "services/universal-stargate/": "stargate",
}

_FILE_FIELDS = (
    ("files_created", "files_created_total"),
    ("files_modified", "files_modified_total"),
    ("files_deleted", "files_deleted_total"),
)


def changed_paths_from_closeout(payload: str) -> tuple[tuple[str, ...], bool] | None:
    """Parse closeout JSON and return deduplicated changed paths plus truncation flag."""
    text = payload.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None

    paths: set[str] = set()
    truncated = False
    for field, total_field in _FILE_FIELDS:
        raw = decoded.get(field)
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if isinstance(entry, str):
                paths.add(entry)
        total = decoded.get(total_field)
        if isinstance(total, int) and total > len(raw):
            truncated = True

    return (tuple(sorted(paths)), truncated)


def _sync_restart_line(slug: str) -> str:
    return f'sync_restart: {slug} — manage(action="sync_restart", service="{slug}")'


def _install_plugin_line() -> str:
    return (
        "install_plugin: scripts/cursor/install-ecosystem-plugin.sh "
        "— then Cursor Developer → Reload Window"
    )


def _unresolved_line(path: str) -> str:
    return f"unresolved: {path} — service dir has no manage slug; lead must resolve"


def _libs_touched_line(path: str) -> str:
    return (
        f"libs_touched: {path} — shared lib; lead must decide which consumers restart"
    )


def _action_for_path(path: str) -> str | None:
    for prefix, slug in _SERVICE_SLUGS.items():
        if path.startswith(prefix) and path.endswith(".py"):
            return _sync_restart_line(slug)

    basename = path.rsplit("/", 1)[-1]
    if path.startswith(_PLUGIN_PREFIX) or basename in _PLUGIN_CENSUS:
        return _install_plugin_line()

    if path.startswith("services/") and path.endswith(".py"):
        return _unresolved_line(path)

    if path.startswith("libs/") and path.endswith(".py"):
        return _libs_touched_line(path)

    return None


def residue_actions(paths: Sequence[str]) -> tuple[str, ...]:
    """Map changed paths to deduplicated residue action lines in stable order."""
    sync_restart: set[str] = set()
    install_plugin: set[str] = set()
    unresolved: set[str] = set()
    libs_touched: set[str] = set()

    for path in paths:
        action = _action_for_path(path)
        if action is None:
            continue
        if action.startswith("sync_restart:"):
            sync_restart.add(action)
        elif action.startswith("install_plugin:"):
            install_plugin.add(action)
        elif action.startswith("unresolved:"):
            unresolved.add(action)
        elif action.startswith("libs_touched:"):
            libs_touched.add(action)

    ordered: list[str] = []
    ordered.extend(sorted(sync_restart))
    ordered.extend(sorted(install_plugin))
    ordered.extend(sorted(unresolved))
    ordered.extend(sorted(libs_touched))
    return tuple(ordered)


def build_residue_block(
    actions: Sequence[str], *, truncated: bool = False
) -> str | None:
    """Build ``TYPE: RESIDUE`` block, eliding overflow to stay within the line budget.

    Overflow elides rather than raises: this runs inside CLOSEOUT delivery, so an
    exception here would suppress the whole closeout to save a display budget.
    """
    if not actions:
        return None

    fixed = 3 if truncated else 2  # header lines + optional truncation note
    budget = _MAX_RESIDUE_LINES - fixed - 1  # reserve the owner line
    shown, elided = list(actions), 0
    if len(shown) > budget:
        shown, elided = shown[: budget - 1], len(actions) - (budget - 1)

    lines = [
        "TYPE: RESIDUE",
        "Landed, not yet live — propagation required:",
        *[f"- {action}" for action in shown],
    ]
    if elided:
        lines.append(
            f"- (+{elided} further action(s) elided — see files_* in this body)"
        )
    if truncated:
        lines.append("(paths truncated in this closeout — residue may be incomplete)")
    lines.append("Owner: charter tick executes sync_restart at harvest; install_plugin remains manual.")
    return "\n".join(lines)


def resolve_relay_residue(*, wrapper_body: str | None, relay_body: str) -> str | None:
    """Prefer wrapper JSON for residue; fall back to *relay_body*.

    The §2 relay body is markdown, so ``propagation_residue`` lives only on the
    machine wrapper manifest — deriving residue from relayed prose alone returns
    ``None`` even when propagation actions exist.
    """
    if wrapper_body:
        block = residue_for_closeout(wrapper_body)
        if block is not None:
            return block
    return residue_for_closeout(relay_body)


def residue_for_closeout(payload: str) -> str | None:
    """Compose residue block from closeout payload, or ``None`` when absent.

    Prefers the machine field ``propagation_residue`` (survives files_* truncation
    in ``finalize_closeout_body``); falls back to deriving actions from path lists.
    """
    text = payload.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None

    truncated = False
    for field, total_field in _FILE_FIELDS:
        raw = decoded.get(field)
        total = decoded.get(total_field)
        if isinstance(raw, list) and isinstance(total, int) and total > len(raw):
            truncated = True
            break

    raw_residue = decoded.get("propagation_residue")
    if isinstance(raw_residue, list):
        actions = tuple(
            entry for entry in raw_residue if isinstance(entry, str) and entry
        )
        if actions:
            return build_residue_block(actions, truncated=truncated)

    parsed = changed_paths_from_closeout(payload)
    if parsed is None:
        return None
    paths, path_truncated = parsed
    return build_residue_block(
        residue_actions(paths),
        truncated=truncated or path_truncated,
    )


def compose_closeout_body(base_body: str, residue: str | None) -> str:
    """Piggyback RESIDUE into closeout reply when present."""
    if not residue:
        return base_body
    return f"{base_body}\n\n{residue}"
