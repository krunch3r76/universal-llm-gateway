"""False-orphan demotion when late cursor-sdk terminal turns arrive."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    init_db,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.db.turns import get_turns, insert_turn
from agent_bus_store.reconcile import reconcile_orphaned_dispatches
from agent_bus_store.sdk_liveness import LivenessVerdict
from services.git_integration_worker.cursor_sdk_closeout_subject import (
    build_sdk_closeout_subject,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def _producer_closeout_subject(
    *,
    dispatch_id: str = "disp-false",
    contract: str = "investigate",
    caller_agent: str = "dispatch",
) -> str:
    req = CursorDispatchRequest(
        thread_id="1",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="closeout",
        caller_agent=caller_agent,
    )
    return build_sdk_closeout_subject(req, contract=contract)


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _seed_orphan_thread(
    bus_db,
    *,
    slug: str = "false-orphan",
    execution_id: str = "exec-false",
) -> str:
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer only",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent="claude-web",
    )
    def _allow_orphan(**_kwargs: object):
        return LivenessVerdict.ALLOW_ORPHAN, "probe_status_null", None

    with patch("agent_bus_store.reconcile.emit_dispatch_orphaned"):
        with patch(
            "agent_bus_store.reconcile.evaluate_link_liveness",
            side_effect=_allow_orphan,
        ):
            reconcile_orphaned_dispatches()
            reconcile_orphaned_dispatches()
    return thread_id


def _orphan_turns(thread_id: str) -> list[dict]:
    turns = get_turns(thread=thread_id, include_superseded=True)
    return [
        t
        for t in turns
        if t["from_agent"] == "dispatch"
        and "orphaned" in (t.get("subject") or "").lower()
    ]


def test_dispatch_ledger_resolves_under_pytest_tmp_not_live_gateway(
    tmp_path: Path,
) -> None:
    """Root conftest isolation beats inherited live ``CURSOR_SDK_DISPATCH_LEDGER``."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
        resolve_cursor_sdk_dispatch_ledger_path,
    )
    from services.git_integration_worker.cursor_home import operator_real_home

    path = resolve_cursor_sdk_dispatch_ledger_path()
    live_gateway = operator_real_home() / ".gateway"
    assert live_gateway not in path.parents
    expected = tmp_path / "giw-ledger-root" / "cursor-sdk-dispatch.db"
    assert path == expected.resolve()
    import os

    assert os.environ[CURSOR_SDK_DISPATCH_LEDGER_ENV] == str(expected)


_LEDGER_REGRESSION_NODE = (
    "libs/agent_bus_store/test_orphan_demote.py"
    "::test_dispatch_ledger_resolves_under_pytest_tmp_not_live_gateway"
)


def _run_ledger_regression_child(*extra_args: str):
    """Run the path regression in a fresh pytest that inherits a live-ledger export.

    The child env carries ``DATA_DIR`` and ``CURSOR_SDK_DISPATCH_LEDGER`` aimed
    at the operator's real ``~/.gateway``, as a dispatch shell would. The
    regression only resolves a path, and the pytest refuse guard stays on, so no
    live database is opened or written.
    """
    import os
    import subprocess
    import sys

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CURSOR_SDK_DISPATCH_LEDGER_ENV,
    )
    from services.git_integration_worker.cursor_home import operator_real_home

    repo = Path(__file__).resolve().parents[2]
    live_gateway = operator_real_home() / ".gateway"
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("PYTEST_ADDOPTS", None)
    env["PAGER_NOTIFY_ENABLED"] = "0"
    env["ULG_EVENTS_DISABLED"] = "1"
    env["DATA_DIR"] = str(live_gateway)
    env[CURSOR_SDK_DISPATCH_LEDGER_ENV] = str(live_gateway / "cursor-sdk-dispatch.db")
    argv = [
        sys.executable,
        "-m",
        "pytest",
        _LEDGER_REGRESSION_NODE,
        "-q",
        "-p",
        "no:cacheprovider",
        *extra_args,
    ]
    return subprocess.run(
        argv,
        cwd=repo,
        env=env,
        shell=False,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )


def test_root_conftest_isolates_inherited_live_ledger_env_in_child_pytest() -> None:
    """A child pytest inheriting the live ledger export still resolves a tmp ledger."""
    proc = _run_ledger_regression_child()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "1 passed" in proc.stdout, proc.stdout


def test_child_without_root_conftest_hits_live_ledger_guard() -> None:
    """Negative control: without conftest fixtures the inherited export reaches the guard."""
    proc = _run_ledger_regression_child("--noconftest")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "refusing to open the live dispatch ledger" in proc.stdout, proc.stdout


