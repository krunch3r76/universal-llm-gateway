"""Standing-handoff and bus projection for the hop-successor prompt.

``ensure_operator_proxy_mission_prompt`` fills the template from HOUSE RULES,
a live leg projected from agent-bus state, and pointer lines. This module does
not author the old This-hop card.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hop_handoff.body import (
    parse_hop_kind,
    parse_successor_birth_id,
    parse_superseded_registration_id,
)
from hop_handoff.standing_handoff import standing_handoff_path, standing_handoff_uri
from implement_admission.closeout_helpers import cortex_files_root

from claude_bundles.journal_hop_projection import (
    JOURNAL_HEAD_BYTES,
    MAESTRO_JOURNAL_URI,
    MAESTRO_REGISTER_URI,
    ReadJournalHead,
    hop_journal_projection_fields,
)
from claude_bundles.maestro_runbook_load import load_maestro_runbook
from claude_bundles.operator_proxy_bus_fetch import lineage_status_for_execution
from claude_bundles.operator_proxy_live_leg import (
    FoldStateSaver,
    FoldStateStore,
    house_lanes,
    load_maestro_roster_text,
    project_live_leg,
    render_live_leg,
)
from claude_bundles.operator_proxy_standing_render import (
    HOP_INFLIGHT_CAP_B,
    HOP_LIVE_LEG_CAP_B,
    HOP_POINTERS_CAP_B,
    HOP_RUNNING_NOW_CAP_B,
    _extract_current_leg_block,
    cap_utf8_for_hop,
    current_leg_has_inflight_section,
    extract_inflight_work_keys,
    hop_boundary_turn_from_header,
    hop_house_rules_pointer,
    inflight_section_from_sidecar,
    measure_checkpoint_budget_bytes,
    render_budgets_table_block,
    render_checkpoint_block,
    render_house_rules_block,
    render_inflight_block,
    render_pointers_block,
    runbook_refuse_steps_bytes,
)

# CONSUMERS = import-nomination (GIW). INJECTORS = seat paste (cdp_ask).
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

UNKNOWN_FIELD = "unknown"
MISSING_HANDOFF_INSTRUCTION = (
    "The S7 standing-handoff state file is absent.\n"
    "Lane-tip reconstruction is degraded, not equivalent.\n"
    "Author the standing handoff before you leave."
)

_THREAD_RE = re.compile(
    r"^(?:thread_id|parent_thread):\s*(\d+)\s*$",
    re.MULTILINE,
)
_ARC_RE = re.compile(r"^arc:\s*(?:agent-bus:)?(\d+)\s*$", re.MULTILINE)
_LANE_ID_RE = re.compile(r"^lane:\s*(?:agent-bus[:\s]+)?(\d+)", re.MULTILINE)
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_HOUSE_RULES_HEADING = "HOUSE RULES"


def extract_thread_id(text: str) -> str | None:
    """Return the private-lane thread id when the prompt names one.

    Reads ``thread_id`` / ``parent_thread`` first, then ``lane: agent-bus…``,
    then ``arc:``. Does not invent an id from prose.
    """
    for pattern in (_THREAD_RE, _LANE_ID_RE, _ARC_RE):
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return None


def _render_failed(block: str, exc: BaseException, uri: str) -> str:
    return f"[render_failed {block}: {type(exc).__name__} — read {uri}]"


def _is_standing_handoff_failure(text: str | None) -> bool:
    return bool(text) and text.startswith("[render_failed standing_handoff:")


def standing_handoff_text_for_prompt(
    prompt: str,
    *,
    read_path: Callable[[str], str | None] | None = None,
) -> str | None:
    """Load the standing-handoff sidecar for the thread id named in *prompt*.

    Returns None when no thread id is parseable or the file is absent.
    Read and decode failures return a ``[render_failed standing_handoff: …]``
    line (truthy, so callers do not treat them as absence).
    *read_path* is a test seam; production reads ``standing_handoff_path``.
    """
    thread_id = extract_thread_id(prompt)
    if not thread_id:
        return None
    uri = standing_handoff_uri(thread_id)
    try:
        if read_path is not None:
            return read_path(thread_id)
        path = standing_handoff_path(thread_id)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        return _render_failed("standing_handoff", exc, uri)


def _heading_sections(text: str) -> list[tuple[str, str]]:
    """ATX sections as ``(heading, section text including the heading)``."""
    lines = (text or "").splitlines()
    starts: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = _ATX_HEADING_RE.match(line)
        if match is None:
            continue
        starts.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[tuple[str, str]] = []
    for number, (index, level, heading) in enumerate(starts):
        end = len(lines)
        for later in range(number + 1, len(starts)):
            if starts[later][1] <= level:
                end = starts[later][0]
                break
        sections.append((heading, "\n".join(lines[index:end]).rstrip()))
    return sections


def house_rules_section(text: str) -> str | None:
    """Return the HOUSE RULES block from the standing handoff, verbatim."""
    for heading, body in _heading_sections(text or ""):
        if _HOUSE_RULES_HEADING in heading:
            return body
    return None


def handoff_file_sha256(text: str) -> str:
    """sha256 of the whole sidecar text, not of the extracted section."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _cortex_path(uri_or_path: str) -> Path:
    rel = (uri_or_path or "").strip()
    if rel.startswith("cortex://"):
        rel = rel.removeprefix("cortex://").lstrip("/")
    return cortex_files_root() / rel


