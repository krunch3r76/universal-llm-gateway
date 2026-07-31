"""Integration tests for EnforceGroundingHandler.execute().

Exercises the pipeline handler/resolver substrate (NamespaceResolver +
handler_inputs bindings from chain.yaml). Unit tests in test_enforce_grounding.py
pin the pure guard logic; these tests are the F3 acceptance criterion:
  - AUTHORED loss → PipelineExecutionError (no StepOutput / doc_markdown)
  - happy path → stamped GENERATED block + disclaimer, authored_loss == []
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from systems.pipeline.core.dag import PipelineExecutionError
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.step_config import StepConfig
from systems.pipeline.core.step_types import SourceInput

from .enforce_grounding import EnforceGroundingHandler, _DISCLAIMER

_INVENTORY = {"functions": [{"name": "do_work", "path": "mod.py", "line": 1}]}
_INVENTORY_RAW = json.dumps(_INVENTORY)

_HANDLER_INPUTS = {
    "reviewed_doc": "review.json.doc_markdown",
    "existing_doc": "extract.json.existing_doc",
    "inventory_json": "extract.raw",
    "unsupported_claims": "review.json.unsupported_claims",
    "human_markers": "review.json.human_markers",
    "review_notes": "review.json.review_notes",
    "claim_evidence": "review.json.claim_evidence",
}


def _make_step() -> StepConfig:
    return StepConfig(
        id="enforce",
        type="doc_generate_enforce_grounding",
        handler_inputs=_HANDLER_INPUTS,
    )


def _make_context(
    *,
    existing_doc: str,
    reviewed_doc: str,
    review_json: dict | None = None,
) -> SimpleNamespace:
    review_payload = review_json or {
        "doc_markdown": reviewed_doc,
        "unsupported_claims": [],
        "human_markers": [],
        "review_notes": [],
        "claim_evidence": [],
    }
    extract_json = {"existing_doc": existing_doc}
    return SimpleNamespace(
        execution_id="exec-f3-enforce-test",
        _proxy=None,
        source=SourceInput(text="", messages=None),
        options={},
        outputs={
            "extract": StepOutput(
                raw=_INVENTORY_RAW,
                json=extract_json,
                step_id="extract",
            ),
            "review": StepOutput(
                raw=json.dumps(review_payload),
                json=review_payload,
                step_id="review",
            ),
        },
    )


@pytest.mark.asyncio
async def test_execute_raises_on_authored_loss_no_step_output():
    """F3 guarantee: dropped AUTHORED body aborts before any doc_markdown is emitted."""
    existing = (
        "<!-- AUTHORED:START -->\ncritical human design note\n<!-- AUTHORED:END -->"
    )
    reviewed = "## Overview\nregenerated body without the human note\n"
    context = _make_context(existing_doc=existing, reviewed_doc=reviewed)
    handler = EnforceGroundingHandler()
    step = _make_step()

    with pytest.raises(PipelineExecutionError, match="AUTHORED region"):
        await handler.execute(step, context)

    assert "enforce" not in context.outputs


@pytest.mark.asyncio
async def test_execute_happy_path_stamps_generated_and_clears_authored_loss():
    region = "critical human design note"
    existing = f"<!-- AUTHORED:START -->\n{region}\n<!-- AUTHORED:END -->"
    reviewed = (
        f"{region}\n\n"
        "<!-- GENERATED:START -->\n"
        "auto-generated section\n"
        "<!-- GENERATED:END -->"
    )
    context = _make_context(existing_doc=existing, reviewed_doc=reviewed)
    handler = EnforceGroundingHandler()
    step = _make_step()

    out = await handler.execute(step, context)

    assert isinstance(out, StepOutput)
    assert out.json is not None
    assert out.json["authored_loss"] == []
    doc = out.json["doc_markdown"]
    assert "inventory_sha=" in doc
    assert "generated=" in doc
    assert _DISCLAIMER in doc
    assert "auto-generated section" in doc
