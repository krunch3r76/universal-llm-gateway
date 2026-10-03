"""Conductor row-hop reactor (todo:conductor-hop-reactor R3).

Fires after ``ledger.mark_terminal`` on the closeout hot path. Closeout authority
(``hop_declared``, stop tokens) is merged **before** terminal via
``merge_conductor_closeout_hop_authority``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from claude_bundles.cdp_registry.execution_state import execution_state_of
from claude_bundles.conductor_stop import (
    EXIT_PERSIST_STOPS,
    consult_pending_blocks_progression,
    last_next_admit_payload,
    next_admit_blocks_hop_body,
    next_admit_payload_blocks_hop,
    next_admit_payload_matches_entry_gate,
    parse_designed_stop_tokens,
)
from implement_admission.conductor_score_table import SCOREBOARD_ROW_ID
from implement_admission.conductor_witness_types import row_status_in_tip
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_STARGATE_URL,
    make_async_client,
    make_sync_client,
)
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout import conductor_exit_reasons
from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
    _LIVE_NEST,
    SKIP_GATE_LIVE_EXTERNAL,
    SKIP_GATE_PROBE_INDETERMINATE,
    conductor_has_live_nested,
    external_gate_hop_verdict,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    budget_ok_for_hop,
    build_budget_authority_patch,
    evaluate_hop_budget,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_park import (
    park_conductor_hop_mission,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    HOP_NEXT_ADMIT_KEY,
    next_admit_in_closeout,
)
from services.git_integration_worker.cursor_sdk_conductor_identity import (
    is_conductor_dispatch_row,
)
from services.git_integration_worker.cursor_sdk_hop_events import (
    emit_frontier_sdk_conductor_hop_admit_failed,
    emit_frontier_sdk_conductor_hop_admitted,
    emit_frontier_sdk_conductor_hop_declared,
    emit_frontier_sdk_conductor_hop_deferral_released,
    emit_frontier_sdk_conductor_hop_skipped,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
    merge_hop_patch,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    PARK_KIND_DISCARD,
)

logger = get_logger(__name__)

_LIVE_STATUSES = frozenset({"queued", "admitted", "running", "parked_waiting"})
_CLOSEOUT_TOKENS_KEY = "closeout_stop_tokens"
_HOP_SEQ_LINE_RE = re.compile(r"(?im)^(?:\*\*)?hop_seq(?:\*\*)?:\s*(\d+)\s*$")
_RELAY_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=15.0, pool=5.0)
SKIP_GATE_NEXT_ADMIT_BLOCKED = "next_admit_blocked"
HOP_DEFERRAL_GATE_KEY = "hop_deferral_gate"
_TRANSIENT_DEFERRAL_GATES = frozenset(
    {SKIP_GATE_LIVE_EXTERNAL, SKIP_GATE_NEXT_ADMIT_BLOCKED}
)


def _load_row(dispatch_id: str) -> dict[str, Any] | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _is_conductor_row(row: dict[str, Any]) -> bool:
    return is_conductor_dispatch_row(row)


def _record_data(row: dict[str, Any]) -> dict[str, Any]:
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _scoreboard_text_for_row(row: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``(body, tip_sha)`` from the mission scoreboard when resolvable."""
    work_key = str(row.get("work_key") or "")
    if not work_key.startswith("todo:"):
        return "", None
    slug = work_key.split(":", 1)[1].strip()
    if not slug:
        return "", None
    try:
        from implement_admission.conductor_score_journal import read_tip

        tip = read_tip(slug)
    except Exception as exc:  # noqa: BLE001 — fold is advisory on hop path
        logger.warning(
            "conductor hop scoreboard read failed slug=%s err=%s",
            slug,
            exc,
        )
        return "", None
    if tip is None:
        return "", None
    body, sha = tip
    return body, sha


_SCOREBOARD_TABLE_ROW_RE = re.compile(
    r"^\|\s*(G\d+|R\d+|L\d+)\s*\|",
    re.MULTILINE,
)


def _first_open_row_in_scoreboard(scoreboard_body: str) -> str | None:
    """First table row whose Status cell is not DONE."""
    for match in _SCOREBOARD_TABLE_ROW_RE.finditer(scoreboard_body or ""):
        row_id = match.group(1).upper()
        status = (row_status_in_tip(scoreboard_body, row_id) or "OPEN").upper()
        if status != "DONE":
            return row_id
    return None


def _row_id_in_scoreboard_table(scoreboard_body: str, row_id: str | None) -> bool:
    """True when ``row_id`` is the first cell of a table row in the tip body."""
    if not row_id:
        return False
    wanted = row_id.upper()
    for match in _SCOREBOARD_TABLE_ROW_RE.finditer(scoreboard_body or ""):
        if match.group(1).upper() == wanted:
            return True
    return False


def _live_entry_gate_for_row(row: dict[str, Any], scoreboard_body: str) -> str | None:
    """Live entry gate: fold first for todo: missions, else header, else first OPEN row."""
    work_key = str(row.get("work_key") or "")
    if work_key.startswith("todo:"):
        slug = work_key.split(":", 1)[1].strip()
        if slug:
            try:
                from implement_admission.conductor_witness import (
                    fold_scoreboard,
                    resolve_entry_gate_from_fold,
                )

                from services.git_integration_worker.cursor_sdk_nested_witness import (
                    fold_deps_with_ledger,
                )

                fold = fold_scoreboard(
                    slug,
                    deps=fold_deps_with_ledger(
                        f"todo:{slug}",
                        repo=_fold_repo(row),
                    ),
                    write_journal=False,
                )
                if fold is not None:
                    gate_from_fold = resolve_entry_gate_from_fold(fold)
                    if gate_from_fold is None:
                        return None
                    if _row_id_in_scoreboard_table(scoreboard_body, gate_from_fold):
                        return gate_from_fold
            except Exception as exc:  # noqa: BLE001 — fold is advisory on hop path
                logger.warning(
                    "conductor hop entry_gate fold failed slug=%s err=%s",
                    slug,
                    exc,
                )
    gate = _scoreboard_entry_gate(scoreboard_body)
    if gate:
        return gate
    return _first_open_row_in_scoreboard(scoreboard_body)


