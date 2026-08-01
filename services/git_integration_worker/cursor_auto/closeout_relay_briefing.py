"""Structure-aware relay body clamp — judgment in bus, documents in sidecar."""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    RELAY_JUDGMENT_CLAMP_FIELDS,
    CloseoutRelayPayload,
    RELAY_PARSE_FAILED_STATUS,
    _VALID_WRAPPER_STATUSES,
    is_degenerate_fence_cell,
    looks_fenced,
    merge_relay_notes,
    relay_parse_failure_detected,
    sanitize_relay_cell,
    status_from_section2,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fence import (
    apply_write_fence,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_status,
)
from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
    amend_completion_overclaim,
    amend_effects_underclaim,
)
from services.git_integration_worker.cursor_auto.closeout_relay_reporting import (
    amend_reporting_field_gaps,
    stamp_model_actual,
)
from services.git_integration_worker.cursor_auto.relay_trust import (
    enforce_synthesized_partial,
    synthesized_relay_note,
)
from services.git_integration_worker.cursor_auto.episode_residue import (
    residue_for_closeout,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)

RELAY_BODY_TARGET_CHARS = 2_000

_FULL_CLOSEOUT_PREFIX = "\n\nFull closeout: "
_PRESERVED_HEADER_PREFIXES = (
    "type:",
    "status:",
    "dispatch_id:",
    "model:",
    "request_turn:",
    "meta:",
)
_TABLE_ROW_RE = re.compile(r"^\|\s*(?P<field>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\|\s*[-:]+\s*\|")
_TABLE_HEADER_FIELDS = frozenset({"field"})


def _is_table_header_row(field: str, value: str) -> bool:
    return field.strip().casefold() in _TABLE_HEADER_FIELDS and value.strip().casefold() == "value"


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def _split_body(body: str) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    lines = body.splitlines()
    pre_table: list[str] = []
    table_rows: list[tuple[str, str]] = []
    post_table: list[str] = []
    phase = "pre"
    for line in lines:
        if phase == "pre":
            if _is_table_row(line) and not _TABLE_SEP_RE.match(line.strip()):
                phase = "table"
                match = _TABLE_ROW_RE.match(line)
                if match:
                    field = match.group("field").strip()
                    value = match.group("value")
                    if not _is_table_header_row(field, value):
                        table_rows.append((field, value))
                continue
            pre_table.append(line)
            continue
        if phase == "table":
            if not _is_table_row(line):
                phase = "post"
                post_table.append(line)
                continue
            if _TABLE_SEP_RE.match(line.strip()):
                continue
            match = _TABLE_ROW_RE.match(line)
            if match:
                field = match.group("field").strip()
                value = match.group("value")
                if not _is_table_header_row(field, value):
                    table_rows.append((field, value))
            continue
        post_table.append(line)
    return pre_table, table_rows, post_table


def _render_table(rows: list[tuple[str, str]]) -> list[str]:
    if not rows:
        return []
    rendered = ["| Field | Value |", "|---|---|"]
    for field, value in rows:
        rendered.append(f"| {field} | {value} |")
    return rendered


