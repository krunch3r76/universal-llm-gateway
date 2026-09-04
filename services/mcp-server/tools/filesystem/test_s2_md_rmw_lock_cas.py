"""S2 probes: md_* RMW silent-loss arms A–D from G1 bind §9."""

from __future__ import annotations

from multiprocessing import Process, Queue
from pathlib import Path

import pytest
from markdown_sections import append_section as md_append_section

from tools import markdown_tool
from tools.file_editor import perform_edit
from tools.filesystem import _ops_text as ops_text
from tools.filesystem import _ops_write as ops_write
from tools.filesystem import _paths as paths

N_PROCS = 8
N_ROUNDS = 3
EXPECTED = N_PROCS * N_ROUNDS
_LOUD_REASONS = frozenset({"file_sha256.mismatch", "write_verify_failed"})
_SEED = "# Note\n\n## Log\n\nseed\n"


@pytest.fixture(autouse=True)
def _stub_records(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = lambda *_a, **_k: None  # noqa: E731
    monkeypatch.setattr(markdown_tool, "record", stub)
    monkeypatch.setattr(ops_text, "record", stub)
    monkeypatch.setattr(ops_write, "record", stub)


@pytest.fixture
def sandbox_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir()
    monkeypatch.setattr(paths, "SANDBOX_ROOT", root)
    monkeypatch.setattr(markdown_tool, "_FILES_ROOT", root)
    return root


def _silent_loss(outcomes: list, body: str) -> list[str]:
    return [
        marker
        for status, marker, *_rest in outcomes
        if status == "ok" and marker not in body
    ]


def _md_append(resolved: Path, marker: str) -> dict:
    return markdown_tool._section_write_result(
        resolved,
        str(resolved),
        "cortex",
        "Log",
        "mcp.tool.markdown.section.appended",
        "appended",
        lambda t: md_append_section(t, "Log", marker),
    )


def _worker_md(path_s: str, wid: int, q: Queue) -> None:
    path = Path(path_s)
    for round_i in range(N_ROUNDS):
        marker = f"MD{wid}R{round_i}\n"
        res = _md_append(path, marker)
        q.put(
            (
                "ok" if res.get("status") == "appended" else "err",
                marker,
                res.get("error"),
            )
        )


def _worker_fs(path_s: str, wid: int, q: Queue) -> None:
    path = Path(path_s)
    for round_i in range(N_ROUNDS):
        marker = f"FS{wid}R{round_i}\n"
        try:
            res = perform_edit(path, "append", marker)
            status = str(res.get("status", ""))
            q.put(("ok" if status.startswith("edited") else "err", marker, None))
        except Exception as exc:  # noqa: BLE001 — harness outcome
            q.put(("err", marker, type(exc).__name__))


def _collect_md(target, path: Path, n_procs: int = N_PROCS) -> tuple[list, str]:
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


def test_arm_a_md_append_x_md_append_no_silent_loss(
    sandbox_root: Path,
) -> None:
    """AC1: 8 md writers × 3 rounds — SILENT_LOSS=0 observed=24."""
    dest = sandbox_root / "notes/system/threads/s2-arm-a.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_SEED, encoding="utf-8")
    outcomes, body = _collect_md(_worker_md, dest)
    silent = _silent_loss(outcomes, body)
    print(
        f"ARM_A expected={EXPECTED} observed={body.count(chr(10))} "
        f"SILENT_LOSS={len(silent)}"
    )
    assert silent == [], silent
    assert len(outcomes) == EXPECTED
    for status, marker, *_rest in outcomes:
        assert status == "ok"
        assert marker in body


def test_arm_b_md_append_x_fs_append_no_silent_loss(
    sandbox_root: Path,
) -> None:
    """AC2: 4 md + 4 fs append × 3 rounds — SILENT_LOSS=0."""
    dest = sandbox_root / "notes/system/threads/s2-arm-b.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_SEED, encoding="utf-8")
    n_each = 4
    q: Queue = Queue()
    procs = [Process(target=_worker_md, args=(str(dest), i, q)) for i in range(n_each)]
    procs.extend(
        Process(target=_worker_fs, args=(str(dest), i, q)) for i in range(n_each)
    )
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert proc.exitcode == 0, proc.exitcode
    outcomes = [q.get() for _ in range(n_each * N_ROUNDS * 2)]
    body = dest.read_text(encoding="utf-8")
    silent = _silent_loss(outcomes, body)
    print(
        f"ARM_B expected={n_each * N_ROUNDS * 2} observed={body.count(chr(10))} "
        f"SILENT_LOSS={len(silent)}"
    )
    assert silent == [], silent


def test_arm_c_write_file_impl_caller_cas_under_flock(
    sandbox_root: Path,
) -> None:
    """AC3: stale caller CAS is loud; peer bytes preserved."""
    from durable_io.atomic import durable_write_bytes

    rel = "notes/system/threads/s2-arm-c.md"
    dest = sandbox_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("base\n", encoding="utf-8")
    base_sha = paths.sha256_hex_of_file(dest)
    real_retain = ops_write.retain_before_overwrite

    def _retain_then_peer(d: Path) -> str | None:
        out = real_retain(d)
        durable_write_bytes(d, b"base\nPEER-LINE\n", already_locked=True)
        return out

    ops_write.retain_before_overwrite = _retain_then_peer
    try:
        res = ops_write.write_file_impl(
            rel,
            "base\nWRITER-LINE\n",
            expected_sha256=base_sha,
        )
    finally:
        ops_write.retain_before_overwrite = real_retain
    print(f"ARM_C status={res.get('status')} reason={res.get('reason')}")
    body = dest.read_text(encoding="utf-8")
    assert res.get("reason") == "file_sha256.mismatch"
    assert "status" not in res
    assert "PEER-LINE" in body


def test_arm_d_edit_file_impl_caller_cas_honoured(
    sandbox_root: Path,
) -> None:
    """AC4: caller CAS mismatch is loud; body unchanged."""
    from tools._durable_write import durable_write_text

    rel = "notes/system/threads/s2-arm-d.md"
    dest = sandbox_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("base\n", encoding="utf-8")
    base_sha = paths.sha256_hex_of_file(dest)
    real_perform = ops_text.perform_edit

    def _peer_then_edit(*args: object, **kwargs: object) -> dict:
        durable_write_text(dest, "base\nPEER-LINE\n")
        return real_perform(*args, **kwargs)

    ops_text.perform_edit = _peer_then_edit
    try:
        res = ops_text.edit_file_impl(
            rel,
            "append",
            "EDIT-LINE\n",
            expected_sha256=base_sha,
        )
    finally:
        ops_text.perform_edit = real_perform
    print(f"ARM_D status={res.get('status')} reason={res.get('reason')}")
    body = dest.read_text(encoding="utf-8")
    assert res.get("reason") == "file_sha256.mismatch"
    assert body == "base\nPEER-LINE\n"
