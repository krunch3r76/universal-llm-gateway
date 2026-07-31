"""Structure-aware relay body clamp — judgment in bus, documents in sidecar."""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    RELAY_JUDGMENT_CLAMP_FIELDS,
    CloseoutRelayPayload,
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


def _shrink_value(value: str, budget: int) -> str:
    if len(value) <= budget:
        return value
    if budget <= 1:
        return "…"
    return value[: budget - 1] + "…"


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
    """Rewrite §2 header/table status to match amended payload.status."""
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
    )


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
    """Run honesty amend, overclaim clamp, reporting tier, optional confer fence, then clamp."""
    amended = amend_effects_underclaim(
        payload.body,
        wrapper_text=wrapper_text,
        status=payload.status,
        source=payload.source,
    )
    sidecar_read_succeeded = payload.source in ("section2_sidecar", "section2_bus")
    overclaim = amend_completion_overclaim(
        amended.body,
        wrapper_text=wrapper_text,
        status=amended.status,
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
        status=overclaim.status,
        source=overclaim.source,
        caller_auditable=caller_auditable,
        model_substitution=model_substitution,
    )
    if guard_uris:
        processed = apply_write_fence(
            reporting,
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )
    else:
        processed = reporting
    pointer = sidecar_workspaces_ref(dispatch_id) if dispatch_id else None
    clamped_body, was_clamped = clamp_relay_body(processed.body, pointer=pointer)
    synced = _sync_payload_status(
        CloseoutRelayPayload(
            body=clamped_body,
            status=processed.status,
            source=processed.source,
            body_full=processed.body if was_clamped else None,
            clamped=was_clamped,
        )
    )
    return synced


__all__ = ["RELAY_BODY_TARGET_CHARS", "clamp_relay_body", "finalize_relay_payload"]
