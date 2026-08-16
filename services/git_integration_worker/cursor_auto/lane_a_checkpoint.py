"""Lane-A authored-path attribution and tree residue derivation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.spec import NO_RUN_DEGRADED_REASONS

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline,
    changed_paths,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    observed_lane_git_refs,
    paths_in_commit,
    resolve_git_head,
)
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

_TREE_RESIDUE_RE = re.compile(r"(?im)^tree_residue:\s*(\d+)\b")
_CHECKPOINT_LINE_RE = re.compile(r"(?im)^checkpoint:\s*(.+)$")
_BOLD_CHECKPOINT_RE = re.compile(r"(?im)^\*\*checkpoint:\*\*\s*(.+)$")
_CHECKPOINT_CLAIM_LINE_RE = re.compile(r"(?im)^checkpoint_claim:\s*(.+)$")
_BOLD_CHECKPOINT_CLAIM_RE = re.compile(
    r"(?im)^\*\*checkpoint_claim:\*\*\s*(.+)$"
)
_CORTEX_URI_PREFIX = "cortex://"
_UNHASHABLE_CORTEX_DEFERRED = (
    "deferred: cortex durable write could not be rehashed"
)
_BASELINE_UNAVAILABLE = (
    "baseline_unavailable: no admit baseline recorded for this dispatch"
)
_DEGRADED_SUMMARY_RE = re.compile(r"\(degraded:\s*([^)]+)\)")
_TOOL_CALLS_SUMMARY_RE = re.compile(r":\s*(\d+)\s+tool calls\b")


@dataclass(frozen=True)
class AuthoredPathProbe:
    """Probe answer: dispatch baseline → exact authored-path set."""

    exact_at_dispatch: bool
    covers_nested_cursor_sdk: bool
    covers_attended_composer: bool
    registration_mechanism: str
    detail: str


@dataclass(frozen=True)
class TreeResidueSnapshot:
    """Derived dirty-tree residue vs an episode authored-path set."""

    count: int
    authored_paths: tuple[str, ...]


def probe_authored_path_baseline() -> AuthoredPathProbe:
    """Record the code-verified probe answer for lane-A baseline attribution."""
    return AuthoredPathProbe(
        exact_at_dispatch=True,
        covers_nested_cursor_sdk=True,
        covers_attended_composer=True,
        registration_mechanism=(
            "Lane B: Cursor ``afterFileEdit`` hook → "
            "``scripts/cursor/register_seat_write.py`` → "
            "``SeatWriteLedger.register_paths`` (SQLite at "
            "``DATA_DIR/seat-write-ledger.db``). Arc opened on ``sessionStart``, "
            "closed on ``sessionEnd``. GIW ``lane_b_sweeper_loop`` commits "
            "closed-arc quiescent registered paths only."
        ),
        detail=(
            "Per-dispatch admit ``wt_baseline`` yields exact authored paths for "
            "cursor-sdk episodes (lane A). Attended IDE/Composer writes register "
            "via the hook at edit time (lane B); ``tree_residue`` counts only "
            "dirty paths in neither set — registration gaps, not WIP to respect."
        ),
    )


def degraded_reason_from_closeout_wrapper(wrapper_text: str | None) -> str | None:
    """Parse ``degraded_reason`` from an ImplementCloseout JSON body.

    Prefer the structured ``degraded_reason`` field; fall back to the legacy
    ``(degraded: …)`` token embedded in ``summary`` for older wrappers.
    """
    if not wrapper_text or not wrapper_text.strip():
        return None
    try:
        payload = json.loads(wrapper_text)
    except json.JSONDecodeError:
        return None
    structured = payload.get("degraded_reason")
    if isinstance(structured, str) and structured.strip():
        return structured.strip()
    summary = payload.get("summary")
    if not isinstance(summary, str):
        return None
    match = _DEGRADED_SUMMARY_RE.search(summary)
    if match is None:
        return None
    return match.group(1).strip()


def tool_call_count_from_closeout_wrapper(wrapper_text: str | None) -> int | None:
    """Parse tool-call count from an ImplementCloseout JSON body.

    Prefer structured ``tool_call_count``; fall back to the summary line.
    """
    if not wrapper_text or not wrapper_text.strip():
        return None
    try:
        payload = json.loads(wrapper_text)
    except json.JSONDecodeError:
        return None
    structured = payload.get("tool_call_count")
    if isinstance(structured, int):
        return structured
    summary = payload.get("summary")
    if not isinstance(summary, str):
        return None
    match = _TOOL_CALLS_SUMMARY_RE.search(summary)
    if match is None:
        return None
    return int(match.group(1))


def null_run_suppresses_lane_a_authorship(
    *,
    degraded_reason: str | None,
    wrapper_text: str | None = None,
) -> bool:
    """True when ``NO_RUN_DEGRADED_REASONS`` means zero dispatch authorship.

    ``empty_terminal_output`` may co-occur with tool calls that wrote files — only
    suppress when the wrapper reports zero tool calls.
    """
    if degraded_reason is None:
        return False
    if degraded_reason in ("empty_assistant_turn", "zero_tool_calls"):
        return True
    if degraded_reason == "empty_terminal_output":
        tool_calls = tool_call_count_from_closeout_wrapper(wrapper_text)
        return tool_calls == 0
    return degraded_reason in NO_RUN_DEGRADED_REASONS


def authored_paths_for_dispatch(
    *,
    source_repo: Path,
    dispatch_id: str,
) -> tuple[str, ...]:
    """Return ledger-proven paths for one dispatch via baseline delta ∩ SeatWriteLedger."""
    baseline = CursorDispatchLedger.instance().read_wt_baseline(dispatch_id=dispatch_id)
    if baseline is None:
        return ()
    change_set, _deviations = changed_paths(source_repo, baseline)
    delta = tuple(
        dict.fromkeys((*change_set.created, *change_set.modified, *change_set.deleted))
    )
    registered = SeatWriteLedger.instance().registered_paths(
        source_repo=str(source_repo.resolve())
    )
    if not registered:
        return ()
    return tuple(p for p in delta if p in registered)


def derive_tree_residue(
    *,
    source_repo: Path,
    dispatch_id: str,
    baseline: dict[str, Any] | None = None,
) -> TreeResidueSnapshot:
    """Count dirty paths not attributable to lane-A or lane-B authorship."""
    if baseline is None:
        baseline = CursorDispatchLedger.instance().read_wt_baseline(
            dispatch_id=dispatch_id
        )
    if baseline is None:
        authored: set[str] = set()
    else:
        change_set, _deviations = changed_paths(source_repo, baseline)
        authored = set((*change_set.created, *change_set.modified, *change_set.deleted))
    registered = SeatWriteLedger.instance().registered_paths(
        source_repo=str(source_repo.resolve())
    )
    attributed = authored | set(registered)
    current = capture_wt_baseline(source_repo) or {}
    dirty_now = set(current.keys())
    residue_count = len(dirty_now - attributed)
    return TreeResidueSnapshot(
        count=residue_count,
        authored_paths=tuple(sorted(authored)),
    )


def inject_tree_residue_line(body: str, *, count: int) -> str:
    """Replace or append infrastructure-derived ``tree_residue:`` on a CLOSEOUT."""
    line = f"tree_residue: {count}"
    if _TREE_RESIDUE_RE.search(body):
        return _TREE_RESIDUE_RE.sub(line, body, count=1)
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is None:
        return body.rstrip() + f"\n{line}\n"
    insert_at = status_match.end()
    return f"{body[:insert_at]}\n{line}{body[insert_at:]}"


def _extract_checkpoint_field_value(
    text: str,
    field: str,
    *,
    include_control_lines: bool,
) -> str | None:
    """Return a normalized checkpoint disposition from §2 *field* prose."""
    from claude_bundles.lane_a_closeout_checkpoint import normalize_checkpoint_value

    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
        fenced_spans,
        in_fenced_span,
    )

    fenced = fenced_spans(text)
    if field == "checkpoint_claim":
        line_res = _CHECKPOINT_CLAIM_LINE_RE
        bold_res = (_BOLD_CHECKPOINT_CLAIM_RE,)
    else:
        line_res = _CHECKPOINT_LINE_RE if include_control_lines else None
        bold_res = (_BOLD_CHECKPOINT_RE,) if include_control_lines else ()

    if line_res is not None:
        for match in line_res.finditer(text):
            if not in_fenced_span(fenced, match.start()):
                return normalize_checkpoint_value(match.group(1))
    for bold_re in bold_res:
        for match in bold_re.finditer(text):
            if not in_fenced_span(fenced, match.start()):
                return normalize_checkpoint_value(match.group(1))

    section = extract_field_section(text, field)
    if section and section.strip():
        return normalize_checkpoint_value(section.strip())
    table_row_re = re.compile(
        rf"(?im)^\|\s*{re.escape(field)}\s*\|\s*(?P<value>.*?)\s*\|"
    )
    table_match = next(
        (
            match
            for match in table_row_re.finditer(text)
            if not in_fenced_span(fenced, match.start())
        ),
        None,
    )
    if table_match is not None:
        value = table_match.group("value").strip()
        if value and not value.casefold().startswith("relay could not locate"):
            return normalize_checkpoint_value(value)
    return None


def extract_checkpoint_claim(
    body: str,
    *,
    allow_legacy_control_line: bool = False,
) -> str | None:
    """Return the agent's §2 checkpoint claim — never the infra ``checkpoint:`` line.

    Primary surface is ``checkpoint_claim`` (§2 field / table / bold). When
    *allow_legacy_control_line* is True, fall back to legacy ``checkpoint`` §2
    prose and pre-relay ``checkpoint:`` control lines for in-flight sidecars.
    """
    claim = _extract_checkpoint_field_value(
        body or "",
        "checkpoint_claim",
        include_control_lines=True,
    )
    if claim is not None:
        return claim
    return _extract_checkpoint_field_value(
        body or "",
        "checkpoint",
        include_control_lines=allow_legacy_control_line,
    )


def extract_authored_checkpoint(body: str) -> str | None:
    """Backward-compatible alias — prefer :func:`extract_checkpoint_claim`."""
    return extract_checkpoint_claim(body, allow_legacy_control_line=True)


def _parse_wt_baseline(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _canonical_cortex_uri(raw: str) -> str | None:
    stripped = raw.strip()
    if not stripped.casefold().startswith(_CORTEX_URI_PREFIX):
        return None
    return f"{_CORTEX_URI_PREFIX}{stripped[len(_CORTEX_URI_PREFIX) :].lstrip('/')}"


def cortex_offgit_uris_from_wrapper(wrapper_text: str | None) -> tuple[str, ...]:
    """Return order-preserving canonical ``cortex://`` URIs from a closeout wrapper."""
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        machine_write_uris,
    )

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in machine_write_uris(wrapper_text):
        canonical = _canonical_cortex_uri(raw) if isinstance(raw, str) else None
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        ordered.append(canonical)
    return tuple(ordered)


