"""
Deterministic post-step for doc-generate: enforce grounding guarantees.

Runs AFTER the LLM review step. Pure string/regex work — no model, no bodies:
  1. AUTHORED region preservation guard (string equality; blocking via authored_loss).
  2. HUMAN marker drop detection (advisory — humans legitimately resolve these).
  3. Deterministic missing_coverage (inventory symbol set vs doc-mention scan).
  4. GENERATED provenance stamp (inventory_sha + generated date) + consumer-visible
     disclaimer injected into every GENERATED block.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, override

from doc_extraction import detect_inventory_divergence
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

from .events import (
    doc_generate_authored_loss,
    doc_generate_enforce_success,
)

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_DISCLAIMER = (
    "_Generated from docstrings, signatures, and imports; claims reflect what the "
    "source **declares**, not verified runtime behavior. doc-generate verifies "
    "doc<->docstring consistency, not docstring<->behavior truth._"
)
_GENERATED_START_RE = re.compile(r"<!--\s*GENERATED:START[^>]*-->")
_GENERATED_END = "<!-- GENERATED:END -->"
# AUTHORED regions: support both an explicit START/END pair and a bare marker that
# runs until the next marker or EOF (no doc-migration required).
_AUTHORED_PAIR_RE = re.compile(
    r"<!--\s*AUTHORED:START\s*-->(?P<body>.*?)<!--\s*AUTHORED:END\s*-->",
    re.DOTALL,
)
_AUTHORED_BARE_RE = re.compile(
    r"<!--\s*AUTHORED\s*-->(?P<body>.*?)(?=<!--\s*(?:GENERATED:START|AUTHORED)|\Z)",
    re.DOTALL,
)
_HUMAN_RE = re.compile(r"<!--\s*HUMAN:[^>]*-->")


def _publish_event(context: PipelineContext, event: object) -> None:
    proxy = getattr(context, "_proxy", None)
    event_bus = getattr(proxy, "event_bus", None) if proxy else None
    if event_bus is None:
        return
    _ = asyncio.create_task(event_bus.publish_nowait(event))


def _authored_regions(doc: str) -> list[str]:
    """Return normalized AUTHORED region bodies (paired form first, then bare)."""
    regions = [m.group("body").strip() for m in _AUTHORED_PAIR_RE.finditer(doc)]
    regions.extend(m.group("body").strip() for m in _AUTHORED_BARE_RE.finditer(doc))
    return [r for r in regions if r]


def _symbol_set(inventory: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten inventory into (scope:name) coverage targets."""
    symbols: list[tuple[str, str]] = []
    for module in inventory.get("modules", []):
        path = str(module.get("path", ""))
        if path:
            symbols.append(("module", path))
    for cls in inventory.get("classes", []):
        name = str(cls.get("name", ""))
        if name and not name.startswith("_"):
            symbols.append(("class", name))
    for fn in inventory.get("functions", []):
        name = str(fn.get("name", ""))
        if name and not name.startswith("_"):
            symbols.append(("function", name))
    return symbols


