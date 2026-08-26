"""Provenance, topic, attention age, and newly registered SDK handlers."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.board_lines import attention_line
from scripts.model_manager.ui.dispatch_monitor.core.dtos import (
    AttentionItem,
    CdpLegRow,
    SdkDispatchRow,
)
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event


def test_attention_line_keeps_age_prefix_when_title_is_long() -> None:
    """Reserved-left age must survive truncation of a long kind/title."""
    item = AttentionItem(
        key="sdk.dispatch.idle:disp-long",
        kind="sdk.dispatch.idle",
        severity="warn",
        subject="disp-long-" + ("x" * 40),
        title="Dispatch has emitted no progress recently " + ("y" * 80),
        since_ms=1_000,
        age_ms=125_000,
    )
    line = attention_line(item, width=60, now_ms=126_000)
    assert len(line) == 60
    assert "2m05s" in line
    assert line.index("2m05s") < line.index("[sdk.dispatch.idle]")


def test_topic_and_nest_under_absorb_from_dispatched() -> None:
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {
                "dispatch_id": "disp-topic",
                "execution_id": "disp-topic",
                "topic": "ULG gains glanceable dispatch topics",
                "nest_under": "parent-disp",
                "asked_by": "web-anthropic",
                "purpose": "intent: implement",
            },
        )
    )
    row = next(r for r in model.derive(2_000).sdk if r.dispatch_id == "disp-topic")
    assert row.topic == "ULG gains glanceable dispatch topics"
    assert row.nest_under == "parent-disp"
    assert row.purpose == "intent: implement"
    assert row.asked_by == "web-anthropic"


def test_resumed_does_not_mint_a_second_live_identity() -> None:
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": "parent", "execution_id": "parent"},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_100,
            {"dispatch_id": "child", "execution_id": "child"},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_RESUMED,
            1_200,
            {
                "dispatch_id": "child",
                "resume_of": "parent",
                "sdk_agent_id": "agent-1",
                "state_root": "/tmp/state",
                "thread_id": "t1",
                "execution_id": "exec-child",
            },
        )
    )
    frame = model.derive(1_300)
    live = [row for row in frame.sdk if row.terminal_ms is None]
    assert len(live) == 1
    assert live[0].dispatch_id == "parent"
    assert live[0].resume_of is None
    assert frame.health.unhandled_signals == {}


def test_resumed_of_terminal_parent_keeps_one_new_live_row() -> None:
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": "parent", "execution_id": "parent"},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_COMPLETED,
            1_500,
            {"dispatch_id": "parent", "execution_id": "parent", "status": "completed"},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_RESUMED,
            2_000,
            {
                "dispatch_id": "child",
                "resume_of": "parent",
                "sdk_agent_id": "agent-1",
                "state_root": "/tmp/state",
                "thread_id": "t1",
                "execution_id": "exec-child",
            },
        )
    )
    frame = model.derive(2_100)
    live = [row for row in frame.sdk if row.terminal_ms is None]
    assert len(live) == 1
    assert live[0].dispatch_id == "child"
    assert live[0].resume_of == "parent"
    assert any(
        e.kind == "resume_of" and e.from_id == "child" and e.to_id == "parent"
        for e in frame.relations
    )


def test_partial_work_specimen_is_handled_not_unhandled() -> None:
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": "disp-pw", "execution_id": "disp-pw"},
        )
    )
    model.apply(
        Event(
            signals.SDK_CLOSEOUT_PARTIAL_WORK_PRODUCTION_SPECIMEN,
            2_000,
            {
                "dispatch_id": "disp-pw",
                "envelope_turn": 4,
                "thread_id": "6592",
                "closeout_source": "relay",
                "contract": "implement",
                "replay_mode": False,
            },
        )
    )
    frame = model.derive(3_000)
    assert frame.health.unhandled_signals == {}
    row = next(r for r in frame.sdk if r.dispatch_id == "disp-pw")
    assert row.terminal_ms is None
    assert row.thread_id == "6592"


def test_duplicate_refused_is_attention_not_a_live_row() -> None:
    model = Model()
    model.apply(
        Event(
            signals.SDK_ADMIT_DUPLICATE_REFUSED,
            1_000,
            {
                "dispatch_id": "dup-new",
                "thread_id": "6164",
                "work_fingerprint": "abc",
                "holder_dispatch_id": "dup-holder",
            },
        )
    )
    frame = model.derive(2_000)
    assert frame.health.unhandled_signals == {}
    assert frame.sdk == ()
    item = next(i for i in frame.attention if i.kind == "sdk.admit.duplicate_refused")
    assert item.subject == "dup-new"
    assert item.since_ms == 1_000
    assert "dup-holder" in item.detail


def test_lease_acquired_stamps_started_ms_when_missing() -> None:
    model = Model()
    model.apply(
        Event(
            signals.SDK_LEASE_ACQUIRED,
            5_000,
            {"dispatch_id": "disp-lease", "source_repo": "/repo"},
        )
    )
    row = next(r for r in model.derive(6_000).sdk if r.dispatch_id == "disp-lease")
    assert row.started_ms == 5_000
    assert row.state == "running"
    assert row.elapsed_ms == 1_000


def test_park_and_correlation_project_relation_edges() -> None:
    model = Model()
    model.apply(
        Event(
            "manage.charter.tick.admitted",
            1_000,
            {"root": "9001", "worker_thread": "9002"},
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_100,
            {
                "dispatch_id": "parent",
                "execution_id": "parent",
                "thread_id": "9002",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_LEASE_PARK_ENTER,
            1_200,
            {"parent_id": "parent", "child_id": "child"},
        )
    )
    frame = model.derive(1_300)
    kinds = {(e.kind, e.from_id, e.to_id) for e in frame.relations}
    assert ("lease_park", "parent", "child") in kinds
    assert ("root_dispatch", "9001", "parent") in kinds


def test_closeout_uri_lands_on_attention_target_uri() -> None:
    model = Model()
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            1_000,
            {"dispatch_id": "disp-uri", "execution_id": "disp-uri"},
        )
    )
    model.apply(
        Event(
            signals.SDK_CLOSEOUT_RELOCATED,
            2_000,
            {
                "dispatch_id": "disp-uri",
                "uri": "cortex://notes/system/threads/closeout.md",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_FAILED,
            3_000,
            {
                "dispatch_id": "disp-uri",
                "execution_id": "disp-uri",
                "error": "boom",
            },
        )
    )
    frame = model.derive(4_000)
    failed = next(i for i in frame.attention if i.kind == "sdk.dispatch.failed")
    assert failed.target_uri == "cortex://notes/system/threads/closeout.md"


def test_drop_attention_stamps_since_ms() -> None:
    model = Model()
    model.apply(Event(signals.EVENTS_DROPPED_INGEST, 4_000, {"count": 2}))
    item = next(
        i for i in model.derive(5_000).attention if i.kind == "events.dropped.ingest"
    )
    assert item.since_ms == 4_000
    assert item.age_ms == 1_000


def test_sdk_live_line_paints_topic_when_present() -> None:
    from scripts.model_manager.ui.dispatch_monitor.core.board_lines import sdk_live_line

    row = SdkDispatchRow(
        dispatch_id="auto-topic",
        state="running",
        root_id="6186",
        thread_id="5867",
        model="composer-2.5",
        elapsed_ms=3_000,
        idle_age_ms=0,
        emitters_seen=("worker",),
        topic="ULG gains a topic line",
        nest_under="parent-1",
        admitted_via="cursor-auto",
        asked_by="web-anthropic",
        provenance="signal",
        caller_from="ide",
        caller_via="http",
    )
    line = sdk_live_line(row, width=200)
    assert "topic=ULG gains a topic line" in line
    assert "nest=parent-1" in line
    assert "from=ide" in line


def test_cdp_line_labels_url_and_thread_without_req_exec() -> None:
    from scripts.model_manager.ui.dispatch_monitor.core.watch import (
        _cdp_line,
        cdp_id_legend,
        render,
    )

    chat_url = "claude.ai/cowork/cse_abc123"
    req_id = "b72ae1a039f2"
    row = CdpLegRow(
        request_id=req_id,
        execution_id="4182c834-696d-4abc-8def-0123456789ab",
        thread_id="6329",
        chat_url=chat_url,
        model="cdp/fable-5",
        caller_agent="cursor",
        state="admitted",
        elapsed_ms=3_493_000,
        topic="bind the CDP id legend",
    )
    line = _cdp_line(row)
    assert f"url={chat_url}" in line
    assert "th=6329" in line
    assert "topic=bind the CDP id legend" in line
    assert "req=" not in line
    assert "exec=" not in line
    assert "lane=" not in line
    assert line.index("url=") < line.index("th=")
    narrow = _cdp_line(row, width=40)
    assert f"url={chat_url}" in narrow
    assert "ids: url=CSE chat (when bound)" in cdp_id_legend()
    assert "req=" not in cdp_id_legend()

    no_url = CdpLegRow(request_id=req_id, thread_id="6329", state="admitted")
    bare = _cdp_line(no_url)
    assert "url=" not in bare
    assert "th=6329" in bare
    assert "req=" not in bare
    assert "exec=" not in bare

    model = Model()
    model.apply(
        Event(
            signals.CDP_ADMITTED,
            1_000,
            {
                "request_id": req_id,
                "execution_id": "4182c834-696d-4abc-8def-0123456789ab",
                "thread_id": "6329",
                "model": "cdp/fable-5",
                "caller_agent": "cursor",
            },
        )
    )
    model.apply(
        Event(
            signals.AGENTBUS_THREAD_CSE_BOUND,
            1_100,
            {
                "thread_id": "6329",
                "cse_chat_url": f"https://{chat_url}/",
            },
        )
    )
    text = render(model.derive(2_000))
    assert cdp_id_legend() in text
    assert f"url={chat_url}" in text
    assert "th=6329" in text
    assert "req=" not in text
    assert "exec=" not in text
