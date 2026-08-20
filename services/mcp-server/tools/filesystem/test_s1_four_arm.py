"""S1 four-arm harness: spec 24→5 and mixed SILENT_LOSS arms, before and after."""

from __future__ import annotations

import os
import time
from multiprocessing import Process, Queue
from pathlib import Path

from durable_io.atomic import durable_write_text, path_flock

from tools.file_editor import perform_edit

N_PROCS = 8
N_ROUNDS = 3
EXPECTED = N_PROCS * N_ROUNDS


def _legacy_rmw_append(path: Path, marker: str) -> None:
    data = path.read_text(encoding="utf-8") if path.is_file() else ""
    time.sleep(0.002)
    path.write_text(data + marker, encoding="utf-8")


def _worker_legacy(path_s: str, wid: int, q: Queue) -> None:
    path = Path(path_s)
    for round_i in range(N_ROUNDS):
        marker = f"- fanin w{wid} r{round_i}\n"
        try:
            _legacy_rmw_append(path, marker)
            q.put(("ok", marker))
        except Exception as exc:  # noqa: BLE001 — harness outcome
            q.put(("err", marker, type(exc).__name__))


def _worker_perform_edit(path_s: str, wid: int, q: Queue) -> None:
    path = Path(path_s)
    for round_i in range(N_ROUNDS):
        marker = f"W{wid}R{round_i}\n"
        try:
            result = perform_edit(path, "append", marker)
            status = str(result.get("status", ""))
            q.put(("ok" if status.startswith("edited") else "err", marker))
        except Exception:  # noqa: BLE001 — harness outcome
            q.put(("err", marker))


def _worker_o_append(path_s: str, wid: int, q: Queue) -> None:
    path = Path(path_s)
    for round_i in range(N_ROUNDS):
        marker = f"OA{wid}R{round_i}\n"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, marker.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        q.put(("ok", marker))


def _worker_durable_replace_rmw(path_s: str, wid: int, q: Queue) -> None:
    """Former Path.write_text whole-file RMW, now the serialised leaf."""
    path = Path(path_s)
    for round_i in range(N_ROUNDS):
        marker = f"DW{wid}R{round_i}\n"
        try:
            with path_flock(path):
                data = path.read_text(encoding="utf-8") if path.is_file() else ""
                durable_write_text(path, data + marker, already_locked=True)
            q.put(("ok", marker))
        except Exception:  # noqa: BLE001 — harness outcome
            q.put(("err", marker))


def _collect(target, path: Path, n_procs: int = N_PROCS) -> tuple[list, str]:
    q: Queue = Queue()
    procs = [Process(target=target, args=(str(path), i, q)) for i in range(n_procs)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert proc.exitcode == 0, proc.exitcode
    outcomes = [q.get() for _ in range(n_procs * N_ROUNDS)]
    body = path.read_text(encoding="utf-8") if path.is_file() else ""
    return outcomes, body


def _silent_loss(outcomes: list, body: str) -> list[str]:
    return [
        marker
        for status, marker, *rest in outcomes
        if status == "ok" and marker not in body
    ]


def test_four_arm_harness_before_and_after(tmp_path: Path) -> None:
    before = tmp_path / "before.md"
    before.write_text("", encoding="utf-8")
    before_out, before_body = _collect(_worker_legacy, before)
    before_silent = _silent_loss(before_out, before_body)
    print(
        f"ARM_BEFORE_legacy_write_text expected={EXPECTED} "
        f"observed={before_body.count(chr(10))} SILENT_LOSS={len(before_silent)}"
    )
    assert len(before_silent) > 0, "legacy RMW did not reproduce silent loss"

    after = tmp_path / "after-mcp.md"
    after.write_text("", encoding="utf-8")
    after_out, after_body = _collect(_worker_perform_edit, after)
    after_silent = _silent_loss(after_out, after_body)
    print(
        f"ARM_mcp_perform_edit_append expected={EXPECTED} "
        f"observed={after_body.count(chr(10))} SILENT_LOSS={len(after_silent)}"
    )
    assert after_silent == []
    assert after_body.count("\n") == EXPECTED

    oa = tmp_path / "o-append.md"
    oa.write_text("", encoding="utf-8")
    oa_out, oa_body = _collect(_worker_o_append, oa)
    oa_silent = _silent_loss(oa_out, oa_body)
    print(
        f"ARM_o_append expected={EXPECTED} observed={oa_body.count(chr(10))} "
        f"SILENT_LOSS={len(oa_silent)}"
    )
    assert oa_silent == []
    assert oa_body.count("\n") == EXPECTED

    mixed = tmp_path / "mixed.md"
    mixed.write_text("seed\n", encoding="utf-8")
    q: Queue = Queue()
    procs = [
        Process(target=_worker_o_append, args=(str(mixed), i, q)) for i in range(4)
    ]
    procs.extend(
        Process(target=_worker_perform_edit, args=(str(mixed), i, q)) for i in range(4)
    )
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert proc.exitcode == 0, proc.exitcode
    mixed_out = [q.get() for _ in range(4 * N_ROUNDS * 2)]
    mixed_body = mixed.read_text(encoding="utf-8")
    mixed_silent = _silent_loss(mixed_out, mixed_body)
    print(
        f"ARM_mixed_o_append_x_rmw expected=24 "
        f"observed={mixed_body.count(chr(10))} SILENT_LOSS={len(mixed_silent)}"
    )
    assert mixed_silent == []

    former = tmp_path / "former-write-text.md"
    former.write_text("", encoding="utf-8")
    dw_out, dw_body = _collect(_worker_durable_replace_rmw, former)
    dw_silent = _silent_loss(dw_out, dw_body)
    print(
        f"ARM_former_write_text_now_leaf expected={EXPECTED} "
        f"observed={dw_body.count(chr(10))} SILENT_LOSS={len(dw_silent)}"
    )
    assert dw_silent == []
    assert dw_body.count("\n") == EXPECTED

    md_tool = (Path(__file__).resolve().parents[1] / "markdown_tool.py").read_text(
        encoding="utf-8"
    )
    assert ".write_text(" not in md_tool