def test_late_closeout_demotes_orphan(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db)
    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"

    emitted: list[dict] = []
    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted",
        side_effect=lambda **kwargs: emitted.append(kwargs),
    ):
        closeout_id, _, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject=_producer_closeout_subject(),
            body="done",
        )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == closeout_id
    assert len(emitted) == 1
    assert emitted[0] == {
        "thread_id": thread_id,
        "orphan_turn_id": orphan["id"],
        "closeout_turn_id": closeout_id,
    }


def test_late_cursor_sdk_dispatch_demotes_orphan(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="dispatch-closeout")
    orphan_id = _orphan_turns(thread_id)[0]["id"]

    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted"
    ) as mock_emit:
        closeout_id, _, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="cursor-sdk dispatch disp-1 COMPLETED",
            body="result",
        )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == closeout_id
    mock_emit.assert_called_once_with(
        thread_id=thread_id,
        orphan_turn_id=orphan_id,
        closeout_turn_id=closeout_id,
    )


def test_nonterminal_cursor_sdk_does_not_demote(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="nonterminal")

    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted"
    ) as mock_emit:
        insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject="cursor-sdk progress update",
            body="still running",
        )

    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"
    mock_emit.assert_not_called()


def test_true_orphan_without_late_terminal_stays_open(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="true-orphan")
    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"


def test_second_terminal_idempotent(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="idem-demote")
    orphan_id = _orphan_turns(thread_id)[0]["id"]

    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted"
    ) as mock_emit:
        first_id, _, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject=_producer_closeout_subject(dispatch_id="disp-first"),
            body="done",
        )
        second_id, _, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject=_producer_closeout_subject(dispatch_id="disp-second"),
            body="also done",
        )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == first_id
    mock_emit.assert_called_once_with(
        thread_id=thread_id,
        orphan_turn_id=orphan_id,
        closeout_turn_id=first_id,
    )
    assert second_id != first_id


def test_non_cursor_sdk_closeout_prose_does_not_demote(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="prose-closeout")

    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted"
    ) as mock_emit:
        insert_turn(
            thread=thread_id,
            from_agent="claude-web",
            to_agent="dispatch",
            subject="closeout summary for operator",
            body="manual closeout prose",
        )

    orphan = _orphan_turns(thread_id)[0]
    assert orphan["status"] == "open"
    mock_emit.assert_not_called()


def test_producer_closeout_subject_demotes_orphan(bus_db) -> None:
    """Behavioural falsifier: build_sdk_closeout_subject output demotes open orphans."""
    thread_id = _seed_orphan_thread(bus_db, slug="producer-falsifier")
    orphan_id = _orphan_turns(thread_id)[0]["id"]
    subject = _producer_closeout_subject(
        dispatch_id="893994c82a61-75be8122",
        contract="conductor",
        caller_agent="conductor-hop",
    )

    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted"
    ) as mock_emit:
        closeout_id, _, _, _ = insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject=subject,
            body="done",
        )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == closeout_id
    mock_emit.assert_called_once_with(
        thread_id=thread_id,
        orphan_turn_id=orphan_id,
        closeout_turn_id=closeout_id,
    )


def test_demotion_via_insert_turn_shared_choke(bus_db) -> None:
    """insert_turn is the shared choke for POST /turns and send paths."""
    thread_id = _seed_orphan_thread(bus_db, slug="choke")

    closeout_id, _, _, _ = insert_turn(
        thread=thread_id,
        from_agent="cursor-sdk",
        to_agent="dispatch",
        subject="cursor-sdk dispatch disp-choke COMPLETED",
        body="via insert_turn",
    )

    demoted = _orphan_turns(thread_id)[0]
    assert demoted["status"] == "superseded"
    assert demoted["supersedes_turn"] == closeout_id


def test_execution_id_mismatch_skips_demotion(bus_db) -> None:
    thread_id = _seed_orphan_thread(bus_db, slug="exec-mismatch", execution_id="exec-a")
    orphan = _orphan_turns(thread_id)[0]
    assert "exec-a" in orphan["body"]

    with connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links SET execution_id = ? WHERE thread_id = ?",
            ("exec-b", thread_id),
        )

    with patch(
        "agent_bus_store.orphan_demote.emit_dispatch_orphan_demoted"
    ) as mock_emit:
        insert_turn(
            thread=thread_id,
            from_agent="cursor-sdk",
            to_agent="dispatch",
            subject=_producer_closeout_subject(dispatch_id="disp-late"),
            body="done",
        )

    assert _orphan_turns(thread_id)[0]["status"] == "open"
    mock_emit.assert_not_called()
