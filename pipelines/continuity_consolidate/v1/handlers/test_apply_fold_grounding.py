"""Apply-handler wiring for friction 37643 — hub copies must not cite the trigger."""

from __future__ import annotations

import pytest

from pipelines.continuity_consolidate.v1.handlers.apply import (
    ContinuityConsolidateApplyHandler,
)
from pipelines.continuity_consolidate.v1.handlers.test_fold_grounding import (
    _CLOSEOUT,
    _STALE_MISSION,
    _STALE_NEXT,
)

pytestmark = pytest.mark.offline

_PRIOR = (
    "Mission: Harvest CONSULT_PENDING tokens. "
    "Next: Restore paid plan on crsr_fb. "
    "Consolidated through agent-bus:12286#1308 at 2026-09-30T06:41:29Z "
    "(consolidate-continuity v1)."
)
_TRIGGER = "agent-bus:14759#8"


class _Output:
    def __init__(self, payload: dict) -> None:
        self.json = payload
        self.json_parse_error = None
        self.raw = ""


class _Context:
    def __init__(self, ingest: dict, distill: dict, options: dict) -> None:
        self._outputs = {"ingest": _Output(ingest), "distill": _Output(distill)}
        self.options = options

    def get_output(self, name: str) -> _Output:
        return self._outputs[name]


class _Client:
    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_apply_37643_specimen_cites_trigger_only_on_watermark(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(_client, tool: str, arguments: dict) -> dict:
        calls.append((tool, arguments))
        if tool == "assertions":
            return {"assertions": []}
        if tool == "assert":
            return {"id": 9001}
        return {}

    monkeypatch.setattr(
        "pipelines.continuity_consolidate.v1.handlers.apply.cortex_client",
        lambda: _Client(),
    )
    monkeypatch.setattr(
        "pipelines.continuity_consolidate.v1.handlers._cortex.dispatch",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "pipelines.continuity_consolidate.v1.handlers._plan.dispatch",
        fake_dispatch,
    )

    ingest = {
        "hub_id": "document:12286-continuity",
        "trigger": {
            "thread": "14759",
            "turn": 8,
            "subject": "CLOSEOUT: rag search",
            "body": _CLOSEOUT,
        },
        "payload_text": _CLOSEOUT,
        "pipeline_rows": [],
        "assertion_ids": [],
        "relationship_targets": [],
        "claims": [],
        "hub": {"description": _PRIOR},
    }
    fold = {
        "mission": _STALE_MISSION,
        "resume": {"settled": "", "live": "", "next": _STALE_NEXT},
        "claims": [
            {
                "kind": "open",
                "claim": (
                    "Restore paid plan on crsr_fb so the operator-ear house "
                    "can resume credential work."
                ),
                "evidence_uris": [_TRIGGER],
            }
        ],
    }
    ctx = _Context(ingest, fold, {"root_thread": "12286"})
    out = await ContinuityConsolidateApplyHandler().execute(None, ctx)
    result = out.json

    assert result["ok"] is True
    assert result["mission_source"] == "none"
    writes = result["writes"]
    assert any(
        row["kind"] == "mission"
        and row["status"] == "skipped"
        and row["reason"] == "ungrounded_in_closeout"
        for row in writes
    )
    assert any(
        row["kind"] == "resume" and row["reason"] == "ungrounded_in_closeout"
        for row in writes
    )
    assert any(
        row["kind"] == "claim" and row["reason"] == "ungrounded_in_closeout"
        for row in writes
    )

    asserted = [args for tool, args in calls if tool == "assert"]
    cited = [
        args
        for args in asserted
        if _TRIGGER in (args.get("evidence_uris") or [])
    ]
    assert len(cited) == 1
    assert str(cited[0]["claim"]).startswith("WATERMARK:")

    described = next(args for tool, args in calls if tool == "entity_update")
    description = described["description"]
    assert "Harvest CONSULT_PENDING" in description
    assert "Consolidated through agent-bus:12286#1308" in description
    assert f"Consolidated through {_TRIGGER}" not in description
    assert "(none folded yet)" not in description
    assert f"Last fold {_TRIGGER}" in description
    assert "no grounded mission/resume." in description