def cortex_archive_path_exists(path: str) -> bool:
    """True when *path* is a file under the cortex files root.

    Missing → False. ``OSError`` (permission, IO) propagates so callers do not
    report the archive as absent.
    """
    return _cortex_path(path).is_file()


def read_cortex_journal_head(uri: str, limit: int) -> tuple[str | None, int | None]:
    """Read at most *limit* bytes from a cortex journal URI; return ``(head, total)``.

    Missing file → ``(None, None)``. ``OSError`` / ``UnicodeError`` propagate
    (same discipline as ``read_cortex_uri_from_root``).
    """
    path = _cortex_path(uri)
    if not path.is_file():
        return None, None
    with path.open("rb") as handle:
        head = handle.read(limit)
        handle.seek(0, 2)
        total = handle.tell()
    if not head and total == 0:
        return "", 0
    cut = head
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore"), total


def read_cortex_uri_from_root(uri: str) -> str | None:
    """Read a ``cortex://`` URI from the cortex files root.

    Missing → None. ``OSError`` other than ``FileNotFoundError``, and
    ``UnicodeError``, propagate.
    """
    path = _cortex_path(uri)
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def production_standing_seams() -> dict[str, Any]:
    """Lineage, archive-exists, and cortex-read callables for production inject paths.

    ``hop_successor_fields`` does not apply these itself. Callers that omit them
    make no lineage HTTP request.
    """
    return {
        "lineage_lookup": lineage_status_for_execution,
        "archive_path_exists": cortex_archive_path_exists,
        "read_cortex_uri": read_cortex_uri_from_root,
        "read_journal_head": read_cortex_journal_head,
    }


