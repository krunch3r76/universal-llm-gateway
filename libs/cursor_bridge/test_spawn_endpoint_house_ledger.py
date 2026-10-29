"""Acceptance tests for spawn-endpoint-house-ledger (AC1–AC5, AC12).

F3a (RULING 21088#158 + 21088#190) — rule of record for ``ingest_report_turn``:

1. ``ingest_report_turn`` takes the marker from typed fields only, never by substring
   search: the subject's leading token (``[A-Z][A-Z_]*`` plus an optional ``|``, R3 boundary:
   token followed by ``|``, ``:``, whitespace, or end).
2. For ``CLOSEOUT``, the body JSON ``status``, qualified by ``status_incomplete_class`` as
   ``partial:<class>``, else the first whitespace-delimited word after ``CLOSEOUT`` or
   ``CLOSEOUT|`` in the subject (JSON wins).
3. GIW park-notice subjects (``cursor-sdk dispatch <id> PARKED…``) and body line
   ``stop: PARKED_TRANSPORT`` when the subject has no leading token.
4. Only when the subject has no leading token, the leading token of the first body line
   that is not ``ledger_row:`` or ``purpose:`` maps with the same table as a subject token
   (R1).
5. ``CLOSEOUT partial:capture`` and ``partial:consult`` map to ``done``; ``partial:work``,
   ``partial:park``, and bare ``partial`` map to ``parked``; ``gated`` maps to ``failed``.
6. ``PARKED`` and ``PARKED|`` map to ``parked`` (R2). Bare ``DONE`` / bare ``STATUS`` →
   ``unknown`` with ``source_status`` ``DONE`` / ``STATUS``.
7. Any other leading subject token yields ``unknown`` without reading the body.
8. ``source_status`` is the marker that was mapped, else the subject leading token label,
   else ``null``.

Corrections where bus turns override prior design shorthand:
- **21088#151** real bus JSON includes ``"status_incomplete_class":"capture"``; golden row
  151 uses that body for the partial→done path.
- **21088#92** park notice uses body line ``stop: PARKED_TRANSPORT``; subject is the
  ``cursor-sdk dispatch … PARKED`` form (F5 wording fix).
- **21088#37** STEER row carries CLOSEOUT complete→done trap text in the body; subject
  ``STEER`` must not read the body (substring ``CLOSEOUT complete`` must not map ``done``).

Parametrized golden table: ``test_ingest_report_marker_golden_21088`` (design §3 + F1–F7).
Dispatch-id-only match: ``test_ingest_report_dispatch_id_isolated_from_thread`` (F7).
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

import cursor_bridge.spawn_endpoint_house_ledger as ledger

_DISPATCH_IN_SUBJECT = re.compile(r"([0-9a-f]{12}-[0-9a-f]{8}(?:-r\d+)?)")

_BODY_151 = (
    '{"schema_version":1,"status":"partial","status_incomplete_class":"capture",'
    '"capture_status":"partial","work_outcome":"unverified"}'
)
_BODY_128 = (
    '{"schema_version":1,"status":"partial","status_incomplete_class":"consult",'
    '"capture_status":"complete","work_outcome":"unverified"}'
)
_BODY_97 = (
    '{"schema_version":1,"status":"partial","status_incomplete_class":"capture",'
    '"capture_status":"partial","work_outcome":"unverified"}'
)
_BODY_156 = (
    '{"schema_version":1,"status":"partial","status_incomplete_class":"capture",'
    '"capture_status":"partial","work_outcome":"unverified"}'
)
_SUBJ_151 = (
    "CLOSEOUT|cursor-sdk CLOSEOUT 887d997ebee5-4d67618c "
    "contract=conductor caller=web-anthropic"
)
_SUBJ_128 = (
    "CLOSEOUT|cursor-sdk CLOSEOUT e2e3e6515bee-ba27b08f "
    "contract=conductor caller=conductor-hop"
)
_SUBJ_97 = (
    "CLOSEOUT|cursor-sdk CLOSEOUT e58592b9923f-5b5ed39e-r1 "
    "contract=conductor admitted_via=giw_park_resume caller=web-anthropic"
)
_SUBJ_156 = (
    "CLOSEOUT|cursor-sdk CLOSEOUT 2633980ced39-5cb91fc5 "
    "contract=conductor caller=web-anthropic"
)
_BODY_11 = (
    "DONE| gate=G1 witness only, not land.\n"
    "todo:spawn-endpoint-house-ledger\n"
    "Next: G2.\n"
)
_SUBJ_92 = (
    "cursor-sdk dispatch e58592b9923f-5b5ed39e PARKED "
    "(for GIW restart 419cbca1-1c08-4332-a15a-4c1ea16aceb0)"
)
_BODY_92 = (
    "stop: PARKED_TRANSPORT\n"
    "PARKED_TRANSPORT wake=giw_restart:419cbca1-1c08-4332-a15a-4c1ea16aceb0\n\n"
    '{"status":"parked"}\n'
)
_BODY_3 = (
    "## Derived (projected at post — do not hand-edit)\n\n"
    "### Child lanes\n_none substantiated_\n\n"
    "### In-flight producers\nproducers: 2 in_flight\n"
)
_BODY_155 = (
    "## Derived (projected at post — do not hand-edit)\n\n"
    "### Child lanes\n- agent-bus:21183\n\n"
    "### In-flight producers\nproducers: 1 in_flight\n"
)
_BODY_154 = (
    "QUESTION (ruling 21088#145 §4). G6 r4 harvested.\n"
    "AC12 fails again.\n"
    "stop: ROW_PINNED.\n"
)
_BODY_113 = (
    "RULING|todo:spawn-endpoint-house-ledger — G6 (maestro seat)\n"
    "data: CLOSEOUT … partial\n"
    "ruling:\n1. G6 reviewer\n"
)
_BODY_152 = (
    "execution_id: `647ddc5e-de39-4f04-92bb-50c7eba747e8`\n"
    "execution_id: `c9a27e916c2c49e7857392f32063de51`\n"
    "# CDP generate result (opus-5.5-high)\n"
)
_BODY_37 = (
    '{"dispatch_id":"615c1b8cc57a-1a8dc466",'
    '"directive":"RULING from maestro seat 2406a13d (lane 12286)",'
    '"trap":"CLOSEOUT complete must not map STEER subject to done"}'
)


def _golden(
    case_id: str,
    bus_ref: str,
    subject: str,
    case_body: str,
    expected_status: str,
    expected_source_status: str | None,
    *,
    xfail_f: str | None = None,
    body_is_full: bool = False,
) -> Any:
    marks = (
        [pytest.mark.xfail(strict=True, reason=xfail_f)] if xfail_f else []
    )
    return pytest.param(
        case_id,
        bus_ref,
        subject,
        case_body,
        expected_status,
        expected_source_status,
        body_is_full,
        marks=marks,
        id=case_id,
    )


_GOLDEN_21088 = (
    _golden("151", "21088#151", _SUBJ_151, _BODY_151, "done", "CLOSEOUT partial:capture"),
    _golden("128", "21088#128", _SUBJ_128, _BODY_128, "done", "CLOSEOUT partial:consult"),
    _golden("97", "21088#97", _SUBJ_97, _BODY_97, "done", "CLOSEOUT partial:capture"),
    _golden("156", "21088#156", _SUBJ_156, _BODY_156, "done", "CLOSEOUT partial:capture"),
    _golden(
        "11",
        "21088#11",
        "DONE| G1 witness todo:spawn-endpoint-house-ledger",
        _BODY_11,
        "done",
        "DONE|",
    ),
    _golden("92", "21088#92", _SUBJ_92, _BODY_92, "parked", "PARKED"),
    _golden("3", "21088#3", "CHECKPOINT — hop 1", _BODY_3, "live", "CHECKPOINT"),
    _golden(
        "155",
        "21088#155",
        "CHECKPOINT spawn-endpoint-house-ledger hop 33 ROW_PINNED",
        _BODY_155,
        "live",
        "CHECKPOINT",
    ),
    _golden(
        "154",
        "21088#154",
        "QUESTION|todo:spawn-endpoint-house-ledger — G6 r4 red on AC12 again",
        _BODY_154,
        "unknown",
        "QUESTION|",
    ),
    _golden(
        "113",
        "21088#113",
        "RULING|todo:spawn-endpoint-house-ledger — G6 reviewer anthropic/claude-sonnet-5-5",
        _BODY_113,
        "unknown",
        "RULING|",
    ),
    _golden("37", "21088#37", "STEER directive", _BODY_37, "unknown", "STEER"),
    _golden("152", "21088#152", "cdp reply — 647ddc5e", _BODY_152, "unknown", None),
    _golden("r3-falsifier", "21088#158", "CLOSEOUT complete", "", "done", "CLOSEOUT complete"),
    _golden(
        "F1",
        "21088#162",
        "cdp reply — x",
        "DONE| shipped",
        "done",
        "DONE|",
    ),
    _golden(
        "F2",
        "21088#162",
        "CLOSEOUT|todo:fix-failed-ingest",
        "plain body no json",
        "unknown",
        "CLOSEOUT|",
    ),
    _golden(
        "F3",
        "21088#162",
        "QUESTION|why is cursor-sdk dispatch abc parked?",
        "body",
        "unknown",
        "QUESTION|",
    ),
    _golden("F4", "21088#162", "PARKED|", "body", "parked", "PARKED|"),
    _golden(
        "F5-body-stop",
        "21088#92",
        "cdp reply — park transport only in body",
        "stop: PARKED_TRANSPORT\n",
        "parked",
        "PARKED",
    ),
    _golden(
        "F6-checkpoint-pipe",
        "21088#162",
        "CHECKPOINT| hop 1",
        "body",
        "live",
        "CHECKPOINT|",
    ),
    _golden(
        "F6-bare-done",
        "21088#162",
        "DONE shipped",
        "body",
        "unknown",
        "DONE",
    ),
    _golden(
        "F6-bare-status",
        "21088#162",
        "STATUS alive",
        "body",
        "unknown",
        "STATUS",
    ),
    _golden(
        "F6-incomplete-json",
        "21088#162",
        "CLOSEOUT|x",
        '{"status":"incomplete"}',
        "unknown",
        "CLOSEOUT incomplete",
    ),
    _golden(
        "F7-park-substring",
        "21088#190",
        "cdp reply — re cursor-sdk dispatch abc PARKED earlier",
        "plain",
        "unknown",
        None,
    ),
    _golden(
        "F7-park-case",
        "21088#190",
        "cursor-sdk dispatch abc parked? asking",
        "plain",
        "unknown",
        None,
    ),
    _golden(
        "F7-body-status",
        "21088#190",
        "cdp reply — x",
        "STATUS| alive",
        "live",
        "STATUS|",
    ),
    _golden(
        "F7-body-checkpoint",
        "21088#190",
        "cdp reply — x",
        "CHECKPOINT hop 2",
        "live",
        "CHECKPOINT",
    ),
    _golden(
        "F7-closeout-partial-subject",
        "21088#190",
        "CLOSEOUT partial:capture",
        "plain no json",
        "done",
        "CLOSEOUT partial:capture",
    ),
    _golden(
        "F7-quiet-subject",
        "21088#190",
        "Quiet with work in flight",
        "plain",
        "unknown",
        None,
    ),
    _golden(
        "F7-bare-done-body",
        "21088#190",
        "cdp reply — x",
        "DONE shipped",
        "unknown",
        "DONE",
    ),
)

pytestmark = pytest.mark.offline


def test_missing_kind_reads_as_wake(tmp_path: Path) -> None:
    path = ledger.ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"agent_id": "a1", "ok": True, "op": "wake_tab"}) + "\n",
        encoding="utf-8",
    )
    assert ledger.last_successful_wake("a1", watch_dir=tmp_path) is not None


def test_line_at_4kb_rejected(tmp_path: Path) -> None:
    blob = "x" * 4096
    out = ledger.record_spawn(
        surface="cursor_sdk",
        thread="t1",
        purpose=blob,
        purpose_unstated=False,
        watch_dir=tmp_path,
    )
    assert out == {"ok": False, "error": "line_too_long"}
    assert not ledger.ledger_path(tmp_path).exists()


def test_partial_trailing_line_ignored(tmp_path: Path) -> None:
    path = ledger.ledger_path(tmp_path)
    path.write_bytes(b'{"kind":"wake","agent_id":"a","ok":true}')
    assert ledger.last_successful_wake("a", watch_dir=tmp_path) is None


def test_append_leading_newline_when_file_unterminated(tmp_path: Path) -> None:
    path = ledger.ledger_path(tmp_path)
    path.write_bytes(b'{"kind":"wake","agent_id":"a","ok":true}')
    ledger.record_tab_wake(
        gui_host=":8",
        agent_id="b",
        op="wake_tab",
        ok=True,
        watch_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") >= 2
    assert not text.endswith('{"kind":"wake"')


def test_surface_enum_rejects_unknown(tmp_path: Path) -> None:
    out = ledger.record_spawn(
        surface="unknown",
        thread="t",
        purpose="p",
        purpose_unstated=False,
        watch_dir=tmp_path,
    )
    assert out["ok"] is False
    assert out["error"] == "surface_invalid"


def test_duplicate_row_id_rejected(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    first = ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=False,
        row_id=rid,
        watch_dir=tmp_path,
    )
    assert first["ok"] is True
    second = ledger.record_spawn(
        surface="cdp",
        thread="t2",
        purpose="p2",
        purpose_unstated=False,
        row_id=rid,
        watch_dir=tmp_path,
    )
    assert second == {"ok": False, "error": "duplicate_row_id"}


def test_row_id_is_32_hex_not_execution_id(tmp_path: Path) -> None:
    execution_id = str(uuid.uuid4())
    out = ledger.record_spawn(
        surface="cdp",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        execution_id=execution_id,
        watch_dir=tmp_path,
    )
    assert out["ok"] is True
    assert out["row_id"] != execution_id
    assert len(out["row_id"]) == 32


def test_terminal_source_beats_ledger_status(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    ledger.record_status_row(
        row_id=rid,
        status="live",
        source_status="STATUS| ok",
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(
        rid,
        watch_dir=tmp_path,
        terminal_source="CLOSEOUT complete",
    )
    assert proj["status"] == "done"
    assert proj["source_status"] == "CLOSEOUT complete"


def test_ledger_report_when_no_terminal(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    ledger.record_report_row(
        row_id=rid,
        status="parked",
        source_status="PARKED",
        thread="t",
        turn=3,
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(rid, watch_dir=tmp_path)
    assert proj["status"] == "parked"


def test_registry_parked_is_read_only(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(
        rid,
        watch_dir=tmp_path,
        registry_read=lambda: "PARKED",
        giw_read=lambda: None,
    )
    assert proj["status"] == "parked"


def test_status_map_closeout_and_markers() -> None:
    assert ledger.map_source_status("CLOSEOUT partial") == "parked"
    assert ledger.map_source_status("DONE| shipped") == "done"
    assert ledger.map_source_status("STATUS| alive") == "live"
    assert ledger.map_source_status("seat_closed_at=1") == "done"


def test_status_miss_returns_unknown(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(rid, watch_dir=tmp_path)
    assert proj["status"] == "unknown"
    assert proj["source_status"] is None


def test_no_grace_constant() -> None:
    assert not any("grace" in name for name in dir(ledger))


def test_ingest_report_prefers_body_row_id(tmp_path: Path) -> None:
    rid_a = uuid.uuid4().hex
    rid_b = uuid.uuid4().hex
    for rid in (rid_a, rid_b):
        ledger.record_spawn(
            surface="cursor_sdk",
            thread=f"t-{rid[:4]}",
            purpose="p",
            purpose_unstated=True,
            row_id=rid,
            watch_dir=tmp_path,
        )
    body = f"ledger_row: {rid_b}\nrest"
    out = ledger.ingest_report_turn(body, "other", "t-x", turn=2, watch_dir=tmp_path)
    assert out["kind"] == "report"
    assert out["row_id"] == rid_b


def test_ingest_report_turn_from_caller(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        f"ledger_row: {rid}\n",
        "",
        "t",
        turn=7,
        watch_dir=tmp_path,
    )
    assert out["turn"] == 7


def test_append_row_oserror_returns_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: OSError on append escapes to fire_cse_followup / relay_wake_tab."""
    path = ledger.ledger_path(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(path.__class__, "open", _boom)
    out = ledger.record_tab_wake(
        gui_host=":8",
        agent_id="a",
        op="wake_tab",
        ok=True,
        watch_dir=tmp_path,
    )
    assert out == {"ok": False, "error": "io_error", "detail": "OSError"}


def test_patch_spawn_row_appends_without_rewrite(tmp_path: Path) -> None:
    """Break: patch_spawn_row rewrites the file and drops concurrent appends."""
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    ledger.record_tab_wake(
        gui_host=":8",
        agent_id="a",
        op="wake_tab",
        ok=True,
        watch_dir=tmp_path,
    )
    out = ledger.patch_spawn_row(rid, dispatch_id="disp-1", watch_dir=tmp_path)
    assert out["ok"] is True
    raw = ledger.read_all_rows(ledger.ledger_path(tmp_path))
    assert sum(1 for r in raw if r.get("kind") == "spawn") == 1
    assert any(r.get("kind") == "patch" for r in raw)
    assert len([r for r in raw if r.get("kind") == "wake"]) == 1
    folded = ledger._fold_spawn_rows(raw)
    assert folded[rid]["dispatch_id"] == "disp-1"


def test_ingest_report_no_matching_spawn(tmp_path: Path) -> None:
    out = ledger.ingest_report_turn("body", "subj", "t-none", watch_dir=tmp_path)
    assert out == {"ok": False, "error": "no_matching_spawn"}


def test_ingest_report_closeout_complete_subject_maps_done(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        f"ledger_row: {rid}\n",
        "CLOSEOUT complete",
        "t",
        turn=4,
        watch_dir=tmp_path,
    )
    assert out["status"] == "done"
    assert out["source_status"] == "CLOSEOUT complete"


def test_ingest_report_no_marker_stores_unknown_not_live(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        f"ledger_row: {rid}\n",
        "",
        "t",
        watch_dir=tmp_path,
    )
    assert out["status"] == "unknown"
    assert out.get("source_status") is None


def test_ingest_report_unmapped_marker_stored_unknown(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        f"ledger_row: {rid}\n",
        "NOT_A_KNOWN_MARKER",
        "t",
        watch_dir=tmp_path,
    )
    assert out["status"] == "unknown"
    assert out["source_status"] == "NOT_A_KNOWN_MARKER"
    proj = ledger.project_row_status(rid, watch_dir=tmp_path)
    assert proj["status"] == "unknown"


def test_read_house_ledger_object_disambiguates_same_thread(tmp_path: Path) -> None:
    rid_a = uuid.uuid4().hex
    rid_b = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_ide",
        thread="shared-thread",
        purpose="a",
        purpose_unstated=False,
        row_id=rid_a,
        dispatch_id="disp-a",
        watch_dir=tmp_path,
    )
    ledger.record_spawn(
        surface="cursor_ide",
        thread="shared-thread",
        purpose="b",
        purpose_unstated=False,
        row_id=rid_b,
        dispatch_id="disp-b",
        watch_dir=tmp_path,
    )
    detail = ledger.read_house_ledger(
        house={"thread": "shared-thread", "dispatch_id": "disp-b"},
        watch_dir=tmp_path,
    )
    assert len(detail["rows"]) == 1
    assert detail["rows"][0]["row_id"] == rid_b