def _scoreboard_next_admit_guard_snippet(
    row: dict[str, Any], scoreboard_body: str
) -> str | None:
    """Recency-scoped scoreboard NEXT_ADMIT when closeout did not name one."""
    admit = last_next_admit_payload(scoreboard_body)
    if not admit or not next_admit_payload_blocks_hop(admit):
        return None
    entry_gate = _live_entry_gate_for_row(row, scoreboard_body)
    if not next_admit_payload_matches_entry_gate(admit, entry_gate):
        return None
    return f"NEXT_ADMIT: {admit}"


def _next_admit_guard_text(row: dict[str, Any], rec: dict[str, Any]) -> str:
    """Closeout + gate-scoped scoreboard NEXT_ADMIT for P3.2 hop-body refusal."""
    parts: list[str] = []
    closeout_body = rec.get("closeout_body")
    if isinstance(closeout_body, str) and closeout_body.strip():
        parts.append(closeout_body)
    if next_admit_in_closeout(closeout_body or "") is not None:
        return "\n".join(parts)
    scoreboard_body, _ = _scoreboard_text_for_row(row)
    if scoreboard_body:
        snippet = _scoreboard_next_admit_guard_snippet(row, scoreboard_body)
        if snippet:
            parts.append(snippet)
    return "\n".join(parts)


_TERMINAL_EXECUTION_STATES = frozenset({"finished", "failed", "aborted"})
_HARVEST_ID_TOKEN_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")


def _row_hop_tokens_allow_lift(row: dict[str, Any]) -> bool:
    tokens = _closeout_tokens_from_row(row)
    if "ROW_HOP" not in tokens:
        return False
    if tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
        return False
    return True


def _harvest_target_token(guard: str) -> str | None:
    admit = last_next_admit_payload(guard)
    if not admit:
        return None
    parts = admit.strip().split()
    if len(parts) != 2 or parts[0].lower() != "harvest":
        return None
    token = parts[1]
    if not _HARVEST_ID_TOKEN_RE.fullmatch(token):
        return None
    hex_digits = sum(1 for ch in token if ch in "0123456789abcdefABCDEF")
    if hex_digits < 8:
        return None
    return token


def _inflight_db_path() -> Path:
    data_dir = os.environ.get("DATA_DIR", str(Path.home() / ".gateway"))
    return Path(data_dir) / "stargate-cdp-generate-inflight.db"


def _prefix_match_id(candidate: str, token: str) -> bool:
    if not candidate or not token:
        return False
    lower_c = candidate.lower()
    lower_t = token.lower()
    return lower_c == lower_t or lower_c.startswith(lower_t)


def _dispatch_terminal_for_status(status: str | None) -> bool:
    return str(status or "") not in _LIVE_NEST


def _execution_terminal_from_state(row: dict[str, Any]) -> bool:
    entry = execution_state_of(row)
    if entry is None:
        return False
    return str(entry.get("state") or "") in _TERMINAL_EXECUTION_STATES


def _collect_dispatch_targets(token: str) -> list[tuple[str, bool]]:
    ledger = CursorDispatchLedger.instance()
    hits: list[tuple[str, bool]] = []
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id = ? OR dispatch_id LIKE ?",
            (token, token + "%"),
        ).fetchall()
    for row in rows:
        dispatch_id = str(row["dispatch_id"])
        hits.append((dispatch_id, _dispatch_terminal_for_status(row["status"])))
    return hits


def _registry_rows_for_exact_execution_id(
    active: dict[str, dict[str, Any]], execution_id: str
) -> list[dict[str, Any]]:
    if not execution_id:
        return []
    matches: list[dict[str, Any]] = []
    for row in active.values():
        if not isinstance(row, dict):
            continue
        top = str(row.get("execution_id") or "").strip()
        state_entry = execution_state_of(row)
        nested = (
            str(state_entry.get("execution_id") or "").strip() if state_entry else ""
        )
        if top == execution_id or nested == execution_id:
            matches.append(row)
    return matches


def _collect_execution_targets(token: str) -> list[tuple[str, bool]]:
    from claude_bundles.cdp_registry_store import load_active

    targets: list[tuple[str, bool]] = []
    inflight_satellites: set[str] = set()
    db_path = _inflight_db_path()
    if db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            conn.row_factory = sqlite3.Row
            inflight_rows = conn.execute(
                "SELECT execution_id, satellite_execution_id FROM cdp_inflight_leg "
                "WHERE execution_id = ? OR execution_id LIKE ?",
                (token, token + "%"),
            ).fetchall()
        finally:
            conn.close()
        active = load_active()
        for inflight_row in inflight_rows:
            exec_id = str(inflight_row["execution_id"])
            satellite = str(inflight_row["satellite_execution_id"] or "").strip()
            if satellite:
                inflight_satellites.add(satellite)
            registry_hits = _registry_rows_for_exact_execution_id(active, satellite)
            if len(registry_hits) != 1:
                terminal = False
            else:
                terminal = _execution_terminal_from_state(registry_hits[0])
            targets.append((exec_id, terminal))

    active = load_active()
    for row in active.values():
        if not isinstance(row, dict):
            continue
        top = str(row.get("execution_id") or "").strip()
        state_entry = execution_state_of(row)
        nested = (
            str(state_entry.get("execution_id") or "").strip() if state_entry else ""
        )
        matched_key: str | None = None
        if _prefix_match_id(top, token):
            matched_key = top
        elif nested and _prefix_match_id(nested, token):
            matched_key = nested
        else:
            continue
        if top in inflight_satellites or nested in inflight_satellites:
            continue
        targets.append((matched_key, _execution_terminal_from_state(row)))
    return targets


