"""substrate_feedback entity resolution and graph-write adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.substrate_feedback import (
    maybe_post_substrate_feedback,
    resolve_substrate_feedback_entity_id,
)

pytestmark = pytest.mark.offline


def test_resolve_entity_id_from_todo_token() -> None:
    assert (
        resolve_substrate_feedback_entity_id(
            subject="Implement todo:dispatch-verb-surface",
            body="",
        )
        == "todo:dispatch-verb-surface"
    )


def test_resolve_entity_id_from_entity_id_line() -> None:
    assert (
        resolve_substrate_feedback_entity_id(
            subject="Implement",
            body="entity_id: friction:123\n",
        )
        == "friction:123"
    )


@pytest.mark.asyncio
async def test_substrate_feedback_writes_claim_when_entity_resolved() -> None:
    job = AutoJob(
        job_id="j1",
        thread_id="77",
        turn_number=2,
        subject="Implement todo:dispatch-verb-surface",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    with patch(
        "services.git_integration_worker.cursor_auto.substrate_feedback.write_claim",
        return_value={"item": {"id": 5}},
    ) as write_claim:
        result = await maybe_post_substrate_feedback(
            job,
            sdk_body="audit warning in closeout",
            closeout_body=None,
            bus=bus,
        )

    write_claim.assert_called_once()
    assert result is not None
    assert "operator carries graph write" not in bus.reply.call_args.kwargs["body"]
    assert "substrate_graph_write" in bus.reply.call_args.kwargs["body"]


@pytest.mark.asyncio
async def test_substrate_feedback_names_verb_when_entity_missing() -> None:
    job = AutoJob(
        job_id="j2",
        thread_id="77",
        turn_number=2,
        subject="Implement something",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    with patch(
        "services.git_integration_worker.cursor_auto.substrate_feedback.write_claim",
    ) as write_claim:
        await maybe_post_substrate_feedback(
            job,
            sdk_body="audit warning in closeout",
            closeout_body=None,
            bus=bus,
        )

    write_claim.assert_not_called()
    body = bus.reply.call_args.kwargs["body"]
    assert "substrate_graph_write" in body
    assert "entity_id" in body
