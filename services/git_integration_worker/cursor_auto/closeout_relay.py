"""Select operator-facing CLOSEOUT payload for cursor-auto relay.

Proxy §2 wants ``ac_verdict`` / ``deltas_to_spec`` etc. GIW's cursor-sdk bus
turn is a machine capture/manifest that shares the name "closeout". Prefer an
authored §2 body (usually the repo sidecar) when present; otherwise synthesize
§2 from the wrapper manifest so the operator never receives raw JSON alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
    finalize_relay_payload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    RELAY_EFFECTS_MAX_ITEMS,
    CloseoutRelayPayload,
    _as_str_list,
    _order_preserving_dedup,
    _table_cell,
    is_wrapper_manifest,
    looks_section2,
    relay_parse_miss_cell,
    strip_machine_tail,
    wrapper_status,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex import (
    run_cortex_scan,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fence import (
    extract_fence_exception_lines,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    status_from_section2,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_uri import (
    extract_cortex_uris_from_wrapper,
)
from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
    machine_write_uris,
)
from services.git_integration_worker.cursor_auto.closeout_relay_project import (
    project_section2_table,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)

_SIDECAR_REL_DIR = "tmp/reviews/closeouts"


def _append_fence_exception_lines(projected: str, prose: str) -> str:
    extras = extract_fence_exception_lines(prose)
    if not extras:
        return projected
    return projected.rstrip() + "\n\n" + "\n".join(extras) + "\n"


def _evidence_cell_from_parts(
    artifact_paths: list[str],
    deviations: list[str],
    capture_status: object,
    work_outcome: object = None,
    usage: object = None,
    usage_capture_status: object = None,
) -> str:
    evidence_parts: list[str] = []
    if artifact_paths:
        evidence_parts.append("artifact_paths: " + ", ".join(artifact_paths))
    if deviations:
        evidence_parts.append("deviations: " + "; ".join(deviations))
    if work_outcome is not None:
        evidence_parts.append(f"work_outcome={work_outcome}")
    if capture_status is not None:
        evidence_parts.append(f"capture_status={capture_status}")
    if usage is not None and isinstance(usage, dict):
        total = usage.get("total_tokens")
        if total is not None:
            evidence_parts.append(f"usage_total_tokens={total}")
    if usage_capture_status is not None:
        evidence_parts.append(f"usage_capture_status={usage_capture_status}")
    return "; ".join(evidence_parts) if evidence_parts else "none"


def synthesize_section2(
    *,
    wrapper_text: str | None,
    sidecar_text: str | None,
    dispatch_id: str,
) -> str | None:
    """Build operator-facing §2 markdown from a machine wrapper manifest."""
    if not wrapper_text or not is_wrapper_manifest(wrapper_text):
        return None
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    status = data.get("status", "partial")
    files_created = _as_str_list(data.get("files_created"))
    files_modified = _as_str_list(data.get("files_modified"))
    files_deleted = _as_str_list(data.get("files_deleted"))
    files_offgit_produced = _as_str_list(data.get("files_offgit_produced"))
    effects = _as_str_list(data.get("effects"))
    deviations = _as_str_list(data.get("deviations"))
    capture_status = data.get("capture_status")
    work_outcome = data.get("work_outcome")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    usage_capture_status = data.get("usage_capture_status")
    evidence_uris = data.get("evidence_uris")
    artifact_paths: list[str] = []
    if isinstance(evidence_uris, dict):
        artifact_paths = _as_str_list(evidence_uris.get("artifact_paths"))

    effects_union = machine_write_uris(wrapper_text)
    if not effects_union:
        effects_union = _order_preserving_dedup(
            effects,
            files_created,
            files_modified,
            files_deleted,
            files_offgit_produced,
        )

    provenance = (
        sidecar_workspaces_ref(dispatch_id)
        if sidecar_text and sidecar_text.strip() and dispatch_id
        else (
            f"repo sidecar for {dispatch_id}"
            if sidecar_text and sidecar_text.strip()
            else "machine wrapper manifest"
        )
    )
    sidecar_prose = strip_machine_tail(sidecar_text).strip() if sidecar_text else ""

    if sidecar_prose:
        projected, projected_status = project_section2_table(
            sidecar_prose,
            provenance=provenance,
            fallback_status=str(status),
        )
        if effects_union:
            pointer = sidecar_workspaces_ref(dispatch_id) if dispatch_id else provenance
            if len(effects_union) > RELAY_EFFECTS_MAX_ITEMS:
                shown = effects_union[:RELAY_EFFECTS_MAX_ITEMS]
                extra = len(effects_union) - RELAY_EFFECTS_MAX_ITEMS
                effects_cell = "<br>".join(f"- {item}" for item in shown)
                effects_cell += f"<br>+{extra} more (see {pointer})"
            else:
                effects_cell = "<br>".join(f"- {item}" for item in effects_union)
            projected = re.sub(
                r"(?im)^\|\s+effects\s+\|\s+.*?\s+\|",
                f"| effects | {_table_cell(effects_cell)} |",
                projected,
                count=1,
            )
        if evidence_cell := _evidence_cell_from_parts(
            artifact_paths,
            deviations,
            capture_status,
            work_outcome,
            usage,
            usage_capture_status,
        ):
            projected = re.sub(
                r"(?im)^\|\s+evidence\s+\|\s+.*?\s+\|",
                f"| evidence | {_table_cell(evidence_cell)} |",
                projected,
                count=1,
            )
        return projected

    ac_verdict = relay_parse_miss_cell("ac_verdict", provenance)
    deltas_to_spec = relay_parse_miss_cell("deltas_to_spec", provenance)
    decisions_taken = relay_parse_miss_cell("decisions_taken", provenance)
    next_cell = relay_parse_miss_cell("next", provenance)
    open_forks = relay_parse_miss_cell("open forks", provenance)

    if effects_union:
        pointer = sidecar_workspaces_ref(dispatch_id) if dispatch_id else provenance
        if len(effects_union) > RELAY_EFFECTS_MAX_ITEMS:
            shown = effects_union[:RELAY_EFFECTS_MAX_ITEMS]
            extra = len(effects_union) - RELAY_EFFECTS_MAX_ITEMS
            effects_cell = "<br>".join(f"- {item}" for item in shown)
            effects_cell += f"<br>+{extra} more (see {pointer})"
        else:
            effects_cell = "<br>".join(f"- {item}" for item in effects_union)
    else:
        effects_cell = (
            f"none captured — capture_status={capture_status}; "
            'per §4.7 a schema-only read of "none" is not authority'
        )

    evidence_cell = _evidence_cell_from_parts(
        artifact_paths,
        deviations,
        capture_status,
        work_outcome,
        usage,
        usage_capture_status,
    )

    rows = (
        ("status", str(status)),
        ("ac_verdict", ac_verdict),
        ("deltas_to_spec", deltas_to_spec),
        ("decisions_taken", decisions_taken),
        ("effects", effects_cell),
        ("evidence", evidence_cell),
        ("next", next_cell),
        ("open forks", open_forks),
    )
    lines = [
        "TYPE: CLOSEOUT",
        f"source_ref: {provenance}",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for field, value in rows:
        lines.append(f"| {field} | {_table_cell(value)} |")
    return "\n".join(lines)


def ledger_status_to_closeout(terminal_status: str) -> str:
    """Map ledger terminal status → operator CLOSEOUT status line."""
    if terminal_status == "completed":
        return "complete"
    if terminal_status == "failed":
        return "blocked"
    return "partial"


def read_repo_closeout_sidecar(
    dispatch_id: str,
    *,
    source_repo: Path | None = None,
) -> str | None:
    """Read ``tmp/reviews/closeouts/{dispatch_id}.md`` when present."""
    root = source_repo if source_repo is not None else load_config().source_repo
    path = root / _SIDECAR_REL_DIR / f"{dispatch_id}.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text or None


def select_closeout_relay_payload(
    *,
    sdk_body: str | None,
    sidecar_text: str | None,
    ledger_status: str,
    dispatch_id: str = "",
    cortex_root: Path | None = None,
    guard_uris: frozenset[str] | None = None,
    caller_auditable: bool = False,
    requested_model: str | None = None,
    resolved_model: str | None = None,
) -> CloseoutRelayPayload:
    """Prefer authored §2; synthesize from wrapper manifest when absent.

    Selection order:
    1. Repo sidecar with §2 markers (machine tail stripped)
    2. Bus body when it itself looks like §2 (rare)
    3. Cortex promote or field-fill from wrapper ``cortex://`` URIs
    4. Synthesized §2 from wrapper manifest JSON
    5. Non-manifest bus body (plain prose)
    6. Empty placeholder

    All exits pass through ``_finalize`` for machine-over-prose honesty amend
    and optional confer write-fence when *guard_uris* is supplied.

    ``cortex_root`` defaults to ``cortex_files_root()`` when omitted. URIs with
    absolute paths, ``..`` segments, or targets outside ``cortex_root`` are
    skipped without raising.     Cortex promote requires §2 markers and a dispatch
    bind. Relayed status follows the amended §2 body after ``finalize_relay_payload``.
    """
    fallback_status = ledger_status_to_closeout(ledger_status)
    wrapper_text = sdk_body
    cortex_uris = extract_cortex_uris_from_wrapper(wrapper_text or "")

    bind = {
        "wrapper_text": wrapper_text,
        "guard_uris": guard_uris,
        "dispatch_id": dispatch_id,
        "caller_auditable": caller_auditable,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "sidecar_read_failed_uri": None,
    }

    if sidecar_text:
        prose = strip_machine_tail(sidecar_text)
        if looks_section2(prose):
            provenance = sidecar_workspaces_ref(dispatch_id) if dispatch_id else "repo sidecar"
            projected, status = project_section2_table(
                prose,
                provenance=provenance,
                fallback_status=fallback_status,
            )
            projected = _append_fence_exception_lines(projected, prose)
            return finalize_relay_payload(
                CloseoutRelayPayload(
                    body=projected,
                    status=status,
                    source="section2_sidecar",
                ),
                **bind,
            )

    if sdk_body and looks_section2(sdk_body) and not is_wrapper_manifest(sdk_body):
        prose = strip_machine_tail(sdk_body)
        provenance = sidecar_workspaces_ref(dispatch_id) if dispatch_id else "bus §2 body"
        projected, status = project_section2_table(
            prose,
            provenance=provenance,
            fallback_status=fallback_status,
        )
        projected = _append_fence_exception_lines(projected, prose)
        return finalize_relay_payload(
            CloseoutRelayPayload(
                body=projected,
                status=status,
                source="section2_bus",
            ),
            **bind,
        )

    root = cortex_root if cortex_root is not None else cortex_files_root()
    cortex_payload = run_cortex_scan(
        wrapper_text=sdk_body or "",
        dispatch_id=dispatch_id,
        cortex_root=root,
        fallback_status=fallback_status,
    )
    if cortex_payload is not None:
        return finalize_relay_payload(
            cortex_payload,
            **bind,
        )

    if cortex_uris:
        bind["sidecar_read_failed_uri"] = cortex_uris[0]

    synthesized = synthesize_section2(
        wrapper_text=sdk_body,
        sidecar_text=sidecar_text,
        dispatch_id=dispatch_id,
    )
    if synthesized is not None:
        source = "section2_synthesized"
        raw_status = wrapper_status(sdk_body or "") or fallback_status
        return finalize_relay_payload(
            CloseoutRelayPayload(
                body=synthesized,
                status=raw_status,
                source=source,
            ),
            **bind,
        )

    if sdk_body and sdk_body.strip():
        return finalize_relay_payload(
            CloseoutRelayPayload(
                body=sdk_body.strip(),
                status=fallback_status,
                source="wrapper",
            ),
            **bind,
        )

    return finalize_relay_payload(
        CloseoutRelayPayload(
            body="(no cursor-sdk closeout body captured)",
            status=fallback_status,
            source="empty",
        ),
        **bind,
    )


__all__ = [
    "CloseoutRelayPayload",
    "is_wrapper_manifest",
    "ledger_status_to_closeout",
    "looks_section2",
    "machine_write_uris",
    "read_repo_closeout_sidecar",
    "select_closeout_relay_payload",
    "status_from_section2",
    "strip_machine_tail",
    "synthesize_section2",
    "wrapper_status",
]
