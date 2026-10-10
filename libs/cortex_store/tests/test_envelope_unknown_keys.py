"""Unknown envelope keys, including handoff, survive pour, model, and seal write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    seal_messages_sha256,
)
from continuity_tape.pour_contract import validate_pour

from cortex_store.verbatim_succession import build_seal_envelope_meta

pytestmark = pytest.mark.offline

_MESSAGES = [
    {
        "role": "user",
        "content": "hi " + ("word " * 80),
        "turn_index": 1,
    }
]
_HANDOFF = {"mission": "resume", "author": "grok-bot"}
_EXTRA = {"note": "kept"}


def _envelope() -> dict:
    return {
        "schema": "ulg.continuity.messages/1",
        "messages": _MESSAGES,
        "meta": {
            "provenance": "transcribed",
            "coverage": "full",
            "surface": "grok",
            "transcript_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "messages_sha256": seal_messages_sha256(_MESSAGES),
            "sources": [
                {
                    "kind": "dom",
                    "producer": {"id": "grok-bot", "kind": "bot", "version": "1"},
                }
            ],
        },
        "handoff": _HANDOFF,
        "extra_unknown": _EXTRA,
    }


def test_validate_pour_and_model_keep_unknown_keys() -> None:
    raw = _envelope()
    parsed = validate_pour(raw)
    dumped = parsed.model_dump(mode="json", by_alias=True)
    assert dumped["handoff"] == _HANDOFF
    assert dumped["extra_unknown"] == _EXTRA
    again = ContinuityMessagesEnvelope.model_validate(raw).model_dump(
        mode="json", by_alias=True
    )
    assert again["handoff"] == _HANDOFF
    assert again["extra_unknown"] == _EXTRA
    sealed = build_seal_envelope_meta(
        parsed,
        session_id="grok-bot-unknown-keys",
        sealed_at="2026-10-09T18:00:00Z",
    )
    sealed_dump = sealed.model_dump(mode="json", by_alias=True)
    assert sealed_dump["handoff"] == _HANDOFF
    assert sealed_dump["extra_unknown"] == _EXTRA


def _boot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import cortex_store.db as cortex_db
    import cortex_store.dispatch_ops._shared as shared
    import cortex_store.dispatch_ops.ops_session_close as ops
    import cortex_store.routes.session_close_persist as persist
    import cortex_store.routes.session_journals as journals
    from cortex_store._test_db_bootstrap import materialize_head_schema_template

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    for module in (shared, ops, persist, journals):
        monkeypatch.setattr(module, "_FILES_ROOT", tmp_path)
    db_path = tmp_path / "cortex.db"
    materialize_head_schema_template(db_path)
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)
    cortex_db.run_migrations(cortex_db.cortex_conn())


def test_session_close_persist_writes_unknown_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Drive ``_op_session_close``, which persist uses to write the seal file."""
    import cortex_store.dispatch_ops.ops_entities  # noqa: F401 — breaks a routes cycle
    from cortex_store.dispatch_ops.ops_session_close import _op_session_close

    _boot(monkeypatch, tmp_path)
    env = _envelope()
    env["meta"]["opened_at"] = "2026-04-05T06:07:08Z"
    result = _op_session_close(
        session_id="grok-bot-2026-04-05-060708-a1b",
        agent="grok-bot-hm1",
        session_summary_md=(
            "## Session Summary\n\n"
            + "Unknown envelope keys stay on the sealed messages file. " * 4
        ),
        summary="unknown keys stay on the sealed envelope",
        transcript_messages=env,
        transcript_depth="verbatim",
        entity_ids=["agent-bus:1"],
        closed_by="succession",
        succession_seal_authority=True,
    )
    assert isinstance(result, dict)
    assert result.get("error") is None
    session_id = str(result["session_id"])
    seal_path = tmp_path / "notes" / "system" / "seals" / f"{session_id}.messages.json"
    stored = json.loads(seal_path.read_text(encoding="utf-8"))
    assert stored["handoff"] == _HANDOFF
    assert stored["extra_unknown"] == _EXTRA
