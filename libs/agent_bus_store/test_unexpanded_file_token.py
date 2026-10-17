"""Refuse a turn body or sidecar that is only an unexpanded ``$file:`` token.

Breaks when send, with-turn, POST /turns, PATCH body or append, or a
dispatch claim/admit post stores the literal, including a whitespace-padded
token. A mid-sentence mention is ordinary prose and must be stored.
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
_PADDED = f"  {_TOKEN}\n"
_CODE = "body_unexpanded_file_token"
_MESSAGE = (
    "The value is an unexpanded $file: token. "
    "The gateway stores the literal string and does not expand it."
)
_SOURCE = "agent_bus_store.send"


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
    assert detail["reason"] == _CODE
    assert detail["message"] == _MESSAGE
    assert detail["fix_hint"] == _FIX_HINT
    assert detail["retryable"] is True
    assert detail["source"] == _SOURCE


def _seed_turn(client, slug: str, body: str = "hello") -> tuple[str, int]:
    resp = client.post(
        "/threads/with-turn",
        json={
            "slug": slug,
            "from": "cursor",
            "to": "web",
            "subject": "seed",
            "body": body,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["thread"]["id"], int(data["turn"]["id"])


def _pending_shell(client, slug: str) -> str:
    resp = client.post(
        "/threads",
        json={
            "slug": slug,
            "lifecycle_state": "pending",
            "tags": ["cursor-sdk-generate"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["turn_count"] == 0
    assert body["bus_lifecycle_state"] == "pending"
    return body["id"]


def _turns(client, thread_id: str) -> list[dict]:
    resp = client.get("/turns", params={"thread": thread_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["turns"]


def _thread(client, thread_id: str) -> dict:
    resp = client.get(f"/threads/{thread_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _dispatch_body(execution_id: str, body: str) -> dict:
    return {
        "execution_id": execution_id,
        "pipeline_id": "cursor-sdk-generate",
        "caller_agent": "dispatch",
        "from_agent": "dispatch",
        "to_agent": "cursor-sdk",
        "subject": "pointer",
        "body": body,
    }


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


def test_post_turns_refuses_whole_value_file_token(tmp_path, monkeypatch) -> None:
    """POST /turns stores nothing when the body is a padded ``$file:`` token."""
    app, _cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        thread_id, _turn_id = _seed_turn(client, "post-turns-token")
        before = _turns(client, thread_id)
        refused = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "web",
                "to": "cursor",
                "subject": "reply",
                "body": _PADDED,
            },
        )
        _assert_refused(refused)
        after = _turns(client, thread_id)
        assert len(after) == len(before) == 1
        assert after[0]["body"] == "hello"
        assert after[0]["id"] == before[0]["id"]


def test_patch_turns_refuses_file_token_on_body_and_append(
    tmp_path, monkeypatch
) -> None:
    """PATCH body and append leave the row unchanged when each is a token."""
    app, _cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        thread_id, turn_id = _seed_turn(client, "patch-turns-token", body="kept")
        replaced = client.patch(f"/turns/{turn_id}", json={"body": _PADDED})
        _assert_refused(replaced)
        appended = client.patch(f"/turns/{turn_id}", json={"append": _PADDED})
        _assert_refused(appended)
        stored = _turns(client, thread_id)
        assert len(stored) == 1
        assert stored[0]["id"] == turn_id
        assert stored[0]["body"] == "kept"


def test_dispatch_claim_and_post_refuses_whole_value_file_token(
    tmp_path, monkeypatch
) -> None:
    """Claim-and-post does not admit or insert when the pointer body is a token."""
    app, _cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        thread_id = _pending_shell(client, "claim-post-token")
        execution_id = "exec-claim-token"
        refused = client.post(
            f"/threads/{thread_id}/dispatch-claim-and-post",
            json=_dispatch_body(execution_id, _PADDED),
        )
        _assert_refused(refused)
        detail = _thread(client, thread_id)
        assert detail["turn_count"] == 0
        assert detail["bus_lifecycle_state"] == "pending"
        assert _turns(client, thread_id) == []
        link = client.get(f"/dispatch-links/{execution_id}")
        assert link.status_code == 404, link.text


def test_dispatch_admit_and_post_refuses_whole_value_file_token(
    tmp_path, monkeypatch
) -> None:
    """Admit-and-post does not store a caller body that is only a ``$file:`` token."""
    app, _cortex_root = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        thread_id, turn_id = _seed_turn(client, "admit-post-token", body="prior")
        execution_id = "exec-admit-token"
        before = _thread(client, thread_id)
        refused = client.post(
            f"/threads/{thread_id}/dispatch-admit-and-post",
            json=_dispatch_body(execution_id, _PADDED),
        )
        _assert_refused(refused)
        after = _thread(client, thread_id)
        assert after["turn_count"] == before["turn_count"] == 1
        stored = _turns(client, thread_id)
        assert len(stored) == 1
        assert stored[0]["id"] == turn_id
        assert stored[0]["body"] == "prior"
        link = client.get(f"/dispatch-links/{execution_id}")
        assert link.status_code == 404, link.text


def test_ordinary_text_still_passes_guarded_write_routes(tmp_path, monkeypatch) -> None:
    """Mid-text ``$file:`` mentions and plain bodies still store on each route."""
    app, _cortex_root = _app(tmp_path, monkeypatch)
    prose = f"The sender left {_TOKEN} in the turn."
    with TestClient(app) as client:
        thread_id, turn_id = _seed_turn(client, "ordinary-writes", body="seed")
        posted = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "web",
                "to": "cursor",
                "subject": "reply",
                "body": prose,
            },
        )
        assert posted.status_code == 201, posted.text
        patched = client.patch(f"/turns/{turn_id}", json={"body": "revised ordinary"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["body"] == "revised ordinary"

        claim_id = _pending_shell(client, "ordinary-claim")
        claimed = client.post(
            f"/threads/{claim_id}/dispatch-claim-and-post",
            json=_dispatch_body("exec-ordinary-claim", prose),
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["turn_count"] == 1
        assert claimed.json()["bus_lifecycle_state"] == "active"
        assert _turns(client, claim_id)[0]["body"] == prose

        admitted = client.post(
            f"/threads/{thread_id}/dispatch-admit-and-post",
            json=_dispatch_body("exec-ordinary-admit", "plain pointer"),
        )
        assert admitted.status_code == 200, admitted.text
        bodies = [row["body"] for row in _turns(client, thread_id)]
        assert "revised ordinary" in bodies
        assert prose in bodies
        assert "plain pointer" in bodies
