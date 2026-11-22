"""Native Task helper allow-set resolution and dispatch HOME hook wiring."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from universal_logging import get_logger

from services.git_integration_worker.cursor_home import (
    STEER_NATIVE_HOOK_TESTED_SDK_VERSIONS,
    installed_cursor_sdk_version,
)
from services.git_integration_worker.cursor_sdk_breadth_recon import (
    packet_owes_breadth_recon,
)
from services.git_integration_worker.cursor_sdk_run_limits import (
    extract_native_helper_allow_from_packet,
)

logger = get_logger(__name__)

NATIVE_HELPER_SPOOL_DIR_ENV = "ULG_NATIVE_HELPER_SPOOL_DIR"
GIW_NATIVE_HELPER_ALLOWLIST: frozenset[str] = frozenset({"explore"})
_NATIVE_HELPER_HOOK_TIMEOUT_S = 5

NativeHelperEnforcement = Literal["enforced", "unenforced"]


@dataclass(frozen=True, slots=True)
class NativeHelperPolicy:
    allow_kinds: frozenset[str]
    enforcement: NativeHelperEnforcement

    def allows(self, kind: str | None) -> bool:
        normalized = (kind or "").strip().lower() or "unspecified"
        if normalized == "unspecified":
            return False
        return normalized in self.allow_kinds


def resolve_native_helper_enforcement() -> NativeHelperEnforcement:
    version = installed_cursor_sdk_version()
    script = native_helper_hook_script_path()
    if version not in STEER_NATIVE_HOOK_TESTED_SDK_VERSIONS:
        return "unenforced"
    if not script.is_file():
        logger.warning("native helper hook script absent at %s", script)
        return "unenforced"
    return "enforced"


def native_helper_hook_script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "cursor_sdk_native_helper_hook.py"


def native_helper_hook_command() -> str:
    script = native_helper_hook_script_path()
    return f"{sys.executable} {script}"


def resolve_native_helper_policy(
    *,
    packet_text: str = "",
    contract: str | None = None,
    enforcement: NativeHelperEnforcement | None = None,
) -> NativeHelperPolicy:
    eff = enforcement or resolve_native_helper_enforcement()
    allow: set[str] = set()
    packet_allow = extract_native_helper_allow_from_packet(packet_text)
    for kind in packet_allow:
        if kind.lower() in GIW_NATIVE_HELPER_ALLOWLIST:
            allow.add(kind.lower())
    if packet_owes_breadth_recon(wrapper_text=packet_text, contract=contract or ""):
        allow.add("explore")
    return NativeHelperPolicy(allow_kinds=frozenset(allow), enforcement=eff)


def load_native_helper_policy_from_record(
    record_json: str | Mapping[str, Any] | None,
    *,
    packet_text: str = "",
    contract: str | None = None,
) -> NativeHelperPolicy:
    enforcement = resolve_native_helper_enforcement()
    blob: dict[str, Any] = {}
    if isinstance(record_json, str) and record_json.strip():
        try:
            parsed = json.loads(record_json)
            if isinstance(parsed, dict):
                raw = parsed.get("native_helper_policy")
                if isinstance(raw, dict):
                    blob = raw
        except json.JSONDecodeError:
            pass
    elif isinstance(record_json, Mapping):
        raw = record_json.get("native_helper_policy")
        if isinstance(raw, dict):
            blob = dict(raw)
    recorded_enforcement = blob.get("enforcement")
    if recorded_enforcement in {"enforced", "unenforced"}:
        enforcement = recorded_enforcement
    allow_raw = blob.get("allow_kinds") or []
    allow = {str(k).lower() for k in allow_raw if isinstance(k, str)}
    if allow:
        return NativeHelperPolicy(allow_kinds=frozenset(allow), enforcement=enforcement)
    return resolve_native_helper_policy(
        packet_text=packet_text,
        contract=contract,
        enforcement=enforcement,
    )


def native_helper_policy_record(policy: NativeHelperPolicy) -> dict[str, Any]:
    return {
        "allow_kinds": sorted(policy.allow_kinds),
        "enforcement": policy.enforcement,
    }


def native_helper_spool_dir() -> Path:
    raw = os.environ.get(NATIVE_HELPER_SPOOL_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".gateway" / "native-helper-spool"


def native_helper_attempts_path(dispatch_id: str) -> Path:
    base = native_helper_spool_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{dispatch_id}.jsonl"


def append_native_helper_attempt(
    dispatch_id: str,
    record: Mapping[str, Any],
) -> None:
    path = native_helper_attempts_path(dispatch_id)
    line = json.dumps(dict(record), separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def harvest_native_helper_attempts(dispatch_id: str) -> list[dict[str, Any]]:
    path = native_helper_attempts_path(dispatch_id)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def is_task_tool_name(tool_name: str) -> bool:
    return tool_name.strip().lower() in {"task"}


def _native_helper_hook_entry(command: str) -> dict[str, Any]:
    return {
        "command": command,
        "failClosed": True,
        "timeout": _NATIVE_HELPER_HOOK_TIMEOUT_S,
        "matcher": "Task",
    }


def install_native_helper_hook(cursor_dir: Path, *, policy: NativeHelperPolicy) -> None:
    """Write preToolUse + subagentStart deny hook when enforcement is active."""
    command = native_helper_hook_command()
    path = cursor_dir / "hooks.json"
    if policy.enforcement != "enforced":
        _remove_native_helper_hook(path, command)
        return
    if not native_helper_hook_script_path().is_file():
        logger.warning("dispatch_home: native helper hook script absent")
        _remove_native_helper_hook(path, command)
        return
    data: dict[str, Any] = {"version": 1, "hooks": {}}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            data = loaded
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    entry = _native_helper_hook_entry(command)
    for key in ("preToolUse", "subagentStart"):
        items = hooks.get(key)
        if not isinstance(items, list):
            items = []
        replaced = False
        updated: list[Any] = []
        for item in items:
            if isinstance(item, dict) and item.get("command") == command:
                updated.append(entry)
                replaced = True
            else:
                updated.append(item)
        if not replaced:
            updated.append(entry)
        hooks[key] = updated
    data["hooks"] = hooks
    data["version"] = data.get("version") or 1
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    policy_path = cursor_dir / "native_helper_policy.json"
    policy_path.write_text(
        json.dumps(native_helper_policy_record(policy), indent=2) + "\n",
        encoding="utf-8",
    )


def _remove_native_helper_hook(path: Path, command: str) -> None:
    if not path.is_file():
        return
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(loaded, dict):
        return
    hooks = loaded.get("hooks")
    if not isinstance(hooks, dict):
        return
    changed = False
    for key in ("preToolUse", "subagentStart"):
        items = hooks.get(key)
        if not isinstance(items, list):
            continue
        kept = [
            item
            for item in items
            if not (isinstance(item, dict) and item.get("command") == command)
        ]
        if len(kept) != len(items):
            hooks[key] = kept
            changed = True
    if not changed:
        return
    loaded["hooks"] = hooks
    path.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "GIW_NATIVE_HELPER_ALLOWLIST",
    "NativeHelperPolicy",
    "append_native_helper_attempt",
    "harvest_native_helper_attempts",
    "install_native_helper_hook",
    "is_task_tool_name",
    "native_helper_hook_command",
    "native_helper_policy_record",
    "resolve_native_helper_enforcement",
    "resolve_native_helper_policy",
]