def hop_successor_fields(
    prompt: str,
    *,
    standing_handoff_text: str | None = None,
    roster_text: str | None = None,
    fetch_turns: Callable[[str], list[dict[str, Any]]] | None = None,
    execution_id: str = "",
    journal_text: str | None = None,
    register_text: str | None = None,
    read_cortex_uri: Callable[[str], str | None] | None = None,
    read_journal_head: ReadJournalHead | None = None,
    inflight_projection_lookup: Callable[[], str | None] | None = None,
    role: str = "maestro",
    house_rules_text: str | None = None,
    lineage_lookup: Callable[[str, str], str | None] | None = None,
    archive_path_exists: Callable[[str], bool] | None = None,
    page_fetch: Callable[..., tuple[list[dict[str, Any]], bool, int | None]]
    | None = None,
    fold_state_load: FoldStateStore | None = None,
    fold_state_save: FoldStateSaver | None = None,
    act5_fetch_last: int | None = None,
) -> dict[str, str]:
    """Fill the hop-successor template from the prompt, handoff, and bus state."""
    prompt_text = prompt or ""
    lane = extract_thread_id(prompt_text) or UNKNOWN_FIELD
    birth = parse_successor_birth_id(prompt_text) or UNKNOWN_FIELD
    hop_kind = parse_hop_kind(prompt_text)
    handoff_failure = (
        standing_handoff_text
        if _is_standing_handoff_failure(standing_handoff_text)
        else ""
    )
    sidecar = "" if handoff_failure else (standing_handoff_text or "")
    exec_id = (execution_id or "").strip()
    execution_clause = f", execution_id {exec_id}" if exec_id else ""

    roster_unreadable: str | None = None
    if roster_text is not None:
        roster = roster_text
    else:
        loaded = load_maestro_roster_text()
        roster = loaded.text
        roster_unreadable = loaded.unreadable
    lanes = house_lanes(roster)
    roster_clause = (
        f"roster unreadable: {roster_unreadable}; " if roster_unreadable else ""
    )
    if fetch_turns is None:
        from claude_bundles.operator_proxy_bus_fetch import fetch_turns_for_lane

        fetch_turns = fetch_turns_for_lane

    superseded_reg = parse_superseded_registration_id(prompt or "")
    inline_rules = house_rules_section(sidecar) if sidecar else None

    checkpoint_block = ""
    inflight_block = ""
    inflight_count = 0
    checkpoint_header = ""
    inflight_work_keys: frozenset[str] | None = None
    hop_boundary_by_lane: dict[str, int] | None = None
    rules_uri = f"cortex://notes/system/threads/{lane}-house-rules.md"
    handoff_uri = standing_handoff_uri(lane)
    if sidecar:
        try:
            checkpoint_block, ck_meta = render_checkpoint_block(
                sidecar,
                lane=lane,
                inline_house_rules=inline_rules,
                path_exists=archive_path_exists,
                superseded_registration_id=superseded_reg,
                hop_inline=hop_kind == "mission",
            )
        except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
            checkpoint_block = (
                "## CURRENT LEG (rendered checkpoint)\n"
                + _render_failed("checkpoint", exc, handoff_uri)
            )
            ck_meta = {}
        inflight_rows = ck_meta.get("inflight_rows") or []
        inflight_count = len(inflight_rows)
        if inflight_rows:
            try:
                inflight_block = render_inflight_block(
                    inflight_rows,
                    lineage_lookup=lineage_lookup,
                    max_bytes=HOP_INFLIGHT_CAP_B if hop_kind == "mission" else None,
                )
            except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
                inflight_block = _render_failed("inflight", exc, handoff_uri)
        checkpoint_header = str(ck_meta.get("leg_header") or "")
        current_leg_block = _extract_current_leg_block(sidecar)
        if (
            hop_kind == "mission"
            and current_leg_block
            and current_leg_has_inflight_section(current_leg_block)
        ):
            inflight_section = inflight_section_from_sidecar(sidecar)
            inflight_work_keys = extract_inflight_work_keys(inflight_section)
            hop_boundary_by_lane = {
                lane: hop_boundary_turn_from_header(checkpoint_header)
            }
        if ck_meta.get("identity") == "stale":
            act5_fetch_last = 5

    try:
        if handoff_failure:
            house_rules_verbatim = handoff_failure
        elif not sidecar:
            house_rules_verbatim = MISSING_HANDOFF_INSTRUCTION
        elif hop_kind == "mission":
            rules_file_exists: bool | None = None
            if archive_path_exists is not None:
                rules_file_exists = bool(archive_path_exists(rules_uri))
            house_rules_verbatim = hop_house_rules_pointer(
                lane,
                rules_file_exists=rules_file_exists,
                handoff_has_rules=bool(inline_rules and inline_rules.strip()),
            )
        else:
            house_rules_verbatim, _rules_budget = render_house_rules_block(
                lane=lane,
                inline_rules=inline_rules,
                house_rules_text=house_rules_text,
                read_cortex_uri=read_cortex_uri,
            )
    except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
        house_rules_verbatim = _render_failed("house_rules", exc, rules_uri)

    try:
        leg = project_live_leg(
            lanes,
            fetch_turns,
            page_fetch=page_fetch,
            fold_state_load=fold_state_load,
            fold_state_save=fold_state_save,
            inflight_work_keys=inflight_work_keys,
            hop_boundary_by_lane=hop_boundary_by_lane,
        )
        live_leg = render_live_leg(
            leg,
            declared_in_flight=inflight_count,
            max_bytes=HOP_LIVE_LEG_CAP_B if hop_kind == "mission" else None,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
        live_leg = _render_failed("live_leg", exc, handoff_uri)
    lane_list = ", ".join(lanes) if lanes else "none"

    if handoff_failure:
        digest = ""
        detail = handoff_failure
    elif sidecar:
        digest = handoff_file_sha256(sidecar)
        detail = f"handoff sha256 {digest}"
    else:
        digest = ""
        detail = "standing handoff file absent"
    # Sha (or absence) stays on the first line, under HOP_RUNNING_NOW_CAP_B,
    # so a long lane list drops as a whole following line. One combined line
    # used to exceed the cap and the byte cut ate the sha (friction:39828).
    running_now = (
        f"## What is running now; {roster_clause}{detail}\non lanes {lane_list}"
    )

    pointers = render_pointers_block(
        checkpoint_preamble=checkpoint_header,
        rules_text=house_rules_verbatim,
        max_bytes=HOP_POINTERS_CAP_B if hop_kind == "mission" else None,
    )

    try:
        journal_fields = hop_journal_projection_fields(
            journal_text=journal_text,
            register_text=register_text,
            read_uri=read_cortex_uri,
            read_journal_head=read_journal_head or read_cortex_journal_head,
            archive_path_exists=archive_path_exists,
            role=role,
            inflight_projection_lookup=inflight_projection_lookup,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
        journal_fields = {
            "lessons_in_force": (
                "## Lessons in force\n\n"
                + _render_failed("lessons_in_force", exc, MAESTRO_JOURNAL_URI)
                + "\n"
            ),
            "register_card": (
                "## Register\n\n"
                + _render_failed("register_card", exc, MAESTRO_REGISTER_URI)
                + "\n"
            ),
        }

    fetch_last = act5_fetch_last if act5_fetch_last is not None else 3

    rules_bytes = (
        len((house_rules_verbatim or "").encode("utf-8"))
        if house_rules_verbatim
        else None
    )
    checkpoint_bytes = (
        measure_checkpoint_budget_bytes(sidecar, inline_house_rules=inline_rules)
        if sidecar and not handoff_failure
        else None
    )
    runbook_bytes: int | None = None
    try:
        runbook_body, _runbook_err = load_maestro_runbook()
        runbook_bytes = runbook_refuse_steps_bytes(runbook_body)
    except Exception:  # noqa: BLE001 — budgets table is fail-open
        runbook_bytes = None
    raw_journal_bytes = journal_fields.get("journal_head_bytes")
    journal_head_bytes = int(raw_journal_bytes) if raw_journal_bytes else None
    try:
        budgets_table = render_budgets_table_block(
            lane=lane,
            checkpoint_capped_bytes=checkpoint_bytes,
            house_rules_bytes=rules_bytes,
            runbook_bytes=runbook_bytes,
            journal_head_bytes=journal_head_bytes,
        )
        over_budget = journal_fields.get("journal_over_budget_flag") or ""
        if hop_kind == "housekeeping" and over_budget:
            budgets_table = f"{budgets_table}\n\n{over_budget}"
    except Exception as exc:  # noqa: BLE001 — fail-open; one block, not the hop
        budgets_table = _render_failed("budgets_table", exc, standing_handoff_uri(lane))

    if hop_kind == "mission":
        running_now = cap_utf8_for_hop(
            running_now, HOP_RUNNING_NOW_CAP_B, label="running_now"
        )

    return {
        "hop_kind": hop_kind,
        "lane": lane,
        "successor_birth_id": birth,
        "execution_clause": execution_clause,
        "handoff_sha": digest,
        "running_now_line": running_now,
        "house_rules_verbatim": house_rules_verbatim,
        "checkpoint_block": checkpoint_block,
        "inflight_block": inflight_block,
        "lessons_in_force": journal_fields["lessons_in_force"],
        "register_card": journal_fields["register_card"],
        "live_leg": live_leg,
        "pointers": pointers,
        "fetch_last": str(fetch_last),
        "budgets_table": budgets_table,
    }


__all__ = [
    "JOURNAL_HEAD_BYTES",
    "MISSING_HANDOFF_INSTRUCTION",
    "UNKNOWN_FIELD",
    "extract_thread_id",
    "handoff_file_sha256",
    "hop_successor_fields",
    "house_rules_section",
    "production_standing_seams",
    "read_cortex_journal_head",
    "standing_handoff_text_for_prompt",
]