def _missing_coverage(doc: str, inventory: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for scope, ident in _symbol_set(inventory):
        needle = ident.rsplit("/", 1)[-1] if scope == "module" else ident
        if needle and needle not in doc:
            missing.append(f"{scope}:{ident}")
    return missing


def _stamp_and_disclaim(doc: str, inventory_sha: str, generated: str) -> str:
    """Rewrite every GENERATED:START marker with provenance + inject disclaimer."""
    stamped_marker = (
        f"<!-- GENERATED:START inventory_sha={inventory_sha} generated={generated} -->"
    )

    def _open(_m: re.Match[str]) -> str:
        return f"{stamped_marker}\n{_DISCLAIMER}"

    return _GENERATED_START_RE.sub(_open, doc)


class EnforceGroundingHandler(BaseHandler):
    """Deterministic grounding/preservation enforcement for generated docs."""

    step_type: str = "doc_generate_enforce_grounding"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}

        reviewed_doc = self._resolve_input(resolver, step, "reviewed_doc", inputs)
        existing_doc = self._resolve_input(resolver, step, "existing_doc", inputs)
        inventory_json = self._resolve_input(resolver, step, "inventory_json", inputs)
        unsupported_claims = self._resolve_input(
            resolver, step, "unsupported_claims", inputs
        )
        human_markers = self._resolve_input(resolver, step, "human_markers", inputs)
        review_notes = self._resolve_input(resolver, step, "review_notes", inputs)
        claim_evidence = self._resolve_input(resolver, step, "claim_evidence", inputs)

        reviewed_doc = reviewed_doc if isinstance(reviewed_doc, str) else ""
        existing_doc = existing_doc if isinstance(existing_doc, str) else ""
        inventory_raw = inventory_json if isinstance(inventory_json, str) else "{}"
        unsupported_claims = (
            unsupported_claims if isinstance(unsupported_claims, list) else []
        )
        human_markers = human_markers if isinstance(human_markers, list) else []
        review_notes = review_notes if isinstance(review_notes, list) else []
        claim_evidence = claim_evidence if isinstance(claim_evidence, list) else []
        try:
            inventory: dict[str, Any] = json.loads(inventory_raw)
        except json.JSONDecodeError:
            inventory = {}

        inventory_sha = hashlib.sha256(inventory_raw.encode("utf-8")).hexdigest()[:12]
        generated = _dt.date.today().isoformat()

        authored_loss = [
            region
            for region in _authored_regions(existing_doc)
            if region not in reviewed_doc
        ]
        human_markers_dropped = [
            marker
            for marker in _HUMAN_RE.findall(existing_doc)
            if marker not in reviewed_doc
        ]
        missing_coverage = _missing_coverage(reviewed_doc, inventory)
        divergence = detect_inventory_divergence(inventory)
        final_doc = _stamp_and_disclaim(reviewed_doc, inventory_sha, generated)

        if authored_loss:
            _publish_event(
                context,
                doc_generate_authored_loss(
                    execution_id=context.execution_id,
                    step_id=step.id,
                    lost_count=len(authored_loss),
                ),
            )
            logger.error(
                "Step '%s': %d AUTHORED region(s) lost in generated doc",
                step.id,
                len(authored_loss),
            )
            # Hard gate: select_output picks the first candidate whose output EXISTS
            # and is not _skipped — it does NOT inspect StepOutput.error. A string-
            # equality preservation failure must therefore abort the run by raising,
            # not by returning an errored output (which would still be emitted).
            from systems.pipeline.core.dag import PipelineExecutionError

            raise PipelineExecutionError(
                f"Step '{step.id}': {len(authored_loss)} AUTHORED region(s) dropped "
                f"or altered in generated doc — refusing to emit. Lost: {authored_loss}"
            )

        result: dict[str, Any] = {
            "doc_markdown": final_doc,
            "authored_loss": authored_loss,
            "missing_coverage": missing_coverage,
            "divergence": divergence,
            "human_markers_dropped": human_markers_dropped,
            "unsupported_claims": unsupported_claims,
            "human_markers": human_markers,
            "review_notes": review_notes,
            "claim_evidence": claim_evidence,
            "inventory_sha": inventory_sha,
        }
        _publish_event(
            context,
            doc_generate_enforce_success(
                execution_id=context.execution_id,
                step_id=step.id,
                authored_loss_count=len(authored_loss),
                missing_coverage_count=len(missing_coverage),
                inventory_sha=inventory_sha,
            ),
        )
        return StepOutput(
            raw=json.dumps(result, indent=2),
            json=result,
            step_id=step.id,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        for required in (
            "reviewed_doc",
            "existing_doc",
            "inventory_json",
            "unsupported_claims",
            "human_markers",
            "review_notes",
            "claim_evidence",
        ):
            if required not in inputs:
                errors.append(
                    f"Step '{step.id}': doc_generate_enforce_grounding requires "
                    f"'{required}' in handler_inputs"
                )
        return errors
