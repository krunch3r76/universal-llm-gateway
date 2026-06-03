"""Collapse-set selection, idempotency guard, summary assert, supersede batch.

Pure helpers (no I/O) for collapse-set logic and summary-input construction.
Async helpers for cortex writes: summary assertion + supersede batch.

§6.10 consolidation prefix — claim MUST start with ``"archive summary: "``
to be detected by ``libs/cortex_store/compaction._SUMMARY_RE``.

Idempotency: track via ``thread_summary(N)`` predicate-form assertions on the
anchor. If any such assertion has batch-turn-index >= collapse_up_to, the
window has already been compacted and the step is a no-op.

Partial supersede is not tolerated: if any supersede call fails after the
summary assertion is written, raise immediately — the summary exists but some
turn rows remain non-superseded. The caller must surface this as a hard error
so the state can be repaired (retry or manual cleanup).

∀ a ∈ collapse_set: a.id ∈ int | str (cortex assertion primary key)
∀ a ∈ collapse_set: a.superseded_by is None
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .artifact import resolve_artifact_path
from .events import cx_async

logger = get_logger(__name__)

_THREAD_SUMMARY_PREFIX = "thread_summary("
_SUMMARY_CLAIM_PREFIX = "archive summary: "
_USER_TURN_PREFIX = "user_turn("
_ASSISTANT_TURN_PREFIX = "assistant_turn("


# ---------------------------------------------------------------------------
# Pure helpers — no I/O
# ---------------------------------------------------------------------------


def is_thread_summary_assertion(assertion: dict[str, Any]) -> bool:
    """True when *assertion* is a non-superseded ``thread_summary(N)`` predicate."""
    if assertion.get("superseded_by"):
        return False
    pred = assertion.get("predicate_form") or ""
    return pred.startswith(_THREAD_SUMMARY_PREFIX)


def _parse_thread_summary_turn(predicate_form: str) -> int | None:
    """Extract N from ``thread_summary(N)``."""
    if not predicate_form.startswith(_THREAD_SUMMARY_PREFIX):
        return None
    try:
        return int(predicate_form.split("(", 1)[1].rstrip(")"))
    except (ValueError, IndexError):
        return None


def is_already_summarized(
    assertions: list[dict[str, Any]],
    collapse_up_to: int,
) -> bool:
    """True when an existing thread_summary covers the target collapse boundary.

    ∀ a ∈ assertions: is_thread_summary_assertion(a) ∧ parse_N(a) >= collapse_up_to
    ⟹ return True.

    ``collapse_up_to`` is the exclusive upper bound of the collapse set
    (i.e., ``turn_index - window_size``). A summary whose batch index equals
    or exceeds this value already covers the turns we would summarize.
    """
    for ass in assertions:
        if not is_thread_summary_assertion(ass):
            continue
        covered = _parse_thread_summary_turn(ass.get("predicate_form") or "")
        if covered is not None and covered >= collapse_up_to:
            return True
    return False


def _parse_turn_index_from_pred(predicate_form: str) -> int | None:
    """Extract N from ``user_turn(N)`` or ``assistant_turn(N)``."""
    if not (
        predicate_form.startswith(_USER_TURN_PREFIX)
        or predicate_form.startswith(_ASSISTANT_TURN_PREFIX)
    ):
        return None
    try:
        return int(predicate_form.split("(", 1)[1].rstrip(")"))
    except (ValueError, IndexError):
        return None


def select_collapse_set(
    assertions: list[dict[str, Any]],
    collapse_up_to: int,
) -> list[dict[str, Any]]:
    """Select non-superseded user/assistant turns with index < ``collapse_up_to``.

    ∀ a ∈ result:
      a.predicate_form ∈ {user_turn(N), assistant_turn(N)}
      ∧ parse_N(a) < collapse_up_to
      ∧ a.superseded_by is None
    """
    result: list[dict[str, Any]] = []
    for ass in assertions:
        if ass.get("superseded_by"):
            continue
        pred = ass.get("predicate_form") or ""
        n = _parse_turn_index_from_pred(pred)
        if n is None:
            continue
        if n < collapse_up_to:
            result.append(ass)
    return result


def _strip_turn_prefix(claim: str) -> str:
    """Strip ``User: `` or ``Assistant: `` from a turn claim (mirrors window.py)."""
    for prefix in ("User: ", "Assistant: "):
        if claim.startswith(prefix):
            return claim[len(prefix) :]
    return claim


def _collapse_sort_key(assertion: dict[str, Any]) -> tuple[int, int]:
    """Sort key: (turn_index ASC, user_before_assistant)."""
    pred = assertion.get("predicate_form") or ""
    n = _parse_turn_index_from_pred(pred) or 0
    role_order = 0 if pred.startswith(_USER_TURN_PREFIX) else 1
    return (n, role_order)


def _format_tool_calls(tool_calls: list[Any]) -> str:
    """Compact ``name(ok|fail)`` digest for summarization input."""
    parts: list[str] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name") or "?")
        ok = tc.get("ok", True)
        parts.append(f"{name}({'ok' if ok else 'fail'})")
    return ", ".join(parts)


def build_summary_input(
    collapse_set: list[dict[str, Any]],
    artifacts: dict[Any, dict[str, Any]] | None = None,
) -> str:
    """Build the conversation text block sent to the summarization model.

    When *artifacts* is provided (mapping assertion_id → artifact JSON loaded
    from disk), tool-call digests and key sidecar fields are appended after
    the turn content line so tool outcomes survive summarization.

    Turns are sorted by index ascending, user before assistant at each index.
    Each line is ``Role: content`` (role prefix already stripped from claim).
    """
    sorted_turns = sorted(collapse_set, key=_collapse_sort_key)
    lines: list[str] = []
    for ass in sorted_turns:
        pred = ass.get("predicate_form") or ""
        claim = ass.get("claim") or ""
        role = "User" if pred.startswith(_USER_TURN_PREFIX) else "Assistant"
        content = _strip_turn_prefix(claim)
        lines.append(f"{role}: {content}")
        if artifacts and role == "Assistant":
            artifact = artifacts.get(ass.get("id"))
            if artifact:
                tool_calls = artifact.get("tool_calls") or []
                if tool_calls:
                    digest = _format_tool_calls(tool_calls)
                    if digest:
                        lines.append(f"  [Tool activity: {digest}]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Artifact loading helpers (best-effort, Stage A)
# ---------------------------------------------------------------------------


async def _load_artifact_json(uri: str) -> dict[str, Any] | None:
    """Load artifact JSON from a workspaces:// URI.

    Returns None on URI mismatch, file-not-found, or parse error.
    Failures are logged at DEBUG and treated as non-fatal.
    """
    import aiofiles

    path: Path | None = resolve_artifact_path(uri)
    if path is None or not path.exists():
        return None
    try:
        async with aiofiles.open(path, encoding="utf-8") as fh:
            return json.loads(await fh.read())
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("compaction_summarize: artifact load skipped %s: %s", uri, exc)
        return None


async def load_collapse_set_artifacts(
    collapse_set: list[dict[str, Any]],
) -> dict[Any, dict[str, Any]]:
    """Load artifact JSON for each assertion in *collapse_set*.

    Returns a mapping {assertion_id → artifact_dict} for assertions whose
    primary evidence_uri resolves to a readable workspace artifact. Missing
    or unreadable artifacts are silently skipped.

    ∀ a ∈ collapse_set: first readable evidence_uri wins; remainder skipped.
    """
    result: dict[Any, dict[str, Any]] = {}
    for ass in collapse_set:
        aid = ass.get("id")
        if aid is None:
            continue
        for uri in ass.get("evidence_uris") or []:
            data = await _load_artifact_json(uri)
            if data is not None:
                result[aid] = data
                break
    return result


# ---------------------------------------------------------------------------
# Async cortex writes
# ---------------------------------------------------------------------------


async def write_summary_assertion(
    anchor_id: str,
    summary_text: str,
    batch_turn_index: int,
    collapse_set: list[dict[str, Any]],
    seeded_by: str,
) -> dict[str, Any]:
    """Assert a §6.10 consolidation-summary claim on the anchor entity.

    Claim format: ``"archive summary: {summary_text}"`` — matches
    ``_SUMMARY_RE`` in ``libs/cortex_store/compaction``.

    ``predicate_form`` is ``thread_summary({batch_turn_index})`` where
    ``batch_turn_index = turn_index - window_size`` (the exclusive upper
    bound of the collapsed turn set).

    ``evidence_uris`` aggregates all artifact URIs from the collapse set
    so auditors can recover source turns.
    """
    evidence_uris: list[str] = []
    for ass in collapse_set:
        evidence_uris.extend(ass.get("evidence_uris") or [])

    claim = f"{_SUMMARY_CLAIM_PREFIX}{summary_text}"
    return await cx_async(
        "assert",
        {
            "entity_id": anchor_id,
            "claim": claim,
            "confidence": "confirmed",
            "evidence": (
                f"Summarized {len(collapse_set)} turns up to index {batch_turn_index}"
            ),
            "derivation_type": "compression",
            "evidence_uris": evidence_uris,
            "predicate_form": f"thread_summary({batch_turn_index})",
            "seeded_by": seeded_by,
        },
    )


async def supersede_collapsed_turns(
    collapse_set: list[dict[str, Any]],
    summary_assertion_id: int | str,
    seeded_by: str,
) -> int:
    """Supersede every turn assertion in *collapse_set* via ``assertion_update``.

    Fails loudly on first error — partial supersede leaves the summary written
    but some turns still active. Caller must treat this as a hard error.

    Returns the number of successfully superseded assertions.

    ∀ a ∈ collapse_set: cx_async("assertion_update", {id, superseded_by}) ⟹
      status != error ∨ raise RuntimeError
    """
    superseded = 0
    for ass in collapse_set:
        assertion_id = ass.get("id")
        if assertion_id is None:
            raise RuntimeError(
                f"compaction_summarize: collapse_set entry missing id: {ass!r}"
            )
        res = await cx_async(
            "assertion_update",
            {
                "assertion_id": assertion_id,
                "superseded_by": summary_assertion_id,
            },
        )
        if "error" in res:
            raise RuntimeError(
                f"compaction_summarize: partial supersede — "
                f"failed on assertion_id={assertion_id}: {res['error']}; "
                f"summary={summary_assertion_id}, "
                f"already superseded={superseded}/{len(collapse_set)}"
            )
        superseded += 1
    return superseded
