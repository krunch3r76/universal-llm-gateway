"""Structured result + dual-plane pickup proof for guarded manage reexec."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Manage is outside the managed fleet; nothing auto-respawns it after quit.
RECOVERY_PATH = "seat tmux 0:0 re-drive per services_ws safe quit/start recipe"
DEFAULT_QUIT_TIMEOUT_S = 180.0
DEFAULT_BOOT_TIMEOUT_S = 120.0


@dataclass(slots=True)
class GuardedReexecResult:
    """Codeblind-disposition structured result for one guarded reexec attempt."""

    status: str  # refused|quit|start-failed|proof-satisfied|proof-failed|dry-run
    reason: str
    dry_run: bool
    checks: dict[str, Any] = field(default_factory=dict)
    whoami_before: dict[str, Any] | None = None
    whoami_after: dict[str, Any] | None = None
    target_ref: str | None = None
    code_version_ok: bool | None = None
    process_start_later_ok: bool | None = None
    executed: bool = False
    recovery_path: str = RECOVERY_PATH
    boot_timeout_s: float | None = None
    quit_timeout_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "executed": self.executed,
            "target_ref": self.target_ref,
            "recovery_path": self.recovery_path,
            "boot_timeout_s": self.boot_timeout_s,
            "quit_timeout_s": self.quit_timeout_s,
            "checks": self.checks,
            "whoami_before": self.whoami_before,
            "whoami_after": self.whoami_after,
            "proof": {
                "code_version_ok": self.code_version_ok,
                "process_start_later_ok": self.process_start_later_ok,
            },
        }


def parse_iso(value: Any) -> datetime | None:
    """Parse whoami process_start_time; treat missing/unknown as unprovable."""
    if not isinstance(value, str) or not value or value == "unknown":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def prove_pickup(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    target_ref: str,
) -> tuple[bool, bool, str]:
    """Two separate verdicts: code_version == target_ref, start_time later.

    New pid alone is not proof — both verdicts must pass for proof-satisfied.
    """
    after_ver = after.get("code_version")
    version_ok = isinstance(after_ver, str) and after_ver == target_ref
    before_ts = parse_iso(before.get("process_start_time"))
    after_ts = parse_iso(after.get("process_start_time"))
    start_ok = (
        before_ts is not None and after_ts is not None and after_ts > before_ts
    )
    if version_ok and start_ok:
        return True, True, "proof_satisfied"
    parts: list[str] = []
    if not version_ok:
        parts.append(
            f"code_version_mismatch: observed={after_ver!r} target={target_ref!r}"
        )
    if not start_ok:
        parts.append(
            "process_start_time_not_later: "
            f"before={before.get('process_start_time')!r} "
            f"after={after.get('process_start_time')!r}"
        )
    return version_ok, start_ok, "; ".join(parts)