def rehash_cortex_uri(*, uri: str, cortex_root: Path) -> str | None:
    """SHA-256 hex of the on-disk cortex file for *uri*, or None if unreadable."""
    canonical = _canonical_cortex_uri(uri)
    if canonical is None:
        return None
    rel = canonical[len(_CORTEX_URI_PREFIX) :].lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    path = cortex_root / rel
    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _authored_cortex_checkpoint(
    *,
    wrapper_text: str | None,
    cortex_root: Path | None,
) -> str | None:
    """Build ``authored_cortex:`` when wrapper names hashable cortex writes."""
    if wrapper_text is None or cortex_root is None:
        return None
    uris = cortex_offgit_uris_from_wrapper(wrapper_text)
    if not uris:
        return None
    pairs: list[str] = []
    for uri in uris:
        digest = rehash_cortex_uri(uri=uri, cortex_root=cortex_root)
        if digest is None:
            return _UNHASHABLE_CORTEX_DEFERRED
        pairs.append(f"{uri} {digest}")
    return "authored_cortex: " + "; ".join(pairs)


def compute_lane_a_checkpoint_value(
    *,
    source_repo: Path,
    dispatch_id: str,
    baseline: dict[str, Any] | None = None,
    wrapper_text: str | None = None,
    cortex_root: Path | None = None,
    degraded_reason: str | None = None,
) -> str:
    """Infrastructure-derived checkpoint disposition — not agent-typed.

    Git plane is senior: lane commits and dirty porcelain win over cortex-only
    durable writes. When both git signals are empty, closeout-time rehash of
    wrapper ``cortex://`` offgit URIs yields ``authored_cortex:`` (row 19).
    """
    if degraded_reason is None:
        degraded_reason = degraded_reason_from_closeout_wrapper(wrapper_text)
    if null_run_suppresses_lane_a_authorship(
        degraded_reason=degraded_reason,
        wrapper_text=wrapper_text,
    ):
        return "nothing_authored"
    if baseline is None:
        baseline = _parse_wt_baseline(
            CursorDispatchLedger.instance().read_wt_baseline(dispatch_id=dispatch_id)
        )
    authored = authored_paths_for_dispatch(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
    )
    admit_head = None
    if isinstance(baseline, dict):
        raw_admit = baseline.get("admit_head")
        if isinstance(raw_admit, str) and raw_admit.strip():
            admit_head = raw_admit.strip()
    closeout_head = resolve_git_head(source_repo)
    lane_refs = observed_lane_git_refs(
        source_repo,
        dispatch_id=dispatch_id,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    if lane_refs:
        sha = lane_refs[0]
        path_count = len(paths_in_commit(source_repo, sha))
        base = f"committed {sha} paths={path_count}"
        if authored:
            return f"{base} (+{len(authored)} pending)"
        return base
    if authored:
        return "deferred: authored paths not yet path-explicit committed"
    cortex_value = _authored_cortex_checkpoint(
        wrapper_text=wrapper_text,
        cortex_root=cortex_root,
    )
    if cortex_value is not None:
        return cortex_value
    if admit_head is None:
        return _BASELINE_UNAVAILABLE
    return "nothing_authored"


def inject_checkpoint_line(body: str, *, value: str) -> str:
    """Replace or append infrastructure-measured ``checkpoint:`` for lane-A gate."""
    line = f"checkpoint: {value}"
    if _CHECKPOINT_LINE_RE.search(body):
        return _CHECKPOINT_LINE_RE.sub(line, body, count=1)
    residue_match = _TREE_RESIDUE_RE.search(body)
    if residue_match:
        insert_at = residue_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is None:
        return body.rstrip() + f"\n{line}\n"
    insert_at = status_match.end()
    return f"{body[:insert_at]}\n{line}{body[insert_at:]}"


__all__ = [
    "AuthoredPathProbe",
    "TreeResidueSnapshot",
    "authored_paths_for_dispatch",
    "compute_lane_a_checkpoint_value",
    "degraded_reason_from_closeout_wrapper",
    "null_run_suppresses_lane_a_authorship",
    "tool_call_count_from_closeout_wrapper",
    "cortex_offgit_uris_from_wrapper",
    "derive_tree_residue",
    "extract_authored_checkpoint",
    "extract_checkpoint_claim",
    "inject_checkpoint_line",
    "inject_tree_residue_line",
    "probe_authored_path_baseline",
    "rehash_cortex_uri",
]