@pytest.mark.parametrize(
    (
        "case_id",
        "bus_ref",
        "subject",
        "case_body",
        "expected_status",
        "expected_source_status",
        "body_is_full",
    ),
    _GOLDEN_21088,
)
def test_ingest_report_marker_golden_21088(
    tmp_path: Path,
    case_id: str,
    bus_ref: str,
    subject: str,
    case_body: str,
    expected_status: str,
    expected_source_status: str | None,
    body_is_full: bool,
) -> None:
    """One row → status + source_status under F3a (bus_ref cites agent-bus:21088 turn)."""
    dispatch_id = _DISPATCH_IN_SUBJECT.search(subject)
    dispatch_id = dispatch_id.group(1) if dispatch_id else None
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="21088",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        dispatch_id=dispatch_id,
        watch_dir=tmp_path,
    )
    if body_is_full:
        body = case_body
    else:
        body = f"ledger_row: {rid}\n{case_body}"
    out = ledger.ingest_report_turn(
        body,
        subject,
        "21088",
        turn=1,
        watch_dir=tmp_path,
    )
    assert out["kind"] == "report"
    assert out["status"] == expected_status, bus_ref
    assert out["source_status"] == expected_source_status, bus_ref


def test_ingest_report_dispatch_id_isolated_from_thread(tmp_path: Path) -> None:
    """F7: row match via dispatch_id in subject only — wrong ingest thread must not match."""
    target_dispatch = "887d997ebee5-4d67618c"
    rid_target = uuid.uuid4().hex
    rid_other = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="21088",
        purpose="target",
        purpose_unstated=True,
        row_id=rid_target,
        dispatch_id=target_dispatch,
        watch_dir=tmp_path,
    )
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="21088",
        purpose="other",
        purpose_unstated=True,
        row_id=rid_other,
        dispatch_id="000000000000-00000000",
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        _BODY_151,
        _SUBJ_151,
        "99999",
        turn=151,
        watch_dir=tmp_path,
    )
    assert out["kind"] == "report"
    assert out["row_id"] == rid_target
    assert out["status"] == "done"
    assert out["source_status"] == "CLOSEOUT partial:capture"


def test_ingest_report_turn_null_when_omitted(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        f"ledger_row: {rid}\n",
        "",
        "t",
        watch_dir=tmp_path,
    )
    assert out["turn"] is None
