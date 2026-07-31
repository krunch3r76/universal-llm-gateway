"""Append fleet-idle gate attestation to trigger wake prompts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from implement_admission.closeout_helpers import cortex_files_root

from .fleet_idle import FleetIdleSnapshot, read_fleet_idle_memoized
from .models import PREDICATE_FLEET_IDLE, TriggerRow
from .pass_snapshot_publish import SNAPSHOT_URI

_BLOCK_HEADER = "## FLEET GATE ATTESTATION"


def load_prompt_body(prompt_uri: str) -> str:
    """Load canonical prompt text from a ``cortex://`` URI."""
    if not prompt_uri.startswith("cortex://"):
        raise ValueError("prompt_uri must use cortex:// scheme")
    rel = prompt_uri.removeprefix("cortex://").lstrip("/")
    path = (cortex_files_root() / rel).resolve()
    root = cortex_files_root().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"prompt_uri {prompt_uri!r} escapes CORTEX_FILES_ROOT") from exc
    if not path.is_file():
        raise ValueError(f"prompt_uri not found: {prompt_uri!r} -> {path}")
    return path.read_text(encoding="utf-8")


def _grace_s(row: TriggerRow) -> int:
    if not row.predicate_args:
        return 0
    args = json.loads(row.predicate_args)
    return max(0, int(args.get("grace_s", 0)))


def render_attestation_block(
    row: TriggerRow,
    *,
    snapshot: FleetIdleSnapshot | None = None,
    attested_at: datetime | None = None,
) -> str:
    """Render the attestation footer for one trigger row."""
    when = attested_at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    stamp = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        _BLOCK_HEADER,
        "",
        f"prompt_uri: {row.prompt_uri}",
    ]
    if row.predicate == PREDICATE_FLEET_IDLE:
        snap = snapshot if snapshot is not None else read_fleet_idle_memoized()
        lines.extend(
            [
                "fleet_gate_applied: true",
                f"verdict: {snap.verdict.value}",
                f"dispatch_idle: {str(snap.dispatch_idle).lower()}",
                f"tick_empty: {str(snap.tick_empty).lower()}",
                f"cursor_auto_idle: {str(snap.cursor_auto_idle).lower()}",
                f"cdp_lane_idle: {str(snap.cdp_lane_idle).lower()}",
                f"grace_s: {_grace_s(row)}",
                f"attested_at_utc: {stamp}",
                f"pass_snapshot_uri: {SNAPSHOT_URI}",
                (
                    "note: pre-wake observation without a lease — life fs read of "
                    "pass_snapshot_uri; not agent_bus.request"
                ),
            ]
        )
    else:
        lines.extend(
            [
                "fleet_gate_applied: false",
                "note: no fleet_idle predicate on this trigger row",
                f"attested_at_utc: {stamp}",
            ]
        )
    return "\n".join(lines)


def compose_attested_prompt(
    row: TriggerRow,
    *,
    attested_at: datetime | None = None,
) -> str:
    """Return the scheduled prompt body with a fleet gate attestation footer."""
    body = load_prompt_body(row.prompt_uri)
    block = render_attestation_block(row, attested_at=attested_at)
    if body:
        return f"{body.rstrip()}\n\n{block}"
    return block
