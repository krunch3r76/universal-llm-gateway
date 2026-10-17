"""Refuse a turn body or sidecar that is only an unexpanded ``$file:`` token.

Breaks when send or with-turn stores the literal (new thread, continue, or
sidecar write) including a whitespace-padded token. A mid-sentence mention
is ordinary prose and must be stored.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token

pytestmark = pytest.mark.offline

_FIX_HINT = (
    "the $file: token was not expanded by the caller; "
    "send the body inline or via sidecar_content"
)
_TOKEN = "$file:/workspace/rev4/done39520.txt"
_CODE = "body_unexpanded_file_token"


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


def _assert_refused(resp) -> None:
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == _CODE
    assert detail["fix_hint"] == _FIX_HINT


def test_send_refuses_whole_value_file_token(tmp_path, monkeypatch) -> None:
    """Whole-value ``$file:`` is 422; mid-sentence text is stored."""
    app, cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        exact = client.post(
            "/threads/send",
            json={
                "new_slug": "file-token-exact",
                "from": "cursor",
                "to": "web",
                "subject": "done",
                "body": _TOKEN,
            },
        )
        _assert_refused(exact)
        threads = client.get("/threads").json()["threads"]
        assert all(row["slug"] != "file-token-exact" for row in threads)

        padded = client.post(
            "/threads/send",
            json={
                "new_slug": "file-token-pad",
                "from": "cursor",
                "to": "web",
                "subject": "done",
                "body": f"  {_TOKEN}\n",
            },
        )
        _assert_refused(padded)

        mixed = client.post(
            "/threads/send",
            json={
                "new_slug": "file-token-mixed",
                "from": "cursor",
                "to": "web",
                "subject": "done",
                "body": f"The sender left {_TOKEN} in the turn.",
            },
        )
        assert mixed.status_code == 201, mixed.text
        thread_id = mixed.json()["thread"]["id"]
        stored = client.get(f"/turns?thread={thread_id}").json()["turns"]
        assert stored[0]["body"] == f"The sender left {_TOKEN} in the turn."

        sidecar = client.post(
            "/threads/send",
            json={
                "new_slug": "file-token-sidecar",
                "from": "cursor",
                "to": "web",
                "subject": "done",
                "body": "brief",
                "sidecar_content": f"\n{_TOKEN}  ",
            },
        )
        _assert_refused(sidecar)

        with_turn = client.post(
            "/threads/with-turn",
            json={
                "slug": "file-token-with-turn",
                "from": "cursor",
                "to": "web",
                "subject": "done",
                "body": _TOKEN,
            },
        )
        _assert_refused(with_turn)

        seed = client.post(
            "/threads/with-turn",
            json={
                "slug": "file-token-continue",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert seed.status_code == 201, seed.text
        continue_id = seed.json()["thread"]["id"]
        continued = client.post(
            "/threads/send",
            json={
                "thread": continue_id,
                "from": "cursor",
                "to": "web",
                "subject": "done",
                "body": _TOKEN,
            },
        )
        _assert_refused(continued)
        after = client.get(f"/turns?thread={continue_id}").json()["turns"]
        assert len(after) == 1
        assert after[0]["body"] == "hello"

    sidecar_dir = cortex_root / "notes/system/threads"
    assert not sidecar_dir.exists() or list(sidecar_dir.glob("*.md")) == []
