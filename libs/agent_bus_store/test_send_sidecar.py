"""E4 tests: send sidecar-on-send + F4a failure injection matrix."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from cortex_store.dispatch_ops._thread_sidecar import SidecarWriteError
from cortex_store.dispatch_ops.ops_misc import _op_thread_sidecar_write
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.turns_models import (
    MAX_SIDECAR_CONTENT_CHARS,
    sidecar_content_is_unexpanded_shell,
)


def _app(tmp_path, monkeypatch):
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app, cortex_root


def _count_sidecar_pointers(client: TestClient, thread_id: str) -> int:
    turns = client.get(f"/turns?thread={thread_id}").json()["turns"]
    return sum(1 for t in turns if "Sidecar: cortex://" in (t.get("body") or ""))


def test_send_new_thread_with_sidecar_writes_file_and_pointer(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    content = "# Review\n\nFindings here."
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "sidecar-e4",
                "from": "cursor",
                "to": "web",
                "subject": "Review complete",
                "body": "Full findings in sidecar.",
                "sidecar_content": content,
                "sidecar_slug": "review",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        thread_id = data["thread"]["id"]
        sidecar_uri = data["sidecar_uri"]
        sidecar_sha256 = data["sidecar_sha256"]
        assert sidecar_uri == f"cortex://notes/system/threads/{thread_id}-review.md"
        assert sidecar_sha256 == hashlib.sha256(content.encode()).hexdigest()
        # TurnCreated must mirror top-level (a:26439 item 5 — turn.sidecar_uri
        # was null even when the sidecar wrote successfully).
        assert data["turn"]["sidecar_uri"] == sidecar_uri
        assert data["turn"]["sidecar_sha256"] == sidecar_sha256

        turn = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=1"
        ).json()
        assert turn["body"].endswith(f"Sidecar: {sidecar_uri}")
        assert _count_sidecar_pointers(client, thread_id) == 1

        rel = sidecar_uri.removeprefix("cortex://")
        file_path = cortex_root / rel
        assert file_path.is_file()
        assert sidecar_sha256 in file_path.read_text(encoding="utf-8")


def test_send_continue_thread_with_sidecar(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "continue-sidecar",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        thread_id = create.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Follow-up",
                "body": "Details attached.",
                "sidecar_content": "## Details\n\nMore text.",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["send_path"] == "continue"
        assert data["sidecar_uri"].startswith("cortex://notes/system/threads/")
        assert data["sidecar_sha256"]
        assert data["turn"]["sidecar_uri"] == data["sidecar_uri"]
        assert data["turn"]["sidecar_sha256"] == data["sidecar_sha256"]


def test_send_sidecar_content_cap_413(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    oversized = "x" * (MAX_SIDECAR_CONTENT_CHARS + 1)
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "too-big",
                "from": "cursor",
                "to": "web",
                "subject": "Big",
                "body": "brief",
                "sidecar_content": oversized,
            },
        )
        assert resp.status_code == 413
        detail = resp.json()["detail"]
        assert detail["code"] == "sidecar_content_too_large"


def test_send_sidecar_write_failure_no_turn_row(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with (
        TestClient(app) as client,
        patch(
            "cortex_store.dispatch_ops._thread_sidecar.write_thread_sidecar_for_send",
            side_effect=SidecarWriteError("disk full"),
        ),
    ):
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "write-fail",
                "from": "cursor",
                "to": "web",
                "subject": "Fail",
                "body": "brief",
                "sidecar_content": "payload",
            },
        )
        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "sidecar_write_failed"
        thread_id = detail["data"]["thread_id"]
        assert thread_id
        assert client.get(f"/turns?thread={thread_id}").json()["turns"] == []
        assert _count_sidecar_pointers(client, thread_id) == 0


def test_send_turn_insert_failure_orphan_file_no_dangling_pointer(
    tmp_path, monkeypatch
) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    with (
        TestClient(app) as client,
        patch(
            "agent_bus_store.db.turns.insert_turn",
            side_effect=RuntimeError("db locked"),
        ),
    ):
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "insert-fail",
                "from": "cursor",
                "to": "web",
                "subject": "Orphan test",
                "body": "brief",
                "sidecar_content": "orphan payload",
            },
        )
        assert resp.status_code == 500
        threads = client.get("/threads").json()["threads"]
        thread_id = next(t["id"] for t in threads if t["slug"] == "insert-fail")
        assert _count_sidecar_pointers(client, thread_id) == 0
        sidecar_files = list(
            (cortex_root / "notes/system/threads").glob(f"{thread_id}-*.md")
        )
        assert len(sidecar_files) == 1


@pytest.mark.parametrize(
    "failure_phase",
    ["write_fail", "insert_fail"],
)
def test_f4a_dangling_sidecar_pointers_zero(
    tmp_path, monkeypatch, failure_phase: str
) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    patches: list[object] = []
    if failure_phase == "write_fail":
        patches.append(
            patch(
                "cortex_store.dispatch_ops._thread_sidecar.write_thread_sidecar_for_send",
                side_effect=SidecarWriteError("injected"),
            )
        )
    else:
        patches.append(
            patch(
                "agent_bus_store.db.turns.insert_turn",
                side_effect=RuntimeError("injected"),
            )
        )

    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with TestClient(app) as client:
            resp = client.post(
                "/threads/send",
                json={
                    "new_slug": f"f4a-{failure_phase}",
                    "from": "cursor",
                    "to": "web",
                    "subject": "matrix",
                    "body": "brief",
                    "sidecar_content": "matrix payload",
                },
            )
            assert resp.status_code in (500, 503)
            threads = client.get("/threads").json()["threads"]
            dangling = 0
            for thread in threads:
                dangling += _count_sidecar_pointers(client, thread["id"])
            assert dangling == 0


@pytest.mark.parametrize(
    "content",
    [
        "$(cat /tmp/x.md)",
        "  $(echo hi)  ",
        "`cat /tmp/x`",
        "  `echo hi`  ",
    ],
)
def test_sidecar_content_is_unexpanded_shell_matches_exact_forms(content: str) -> None:
    assert sidecar_content_is_unexpanded_shell(content) is True


@pytest.mark.parametrize(
    "content",
    [
        "# Findings\n\nSee $(cat /tmp/x) for detail.",
        "see `cat /tmp/x` then continue",
        "$(unbalanced",
        "$(foo) trailing",
        "`only one",
        "",
        "plain markdown",
    ],
)
def test_sidecar_content_is_unexpanded_shell_accepts_mixed(content: str) -> None:
    assert sidecar_content_is_unexpanded_shell(content) is False


def _assert_shell_refused(resp, *, fix_hint_fragment: str = "fs(sandbox=cortex") -> None:
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "sidecar_unexpanded_shell"
    assert fix_hint_fragment in detail["fix_hint"]


def test_send_refuses_dollar_paren_sidecar_no_thread_or_file(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "shell-sub",
                "from": "cursor",
                "to": "web",
                "subject": "Findings",
                "body": "brief",
                "sidecar_content": "$(cat /tmp/x.md)",
            },
        )
        _assert_shell_refused(resp)
        threads = client.get("/threads").json()["threads"]
        assert all(t["slug"] != "shell-sub" for t in threads)
    sidecar_dir = cortex_root / "notes/system/threads"
    assert not sidecar_dir.exists() or list(sidecar_dir.glob("*.md")) == []


def test_send_refuses_padded_dollar_paren_sidecar(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "shell-pad",
                "from": "cursor",
                "to": "web",
                "subject": "Findings",
                "body": "brief",
                "sidecar_content": "  $(echo hi)  ",
            },
        )
        _assert_shell_refused(resp)
    sidecar_dir = cortex_root / "notes/system/threads"
    assert not sidecar_dir.exists() or list(sidecar_dir.glob("*.md")) == []


def test_send_refuses_backtick_sidecar(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "shell-bt",
                "from": "cursor",
                "to": "web",
                "subject": "Findings",
                "body": "brief",
                "sidecar_content": "`cat /tmp/x`",
            },
        )
        _assert_shell_refused(resp)
    sidecar_dir = cortex_root / "notes/system/threads"
    assert not sidecar_dir.exists() or list(sidecar_dir.glob("*.md")) == []


def test_send_continue_refuses_shell_sidecar_no_new_turn(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "continue-shell",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        thread_id = create.json()["thread"]["id"]
        before = client.get(f"/turns?thread={thread_id}").json()["turns"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Follow-up",
                "body": "brief",
                "sidecar_content": "$(cat /tmp/x.md)",
            },
        )
        _assert_shell_refused(resp)
        after = client.get(f"/turns?thread={thread_id}").json()["turns"]
        assert len(after) == len(before)
    sidecar_dir = cortex_root / "notes/system/threads"
    assert not sidecar_dir.exists() or list(sidecar_dir.glob("*.md")) == []


def test_send_accepts_mixed_body_containing_shell_text(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    content = "# Findings\n\nSee $(cat /tmp/x) for detail."
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "mixed-shell",
                "from": "cursor",
                "to": "web",
                "subject": "Findings",
                "body": "brief",
                "sidecar_content": content,
                "sidecar_slug": "findings",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        rel = data["sidecar_uri"].removeprefix("cortex://")
        file_text = (cortex_root / rel).read_text(encoding="utf-8")
        assert content in file_text
        assert data["sidecar_sha256"] == hashlib.sha256(content.encode()).hexdigest()


def test_cortex_thread_sidecar_write_refuses_unexpanded_shell(tmp_path, monkeypatch) -> None:
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    result = _op_thread_sidecar_write(
        thread="t-shell",
        subject="Findings",
        content="$(cat /tmp/x.md)",
        from_agent="cursor",
    )
    assert result["status_code"] == 422
    assert result["code"] == "sidecar_unexpanded_shell"
    assert "fix_hint" in result
    assert list(cortex_root.rglob("*.md")) == []