def _named_target_is_terminal(
    token: str, *, absent_counts_terminal: bool = False
) -> bool:
    """Whether the harvest id is finished.

    ``absent_counts_terminal`` is only for a deferral already stamped while the
    id was in flight. An empty registry after that means the execution left,
    which is the 9c54 case: the review replied and both the inflight row and
    the active registry row were gone, so ``len(combined) != 1`` stayed false.
    """
    try:
        dispatch_targets = _collect_dispatch_targets(token)
        execution_targets = _collect_execution_targets(token)
    except Exception:
        return False
    combined: list[tuple[str, str, bool]] = [
        ("dispatch", key, terminal) for key, terminal in dispatch_targets
    ] + [("execution", key, terminal) for key, terminal in execution_targets]
    if len(combined) == 0:
        return absent_counts_terminal
    if len(combined) != 1:
        return False
    kind, _key, terminal = combined[0]
    if not terminal:
        return False
    if kind == "execution" and not absent_counts_terminal:
        try:
            snap = conductor_exit_reasons.read_external_gate_lane_snapshot()
        except Exception:
            return False
        if not snap:
            return False
        if conductor_exit_reasons.cdp_ask_health_red():
            return False
    return True


def hop_body_build_refused(
    row: dict[str, Any], rec: dict[str, Any] | None = None
) -> bool:
    """True when NEXT_ADMIT forbids rematerializing a conductor successor (AC7)."""
    data = rec if rec is not None else _record_data(row)
    guard = _next_admit_guard_text(row, data)
    if not next_admit_blocks_hop_body(guard):
        return False
    if not _row_hop_tokens_allow_lift(row):
        return True
    token = _harvest_target_token(guard)
    if token is None:
        return True
    absent = str(data.get(HOP_DEFERRAL_GATE_KEY) or "") == SKIP_GATE_NEXT_ADMIT_BLOCKED
    if not _named_target_is_terminal(token, absent_counts_terminal=absent):
        return True
    if conductor_has_live_nested(dispatch_id=str(row.get("dispatch_id") or "")):
        return True
    return False


def _scoreboard_entry_gate(scoreboard_body: str) -> str | None:
    """Parse ``**Entry gate:** <row id>`` from the scoreboard tip when present.

    Row ids follow ``SCOREBOARD_ROW_ID`` (G1–G7 and R rows). A G-only pattern
    dropped R-row missions, so ``generation_options.scoreboard_entry_gate``
    stayed unset (a:37055).
    """
    # Bold may close before the colon (``**Entry gate**:``) or after it
    # (``**Entry gate:**``), which is how scoreboard tips are written.
    match = re.search(
        rf"(?im)\*{{0,2}}Entry gate\*{{0,2}}\s*:\s*\*{{0,2}}\s*({SCOREBOARD_ROW_ID})\b",
        scoreboard_body or "",
    )
    if match is None:
        return None
    return match.group(1).upper()