def _allocate_cell_budgets(fields: list[str], budget: int) -> dict[str, int]:
    """Weight judgment relay cells 3× reporting cells within *budget*."""
    if not fields or budget <= 0:
        return {}
    weights = [
        3 if field.casefold() in {f.casefold() for f in RELAY_JUDGMENT_CLAMP_FIELDS} else 1
        for field in fields
    ]
    total_weight = sum(weights)
    floor = 80 if any(w == 3 for w in weights) else 40
    shares = [max(budget * weight // total_weight, floor if weight == 3 else 40) for weight in weights]
    while sum(shares) > budget:
        idx = max(range(len(shares)), key=lambda i: shares[i])
        if shares[idx] <= 20:
            break
        shares[idx] -= 5
    return dict(zip(fields, shares, strict=True))


def _shrink_value(value: str, budget: int, *, pointer: str | None = None) -> str:
    if len(value) <= budget:
        return value
    if budget <= 1:
        return "…"
    if looks_fenced(value):
        if pointer:
            return sanitize_relay_cell(value, pointer)
        return "truncated: … (fenced content omitted)"
    shrunk = value[: budget - 1] + "…"
    if is_degenerate_fence_cell(shrunk):
        if pointer and "://" in pointer:
            return f"truncated: … (full text: {pointer})"
        if pointer:
            return f"truncated: … (full: {pointer})"
        return "truncated: …"
    return shrunk


def _shrink_pre_table_prose(pre_table: list[str], budget: int) -> list[str]:
    if budget <= 0:
        preserved: list[str] = []
        for line in pre_table:
            if any(line.strip().lower().startswith(prefix) for prefix in _PRESERVED_HEADER_PREFIXES):
                preserved.append(line)
        return preserved
    result: list[str] = []
    remaining = budget
    for line in pre_table:
        lower = line.strip().lower()
        if any(lower.startswith(prefix) for prefix in _PRESERVED_HEADER_PREFIXES):
            result.append(line)
            remaining -= len(line) + 1
            continue
        if remaining <= 0:
            continue
        if len(line) + 1 <= remaining:
            result.append(line)
            remaining -= len(line) + 1
        else:
            result.append(_shrink_value(line, remaining))
            remaining = 0
    return result


def clamp_relay_body(body: str, *, pointer: str | None) -> tuple[str, bool]:
    """Clamp relay CLOSEOUT bodies while preserving parser-survival structure."""
    if len(body) <= RELAY_BODY_TARGET_CHARS:
        return body, False
    if _FULL_CLOSEOUT_PREFIX.strip() in body:
        return body, False

    pre_table, table_rows, post_table = _split_body(body)
    post_len = sum(len(line) + 1 for line in post_table)
    pre_len = sum(len(line) + 1 for line in pre_table)
    table_shell = _render_table([(field, "") for field, _ in table_rows])
    shell_len = pre_len + sum(len(line) + 1 for line in table_shell) + post_len
    pointer_suffix = f"{_FULL_CLOSEOUT_PREFIX}{pointer}" if pointer else ""
    budget = RELAY_BODY_TARGET_CHARS - shell_len - len(pointer_suffix)
    if budget < 0:
        budget = 0

    value_budgets = _allocate_cell_budgets([field for field, _ in table_rows], budget)
    shrunk_rows = [
        (
            field,
            _shrink_value(
                value,
                value_budgets.get(field, max(budget // max(len(table_rows), 1), 40)),
                pointer=pointer,
            ),
        )
        for field, value in table_rows
    ]
    candidate_lines = pre_table + _render_table(shrunk_rows) + post_table
    candidate = "\n".join(candidate_lines)
    if len(candidate) > RELAY_BODY_TARGET_CHARS:
        prose_budget = RELAY_BODY_TARGET_CHARS - (
            sum(len(line) + 1 for line in _render_table(shrunk_rows))
            + post_len
            + len(pointer_suffix)
        )
        pre_table = _shrink_pre_table_prose(pre_table, max(prose_budget, 0))
        candidate = "\n".join(pre_table + _render_table(shrunk_rows) + post_table)

    if len(candidate) <= RELAY_BODY_TARGET_CHARS:
        if pointer:
            candidate += f"{_FULL_CLOSEOUT_PREFIX}{pointer}"
        return candidate, True

    if pointer:
        candidate = candidate[: RELAY_BODY_TARGET_CHARS - len(pointer_suffix)].rstrip()
        candidate += f"{_FULL_CLOSEOUT_PREFIX}{pointer}"
    else:
        candidate = candidate[:RELAY_BODY_TARGET_CHARS].rstrip()
    return candidate, True


def _sync_payload_status(payload: CloseoutRelayPayload) -> CloseoutRelayPayload:
    """Rewrite §2 header/table status to match payload.status (authored verdict sync)."""
    body_status = extract_status(payload.body) or status_from_section2(payload.body)
    if body_status == payload.status:
        return payload
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _rewrite_relay_status,
    )

    synced_body = _rewrite_relay_status(payload.body, payload.status)
    return CloseoutRelayPayload(
        body=synced_body,
        status=payload.status,
        source=payload.source,
        body_full=payload.body_full,
        clamped=payload.clamped,
        relay_note=payload.relay_note,
        deployment_state=payload.deployment_state,
    )


def _has_authored_executor_verdict(*, source: str, authored_status: str) -> bool:
    """True when the relay path carries an executor-authored §2 status verdict."""
    if source not in ("section2_sidecar", "section2_bus"):
        return False
    return authored_status.strip().lower() in _VALID_WRAPPER_STATUSES


def _deployment_state_from_wrapper(wrapper_text: str | None) -> str | None:
    """Summarize landed-not-live propagation residue without mutating executor status."""
    if not wrapper_text:
        return None
    block = residue_for_closeout(wrapper_text)
    if block is None:
        return None
    action_lines = [
        line.lstrip("- ").strip()
        for line in block.splitlines()
        if line.strip().startswith("- ")
    ]
    count = len(action_lines)
    if count == 0:
        return None
    noun = "path" if count == 1 else "paths"
    return f"{count} landed-not-live {noun} — see RESIDUE block"


def _append_relay_metadata_lines(
    body: str,
    *,
    relay_note: str | None,
    deployment_state: str | None,
) -> str:
    """Inject relay-owned facts adjacent to the authored status header."""
    if not relay_note and not deployment_state:
        return body
    insert_lines: list[str] = []
    if relay_note:
        insert_lines.append(f"relay_note: {relay_note}")
    if deployment_state:
        insert_lines.append(f"deployment_state: {deployment_state}")
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is None:
        return body + "\n" + "\n".join(insert_lines) + "\n"
    insert_at = status_match.end()
    block = "\n" + "\n".join(insert_lines)
    return f"{body[:insert_at]}{block}{body[insert_at:]}"


def _merge_payload_notes(*payloads: CloseoutRelayPayload) -> str | None:
    return merge_relay_notes(*(item.relay_note for item in payloads if item.relay_note))


def finalize_relay_payload(
    payload: CloseoutRelayPayload,
    *,
    wrapper_text: str | None,
    guard_uris: frozenset[str] | None = None,
    dispatch_id: str = "",
    caller_auditable: bool = False,
    requested_model: str | None = None,
    resolved_model: str | None = None,
    sidecar_read_failed_uri: str | None = None,
) -> CloseoutRelayPayload:
    """Run honesty amend, reporting tier, optional confer fence, then clamp — status immutable."""
    authored_status = payload.status.strip().lower()
    amended = amend_effects_underclaim(
        payload.body,
        wrapper_text=wrapper_text,
        status=authored_status,
        source=payload.source,
    )
    sidecar_read_succeeded = payload.source in ("section2_sidecar", "section2_bus")
    overclaim = amend_completion_overclaim(
        amended.body,
        wrapper_text=wrapper_text,
        status=authored_status,
        source=amended.source,
        dispatch_id=dispatch_id,
        sidecar_read_succeeded=sidecar_read_succeeded,
        sidecar_read_failed_uri=sidecar_read_failed_uri,
    )
    model_substitution = bool(
        requested_model
        and resolved_model
        and requested_model.strip().casefold() != resolved_model.strip().casefold()
    )
    stamped = overclaim.body
    if model_substitution and requested_model and resolved_model:
        stamped = stamp_model_actual(
            stamped,
            requested_model=requested_model,
            resolved_model=resolved_model,
        )
    reporting = amend_reporting_field_gaps(
        stamped,
        status=authored_status,
        source=overclaim.source,
        caller_auditable=caller_auditable,
        model_substitution=model_substitution,
    )
    relay_note = merge_relay_notes(
        _merge_payload_notes(amended, overclaim, reporting),
        synthesized_relay_note(
            closeout_source=reporting.source,
            status=authored_status,
        ),
    )
    deployment_state = _deployment_state_from_wrapper(wrapper_text)
    processed = CloseoutRelayPayload(
        body=reporting.body,
        status=authored_status,
        source=reporting.source,
        relay_note=relay_note,
        deployment_state=deployment_state,
    )
    if guard_uris:
        processed = apply_write_fence(
            processed,
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )
    pointer = sidecar_workspaces_ref(dispatch_id) if dispatch_id else None
    clamped_body, was_clamped = clamp_relay_body(processed.body, pointer=pointer)
    body_with_meta = _append_relay_metadata_lines(
        clamped_body,
        relay_note=processed.relay_note,
        deployment_state=processed.deployment_state,
    )
    final_status = authored_status
    if relay_parse_failure_detected(body_with_meta) and not _has_authored_executor_verdict(
        source=processed.source,
        authored_status=authored_status,
    ):
        final_status = RELAY_PARSE_FAILED_STATUS
    synced = _sync_payload_status(
        CloseoutRelayPayload(
            body=body_with_meta,
            status=final_status,
            source=processed.source,
            body_full=processed.body if was_clamped else None,
            clamped=was_clamped,
            relay_note=processed.relay_note,
            deployment_state=processed.deployment_state,
        )
    )
    enforce_synthesized_partial(synced.status, closeout_source=synced.source)
    return synced


__all__ = ["RELAY_BODY_TARGET_CHARS", "clamp_relay_body", "finalize_relay_payload"]