def _resolve_source_ref(row: dict[str, Any], rec: dict[str, Any]) -> str:
    for candidate in (
        rec.get("source_ref"),
        row.get("source_ref"),
        row.get("work_key"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _successor_hop_seq(row: dict[str, Any], rec: dict[str, Any]) -> int:
    """Closeout ``hop_seq`` is this row's seq; successor is +1."""
    closeout_seq = rec.get("closeout_hop_seq")
    if isinstance(closeout_seq, int):
        return closeout_seq + 1
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    prior_seq = hop_fields.get("hop_seq")
    if isinstance(prior_seq, int):
        return prior_seq + 1
    return 1


def _hop_skip_gate(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
) -> str | None:
    """Return skip gate name when hop reactor must not POST successor."""
    if _cancel_discard_blocks_hop(row):
        return "cancel_discard"
    _, ext_gate = external_gate_hop_verdict(row)
    if ext_gate == SKIP_GATE_LIVE_EXTERNAL:
        return SKIP_GATE_LIVE_EXTERNAL
    if ext_gate == SKIP_GATE_PROBE_INDETERMINATE:
        # Stay fail-closed on this pass. The watchdog admits once the row has
        # been terminal longer than twice the reactor grace.
        return SKIP_GATE_PROBE_INDETERMINATE
    if not hop_owed(row, closeout_tokens=closeout_tokens):
        status = str(row.get("status") or "")
        if status not in ("completed", "failed", "cancelled"):
            return "mission_closed"
        tokens = closeout_tokens
        if tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
            return "mission_closed"
        thread_id = str(row.get("thread_id") or "")
        dispatch_id = str(row.get("dispatch_id") or "")
        if (
            thread_id
            and dispatch_id
            and live_conductor_row_on_thread(
                thread_id=thread_id, exclude_dispatch_id=dispatch_id
            )
        ):
            return "live_sibling"
        if dispatch_id and conductor_has_live_nested(dispatch_id=dispatch_id):
            return "live_nested"
        if not mission_open_for_row(row, closeout_tokens=tokens):
            return "mission_closed"
        hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
        if hop_fields.get("hop_successor"):
            return "already_hopped"
        if not budget_ok_for_hop(row, closeout_tokens=tokens):
            return "budget"
        return "mission_closed"
    return None


def _deferral_stamp_allowed(row: dict[str, Any], gate: str) -> bool:
    """Stamp only gates that can clear. ``NEXT_ADMIT: none`` is not one of them."""
    if gate == SKIP_GATE_LIVE_EXTERNAL:
        return True
    if gate != SKIP_GATE_NEXT_ADMIT_BLOCKED:
        return False
    if not _row_hop_tokens_allow_lift(row):
        return False
    rec = _record_data(row)
    return _harvest_target_token(_next_admit_guard_text(row, rec)) is not None


def _emit_hop_skipped(
    row: dict[str, Any],
    *,
    gate: str,
    hop_seq: int | None = None,
) -> None:
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    if not dispatch_id or not thread_id:
        return
    if hop_seq is None:
        rec = _record_data(row)
        hop_seq = _successor_hop_seq(row, rec) - 1
        if hop_seq < 1:
            hop_seq = 1
    emit_frontier_sdk_conductor_hop_skipped(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=int(hop_seq),
        gate=gate,
    )
    if _deferral_stamp_allowed(row, gate):
        CursorDispatchLedger.instance().merge_record_json(
            dispatch_id=dispatch_id,
            patch={HOP_DEFERRAL_GATE_KEY: gate},
        )


def _closeout_tokens_from_row(row: dict[str, Any]) -> frozenset[str]:
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return frozenset()
    raw = data.get(_CLOSEOUT_TOKENS_KEY)
    if isinstance(raw, list):
        return frozenset(str(t).upper() for t in raw)
    return frozenset()


def _closeout_body_from_row(row: dict[str, Any]) -> str:
    rec = _record_data(row)
    body = rec.get("closeout_body")
    if isinstance(body, str) and body.strip():
        return body
    return str(row.get("closeout_body") or row.get("message") or "")


def _parse_hop_seq_from_closeout(body: str) -> int | None:
    match = _HOP_SEQ_LINE_RE.search(body or "")
    if match is None:
        return None
    return int(match.group(1))


def _infer_hop_reason(
    *,
    closeout_tokens: frozenset[str],
    terminal_status: str,
) -> str:
    if "ROW_HOP" in closeout_tokens:
        return "planned"
    if terminal_status == "failed":
        return "crash"
    return "silent"


def live_conductor_row_on_thread(
    *,
    thread_id: str,
    exclude_dispatch_id: str | None = None,
) -> bool:
    """True when another live conductor row holds ``thread_id``."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, record_json, contract, work_key "
            "FROM cursor_sdk_dispatches "
            "WHERE thread_id=? AND status IN ('queued','admitted','running','parked_waiting') "
            "AND dispatch_id<>?",
            (thread_id, exclude_dispatch_id or ""),
        ).fetchall()
    return any(_is_conductor_row({k: row[k] for k in row.keys()}) for row in rows)


_DEFAULT_FOLD_REPO = Path("/mnt/torus/projects/universal-llm-gateway")


def _fold_repo(row: dict[str, Any]) -> Path:
    raw = row.get("source_repo")
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return _DEFAULT_FOLD_REPO


def mission_open_for_row(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
) -> bool:
    """Scoreboard fold still has a non-DONE row (bind §2.6 hop_owed)."""
    if "DONE" in closeout_tokens:
        return False
    work_key = str(row.get("work_key") or "")
    if not work_key.startswith("todo:"):
        return True
    slug = work_key.split(":", 1)[1].strip()
    if not slug:
        return True
    try:
        from implement_admission.conductor_witness import fold_scoreboard

        from services.git_integration_worker.cursor_sdk_nested_witness import (
            fold_deps_with_ledger,
        )

        fold = fold_scoreboard(
            slug,
            deps=fold_deps_with_ledger(
                f"todo:{slug}",
                repo=_fold_repo(row),
            ),
            write_journal=False,
        )
        if fold is None:
            return True
        return any(status != "DONE" for status in fold.row_status.values())
    except Exception as exc:  # noqa: BLE001 — fold is advisory; token gate remains
        logger.warning(
            "conductor hop mission_open fold failed slug=%s err=%s",
            slug,
            exc,
        )
        return "DONE" not in closeout_tokens


def _cancel_discard_blocks_hop(row: dict[str, Any]) -> bool:
    """``cancel_discard`` kills the mission — no successor admit (a:37149)."""
    return str(row.get("park_kind") or "") == PARK_KIND_DISCARD


def hop_owed(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
    ignore_probe_indeterminate: bool = False,
) -> bool:
    """Predicate from bind §2.6 item 3 (row must already be terminal).

    ``ignore_probe_indeterminate`` is the watchdog's double-grace path. The
    reactor leaves it false, so an empty CDP probe still withholds immediately.
    A live external gate is never ignored.

    ``park_kind=cancel_discard`` is a mission kill (agent-bus-discipline): the
    hop reactor must not admit a successor the way ``park_for_restart`` continues
    via resume. Restart blocks hop via ``PARKED_TRANSPORT``; discard blocks here.
    """
    status = str(row.get("status") or "")
    if status not in ("completed", "failed", "cancelled"):
        return False
    if _cancel_discard_blocks_hop(row):
        return False
    tokens = closeout_tokens or _closeout_tokens_from_row(row)
    if tokens & (EXIT_PERSIST_STOPS | frozenset({"DONE"})):
        return False
    if consult_pending_blocks_progression(_closeout_body_from_row(row)):
        return False
    thread_id = str(row.get("thread_id") or "")
    dispatch_id = str(row.get("dispatch_id") or "")
    if not thread_id or not dispatch_id:
        return False
    if live_conductor_row_on_thread(
        thread_id=thread_id, exclude_dispatch_id=dispatch_id
    ):
        return False
    if conductor_has_live_nested(dispatch_id=dispatch_id):
        return False
    if not mission_open_for_row(row, closeout_tokens=tokens):
        return False
    if not budget_ok_for_hop(row, closeout_tokens=tokens):
        return False
    ext_verdict, _ext_gate = external_gate_hop_verdict(row)
    if ext_verdict == "live":
        return False
    if ext_verdict == "indeterminate_closed" and not ignore_probe_indeterminate:
        return False
    hop_fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    if hop_fields.get("hop_successor"):
        return False
    return True


def _write_record_json(dispatch_id: str, record_json: str) -> None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (record_json, dispatch_id),
        )


def _write_budget_authority(dispatch_id: str, row: dict[str, Any]) -> None:
    ledger = CursorDispatchLedger.instance()
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch=build_budget_authority_patch(row),
    )


def _utc_closeout_instant() -> str:
    return datetime.now(UTC).isoformat()


def _summoning_head_turn_patch(
    *,
    summoning_thread_id: str,
    row: dict[str, Any],
    rec: dict[str, Any],
) -> dict[str, Any]:
    """Stamp consult summoning watermark or closeout-anchored error for retry."""
    patch: dict[str, Any] = {}
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    closeout_instant = _utc_closeout_instant()
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=2.0) as client:
            resp = client.get(
                "/turns/by-number",
                params={"thread": summoning_thread_id, "turn_number": "latest"},
                headers=headers,
            )
    except Exception:
        patch["consult_summoning_stamp_error"] = {
            "class": "connect_error",
            "closeout_instant": closeout_instant,
        }
        return patch
    if resp.status_code == 404:
        try:
            detail = resp.json().get("detail", {})
        except ValueError:
            detail = {}
        data = detail.get("data") if isinstance(detail, dict) else None
        turn_count = data.get("turn_count") if isinstance(data, dict) else None
        if turn_count == 0:
            patch["consult_summoning_stamp_error"] = {
                "class": "empty_thread",
                "closeout_instant": closeout_instant,
            }
            return patch
        patch["consult_summoning_stamp_error"] = {
            "class": "not_found",
            "closeout_instant": closeout_instant,
        }
        return patch
    if resp.status_code != 200:
        patch["consult_summoning_stamp_error"] = {
            "class": f"http_{resp.status_code}",
            "closeout_instant": closeout_instant,
        }
        return patch
    try:
        payload = resp.json()
    except ValueError:
        patch["consult_summoning_stamp_error"] = {
            "class": "parse_error",
            "closeout_instant": closeout_instant,
        }
        return patch
    if not isinstance(payload, dict):
        patch["consult_summoning_stamp_error"] = {
            "class": "parse_error",
            "closeout_instant": closeout_instant,
        }
        return patch
    turn_number = payload.get("turn_number")
    if not isinstance(turn_number, int):
        patch["consult_summoning_stamp_error"] = {
            "class": "missing_turn_number",
            "closeout_instant": closeout_instant,
        }
        return patch
    patch["consult_summoning_after_turn"] = turn_number
    exec_id = rec.get("cdp_execution_id") or row.get("execution_id")
    if exec_id:
        patch["cdp_execution_id"] = str(exec_id)
    return patch


def _fold_mission(row: dict[str, Any]) -> Any:
    """Witness fold for the row's todo, or None when there is no board."""
    work_key = str(row.get("work_key") or "")
    if not work_key.startswith("todo:"):
        return None
    slug = work_key.split(":", 1)[1].strip()
    if not slug:
        return None
    try:
        from implement_admission.conductor_witness import fold_scoreboard

        from services.git_integration_worker.cursor_sdk_nested_witness import (
            fold_deps_with_ledger,
        )

        return fold_scoreboard(
            slug,
            deps=fold_deps_with_ledger(f"todo:{slug}", repo=_fold_repo(row)),
            write_journal=False,
        )
    except Exception as exc:  # noqa: BLE001 — fold failure must not fail closeout
        logger.warning(
            "conductor done workflow fold failed slug=%s err=%s",
            slug,
            exc,
        )
        return None


def mark_todo_done_on_mission_close(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
) -> None:
    """Set workflow_state=done when the fold shows every row DONE and G7 landed.

    A DONE token alone is not enough: a partial ladder, an open mission, a
    missing G7, or a ROW_HOP closeout leaves the card open. Matches the
    implement adapter's ``entity_update(workflow_state=done)`` write.
    """
    if "DONE" not in closeout_tokens or "ROW_HOP" in closeout_tokens:
        return
    fold = _fold_mission(row)
    if fold is None:
        return
    statuses = getattr(fold, "row_status", None) or {}
    if not statuses:
        return
    if any(status != "DONE" for status in statuses.values()):
        return
    if statuses.get("G7") != "DONE":
        return
    work_key = str(row.get("work_key") or "")
    slug = work_key.split(":", 1)[1].strip() if work_key.startswith("todo:") else ""
    if not slug:
        return
    entity_id = f"todo:{slug}"
    from implement_admission.closeout_runtime import get_runtime

    try:
        resp = get_runtime().dispatch(
            "entity_update",
            {"entity_id": entity_id, "workflow_state": "done"},
        )
    except Exception as exc:  # noqa: BLE001 — must not block terminal closeout
        logger.warning(
            "conductor done workflow entity_update failed entity_id=%s err=%s",
            entity_id,
            exc,
        )
        return
    if isinstance(resp, dict) and resp.get("error"):
        logger.warning(
            "conductor done workflow entity_update error entity_id=%s err=%s",
            entity_id,
            resp.get("error"),
        )


def merge_conductor_closeout_hop_authority(
    *,
    dispatch_id: str,
    closeout_body: str,
    thread_id: str,
    closeout_turn: int | None = None,
) -> None:
    """Merge ``hop_declared`` and closeout tokens before ``mark_terminal``.

    Swallows ``Exception`` so every production caller can still mark the
    dispatch terminal (friction 37404/37488). ``BaseException`` still
    propagates.
    """
    try:
        _merge_conductor_closeout_hop_authority(
            dispatch_id=dispatch_id,
            closeout_body=closeout_body,
            thread_id=thread_id,
            closeout_turn=closeout_turn,
        )
    except Exception:  # noqa: BLE001 — closeout always marks terminal
        logger.exception(
            "merge_conductor_closeout_hop_authority raised dispatch=%s",
            dispatch_id,
        )


def _merge_conductor_closeout_hop_authority(
    *,
    dispatch_id: str,
    closeout_body: str,
    thread_id: str,
    closeout_turn: int | None = None,
) -> None:
    """Unguarded hop-authority merge; call the public wrapper from production."""
    from bus_watch.park_harvest import harvest_still_owed

    parsed = parse_designed_stop_tokens(closeout_body)
    tokens = parsed.designed_tokens or parsed.tokens
    row = _load_row(dispatch_id)
    if row is None or not _is_conductor_row(row):
        return
    record_json = str(row.get("record_json") or "")
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[_CLOSEOUT_TOKENS_KEY] = sorted(tokens)
    data["closeout_body"] = closeout_body
    if closeout_turn is not None:
        data["closeout_turn"] = int(closeout_turn)
    data["closeout_harvest_owed"] = harvest_still_owed(body=closeout_body)
    next_admit = next_admit_in_closeout(closeout_body)
    if next_admit:
        data[HOP_NEXT_ADMIT_KEY] = next_admit
    if "ROW_HOP" in tokens:
        data["hop_declared"] = True
        hop_seq = _parse_hop_seq_from_closeout(closeout_body)
        if hop_seq is None:
            prior = hop_fields_from_record_json(str(row.get("record_json") or ""))
            prior_seq = prior.get("hop_seq")
            hop_seq = int(prior_seq) if isinstance(prior_seq, int) else 1
        data["closeout_hop_seq"] = hop_seq
    _write_record_json(
        dispatch_id,
        json.dumps(data, sort_keys=True, separators=(",", ":")),
    )
    summoning_thread_id = str(data.get("summoning_thread_id") or "").strip()
    if summoning_thread_id:
        summoning_patch = _summoning_head_turn_patch(
            summoning_thread_id=summoning_thread_id,
            row=row,
            rec=data,
        )
        if summoning_patch:
            CursorDispatchLedger.instance().merge_record_json(
                dispatch_id=dispatch_id,
                patch=summoning_patch,
            )
    row = _load_row(dispatch_id) or row
    if "ROW_HOP" in tokens:
        hop_seq = data.get("closeout_hop_seq")
        if not isinstance(hop_seq, int):
            hop_seq = _parse_hop_seq_from_closeout(closeout_body)
        if hop_seq is None:
            prior = hop_fields_from_record_json(str(row.get("record_json") or ""))
            prior_seq = prior.get("hop_seq")
            hop_seq = int(prior_seq) if isinstance(prior_seq, int) else 1
        emit_frontier_sdk_conductor_hop_declared(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason="planned",
        )
    mark_todo_done_on_mission_close(row, closeout_tokens=tokens)


def build_conductor_hop_idempotency_key(predecessor_dispatch_id: str) -> str:
    return f"conductor-hop:{predecessor_dispatch_id}"


def build_hop_team_dispatch_body(
    row: dict[str, Any],
    *,
    hop_reason_override: str | None = None,
) -> dict[str, Any] | None:
    """Clone predecessor ledger record for Stargate ``team_dispatch`` generate."""
    if not _is_conductor_row(row):
        return None
    record_json = str(row.get("record_json") or "")
    try:
        rec = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    if hop_body_build_refused(row, rec):
        return None
    closeout_tokens = _closeout_tokens_from_row(row)
    predecessor_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    source_ref = _resolve_source_ref(row, rec)
    if not thread_id:
        return None
    if not source_ref:
        return None
    next_seq = _successor_hop_seq(row, rec)
    hop_reason = hop_reason_override or _infer_hop_reason(
        closeout_tokens=closeout_tokens,
        terminal_status=str(row.get("terminal_status") or row.get("status") or ""),
    )
    generation_options = dict(rec.get("generation_options") or {})
    summon_mode = generation_options.get("summon_mode")
    if summon_mode is None and rec.get("summon_mode"):
        summon_mode = rec.get("summon_mode")
    if summon_mode is not None:
        generation_options["summon_mode"] = summon_mode
    generation_options["idempotency_key"] = build_conductor_hop_idempotency_key(
        predecessor_id
    )
    scoreboard_body, scoreboard_sha = _scoreboard_text_for_row(row)
    if scoreboard_sha:
        generation_options["scoreboard_tip_sha"] = scoreboard_sha
    if scoreboard_body:
        generation_options["scoreboard_entry_gate"] = _scoreboard_entry_gate(
            scoreboard_body
        )
    summoning_thread_id = str(rec.get("summoning_thread_id") or "").strip()
    if summoning_thread_id:
        generation_options["summoning_thread_id"] = summoning_thread_id
    else:
        generation_options["summoning_thread_id_unresolved"] = True
    routing_model = rec.get("model") or row.get("resolved_model")
    raw_contract = str(rec.get("contract") or row.get("contract") or "conductor")
    job = {"none": "freeform", "pure-mechanical": "mechanical"}.get(
        raw_contract, raw_contract
    )
    body: dict[str, Any] = {
        "op": "generate",
        "seat": "cursor-sdk",
        "job": job,
        "lane": rec.get("lane") or "B",
        "caller_agent": "conductor-hop",
        "reuse_thread": thread_id,
        "source_ref": source_ref,
        "model": routing_model,
        "generation_options": generation_options,
    }
    if summoning_thread_id:
        body["dispatch_thread_id"] = summoning_thread_id
    if rec.get("model_knobs"):
        body["model_knobs"] = rec.get("model_knobs")
    body["hop_from"] = predecessor_id
    body["hop_seq"] = next_seq
    body["hop_reason"] = hop_reason
    return body


def mint_hop_successor_dispatch_id() -> str:
    """Mint a worker dispatch_id in the server's 12hex-8hex shape.

    Reactor and watchdog omit ``dispatch_id``. A bare ``uuid4`` matches an
    execution_id, so ``nest_under`` cannot separate them by shape. Rows already
    admitted under a UUID still go through the ledger check.
    """
    return f"{uuid.uuid4().hex[:12]}-{uuid.uuid4().hex[:8]}"


async def post_conductor_hop_team_dispatch(
    body: dict[str, Any],
    *,
    stargate_url: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """POST ``/api/v1/team/dispatch``; return ``(ok, detail)``."""
    stop_id = str(body.get("hop_from") or "").strip()
    if not stop_id:
        return False, {"reason": "missing_hop_from"}
    admit_dispatch_id = (
        str(body.get("dispatch_id") or "").strip() or mint_hop_successor_dispatch_id()
    )
    wire_body = dict(body)
    wire_body["dispatch_id"] = admit_dispatch_id
    ledger = CursorDispatchLedger.instance()
    if not ledger.claim_stop_service(stop_id, admit_dispatch_id):
        return False, {"reason": "stop_not_claimed", "stop_id": stop_id}
    base = (stargate_url or DEFAULT_STARGATE_URL).rstrip("/")
    endpoint = "/api/v1/team/dispatch"
    try:
        async with make_async_client(base, timeout=_RELAY_TIMEOUT) as client:
            resp = await client.post(endpoint, json=wire_body)
    except httpx.HTTPError as exc:
        logger.warning("conductor hop team_dispatch transport error: %s", exc)
        ledger.release_stop_service(stop_id, admit_dispatch_id)
        return False, {"error": str(exc), "reason": "stargate_unreachable"}
    try:
        payload = resp.json()
    except ValueError:
        payload = {"error": "non_json_response", "text": resp.text[:300]}
    if not isinstance(payload, dict):
        payload = {"error": "non_object_response"}
    if resp.status_code >= 400 or payload.get("error"):
        # A refused admit created no row; keep the slot open for the retry.
        ledger.release_stop_service(stop_id, admit_dispatch_id)
        return False, {
            "status_code": resp.status_code,
            "error": payload,
        }
    return True, payload


def _mission_park_blocks_hop(row: dict[str, Any]) -> bool:
    from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
        mission_park_state,
    )

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        return (
            mission_park_state(
                conn,
                work_key=str(row.get("work_key") or "") or None,
                thread_id=str(row.get("thread_id") or "") or None,
            )
            is not None
        )


def _emit_deferral_released(
    row: dict[str, Any],
    *,
    hop_seq: int,
    successor_dispatch_id: str,
) -> None:
    rec = _record_data(row)
    prior = str(rec.get(HOP_DEFERRAL_GATE_KEY) or "")
    if prior not in _TRANSIENT_DEFERRAL_GATES or not successor_dispatch_id:
        return
    dispatch_id = str(row.get("dispatch_id") or "")
    thread_id = str(row.get("thread_id") or "")
    if not dispatch_id or not thread_id:
        return
    emit_frontier_sdk_conductor_hop_deferral_released(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq,
        prior_gate=prior,
        successor_dispatch_id=successor_dispatch_id,
    )


def _deferral_cleared(row: dict[str, Any]) -> bool:
    """True when a stamped transient gate no longer withholds the successor."""
    rec = _record_data(row)
    gate = str(rec.get(HOP_DEFERRAL_GATE_KEY) or "")
    if gate == SKIP_GATE_LIVE_EXTERNAL:
        verdict, _skip = external_gate_hop_verdict(row)
        return verdict != "live"
    if gate == SKIP_GATE_NEXT_ADMIT_BLOCKED:
        return not hop_body_build_refused(row, rec)
    return False


def _release_backoff_blocks(row: dict[str, Any]) -> bool:
    """Hold a retry after a failed POST. The first release does not wait."""
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog import (
        _admit_error_permanent,
        _backoff_elapsed,
    )

    if _admit_error_permanent(row):
        return True
    fields = hop_fields_from_record_json(str(row.get("record_json") or ""))
    if not isinstance(fields.get("hop_admit_error"), dict):
        return False
    verdict = evaluate_hop_budget(
        row, closeout_tokens=_closeout_tokens_from_row(row)
    )
    return not _backoff_elapsed(row, backoff_s=verdict.backoff_s)


def _deferred_hop_dispatch_ids() -> list[str]:
    from services.git_integration_worker.cursor_sdk_park import (
        _latest_terminal_conductor_rows,
    )

    ledger = CursorDispatchLedger.instance()
    found: list[str] = []
    with ledger._connect() as conn:
        rows = _latest_terminal_conductor_rows(conn)
    for raw in rows:
        row = {k: raw[k] for k in raw.keys()}
        rec = _record_data(row)
        if str(rec.get(HOP_DEFERRAL_GATE_KEY) or "") not in _TRANSIENT_DEFERRAL_GATES:
            continue
        if hop_fields_from_record_json(str(row.get("record_json") or "")).get(
            "hop_successor"
        ):
            continue
        if _release_backoff_blocks(row):
            continue
        if not _deferral_cleared(row):
            continue
        dispatch_id = str(row.get("dispatch_id") or "")
        if dispatch_id:
            found.append(dispatch_id)
    return found


async def release_deferred_conductor_hops() -> int:
    """Re-run admit for hops deferred on a gate that has since cleared.

    The first release does not wait for reactor grace. A later failed POST
    waits out the hop backoff and stops once the admit error is permanent.
    """
    admitted = 0
    for dispatch_id in await asyncio.to_thread(_deferred_hop_dispatch_ids):
        await maybe_fire_conductor_hop_reactor(dispatch_id=dispatch_id)
        row = _load_row(dispatch_id)
        if row is None:
            continue
        if hop_fields_from_record_json(str(row.get("record_json") or "")).get(
            "hop_successor"
        ):
            admitted += 1
    return admitted


async def maybe_fire_conductor_hop_reactor(*, dispatch_id: str) -> None:
    """Evaluate ``hop_owed`` and POST successor admit when due (after terminal)."""
    row = _load_row(dispatch_id)
    if row is None:
        return
    if not _is_conductor_row(row):
        _emit_hop_skipped(row, gate="not_conductor_row")
        return
    if _mission_park_blocks_hop(row):
        _emit_hop_skipped(row, gate="mission_parked")
        return
    closeout_tokens = _closeout_tokens_from_row(row)
    _write_budget_authority(dispatch_id, row)
    row = _load_row(dispatch_id) or row
    closeout_tokens = _closeout_tokens_from_row(row)
    from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
        maybe_fire_conductor_park_harvest,
        park_harvest_owed,
    )

    if park_harvest_owed(row, closeout_tokens=closeout_tokens):
        await maybe_fire_conductor_park_harvest(dispatch_id=dispatch_id)
        return
    skip_gate = _hop_skip_gate(row, closeout_tokens=closeout_tokens)
    if skip_gate is not None:
        if skip_gate == "budget":
            verdict = evaluate_hop_budget(row, closeout_tokens=closeout_tokens)
            if verdict.park and verdict.reason:
                await park_conductor_hop_mission(row, reason=verdict.reason)
                return
        _emit_hop_skipped(row, gate=skip_gate)
        return
    body = build_hop_team_dispatch_body(row)
    if body is None:
        rec = _record_data(row)
        if hop_body_build_refused(row, rec):
            gate = SKIP_GATE_NEXT_ADMIT_BLOCKED
        elif not str(rec.get("summoning_thread_id") or "").strip():
            gate = "summoning_unresolved"
        elif not _resolve_source_ref(row, rec):
            gate = "missing_source_ref"
        else:
            gate = "body_build_failed"
        _emit_hop_skipped(row, gate=gate)
        return
    if "dispatch_thread_id" not in body:
        _emit_hop_skipped(row, gate="summoning_unresolved")
        return
    thread_id = str(row.get("thread_id") or "")
    hop_seq = int(body.get("hop_seq") or 1)
    hop_reason = str(body.get("hop_reason") or "planned")
    ok, detail = await post_conductor_hop_team_dispatch(body)
    record_json = str(row.get("record_json") or "")
    if ok:
        successor = str(detail.get("dispatch_id") or "") or str(
            detail.get("execution_id") or ""
        )
        if successor:
            merged = merge_hop_patch(
                record_json,
                {"hop_successor": successor},
            )
            _write_record_json(dispatch_id, merged)
            emit_frontier_sdk_conductor_hop_admitted(
                predecessor_dispatch_id=dispatch_id,
                successor_dispatch_id=successor,
                thread_id=thread_id,
                hop_seq=hop_seq,
                hop_reason=hop_reason,
            )
            _emit_deferral_released(
                row, hop_seq=hop_seq, successor_dispatch_id=successor
            )
        else:
            logger.warning(
                "conductor hop admit ok but no successor id dispatch_id=%s detail=%s",
                dispatch_id,
                detail,
            )
        return
    error_text = json.dumps(detail, sort_keys=True)[:500]
    merged = merge_hop_patch(
        record_json,
        {
            "hop_admit_error": {
                "last_error": error_text,
                "last_status_code": detail.get("status_code"),
            }
        },
    )
    _write_record_json(dispatch_id, merged)
    emit_frontier_sdk_conductor_hop_admit_failed(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        hop_seq=hop_seq,
        hop_reason=hop_reason,
        error=error_text,
        status_code=detail.get("status_code"),
    )


__all__ = [
    "HOP_DEFERRAL_GATE_KEY",
    "SKIP_GATE_NEXT_ADMIT_BLOCKED",
    "release_deferred_conductor_hops",
    "build_conductor_hop_idempotency_key",
    "build_hop_team_dispatch_body",
    "budget_ok_for_hop",
    "hop_body_build_refused",
    "hop_owed",
    "live_conductor_row_on_thread",
    "merge_conductor_closeout_hop_authority",
    "maybe_fire_conductor_hop_reactor",
    "mission_open_for_row",
    "post_conductor_hop_team_dispatch",
]
