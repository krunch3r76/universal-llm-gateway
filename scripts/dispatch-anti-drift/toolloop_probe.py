"""Lane B.3 — tool-loop fidelity probe (G2 anti-drift CI, SoT §6).

Validates that a model's tool calls actually execute through the surface's
adapter — the protocol round-trip, not a knob. Sends real requests via
``POST /api/v1/frontier/dispatch`` with ``op=generate``, ``mcp=true`` and
asserts the observed tool-execution count vs the expected minimum.

Tasks:
- Task A (1 tool, max_tool_turns=3):
    ``cortex(tool=stats, arguments={})`` → one-sentence answer.
- Task B (2 tools, max_tool_turns=5):
    ``stats`` then ``entity_get(..., intent=card)``.

Per (model, surface) assertions:
- ``tool_executed ≥ expected`` (1 for A, 2 for B).
- Loop terminated correctly (no truncation / runaway).
- No malformed/unexpected-tool-call failure class.

Regression classes the probe must catch (SoT §6):
- ``tool_executed=0`` — model didn't call any tool.
- ``gemini-2.5`` → ``UNEXPECTED_TOOL_CALL`` error class.
- ``gemini-3`` → ``MALFORMED_FUNCTION_CALL`` error class.

Golden non-regression anchor: ``openai/gpt-5.5`` — must never regress.

Observability: consumes existing ``pipeline.frontier.dispatch.tool.called``
events (frontier_tools.py) via the execution-ID forensics path.  No new
``@event_factory`` is introduced (SoT §6 invariant).

[universal:rest] — all HTTP via ``transport_utils.make_sync_client``.
[universal:modelid] — ``ModelId`` for identity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from transport_utils.client_factory import DEFAULT_STARGATE_URL, make_sync_client

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_TASK_A_PROMPT = (
    "Call cortex(tool='stats', arguments={}) and summarize the result in one sentence."
)
_TASK_B_PROMPT = (
    "First call cortex(tool='stats', arguments={}) to get stats, "
    "then call cortex(tool='entity_get', arguments={'entity_id': 'todo:gemini-mcp-tool-loop-fidelity-fixes', 'intent': 'card'}) "
    "and summarize both results."
)

# Regression classes that the probe must surface as failures.
_REGRESSION_CLASSES = frozenset({"UNEXPECTED_TOOL_CALL", "MALFORMED_FUNCTION_CALL"})

# (model, task_label, prompt, max_tool_turns, min_tools_expected)
_PROBE_MATRIX: list[tuple[str, str, str, int, int]] = [
    # Golden anchor — must never regress.
    ("openai/gpt-5.5", "task_a", _TASK_A_PROMPT, 3, 1),
    ("openai/gpt-5.5", "task_b", _TASK_B_PROMPT, 5, 2),
    # Anthropic (client-side MCP).
    ("anthropic/claude-sonnet-4-6", "task_a", _TASK_A_PROMPT, 3, 1),
    ("anthropic/claude-sonnet-4-6", "task_b", _TASK_B_PROMPT, 5, 2),
    # xAI.
    ("xai/grok-4.3", "task_a", _TASK_A_PROMPT, 3, 1),
    # Google (regression-class monitoring).
    ("google/gemini-3-pro", "task_a", _TASK_A_PROMPT, 3, 1),
]


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class ToolLoopFinding:
    model: str
    task: str
    execution_id: str | None
    tool_executed: int
    min_expected: int
    terminated_correctly: bool
    regression_class: (
        str | None
    )  # UNEXPECTED_TOOL_CALL | MALFORMED_FUNCTION_CALL | None
    drift: bool
    elapsed_ms: float
    note: str = ""

    @classmethod
    def from_error(
        cls,
        model: str,
        task: str,
        note: str,
        elapsed_ms: float = 0.0,
    ) -> ToolLoopFinding:
        return cls(
            model=model,
            task=task,
            execution_id=None,
            tool_executed=0,
            min_expected=0,
            terminated_correctly=False,
            regression_class=None,
            drift=True,
            elapsed_ms=elapsed_ms,
            note=note,
        )


@dataclass
class ToolLoopReport:
    findings: list[ToolLoopFinding] = field(default_factory=list)

    @property
    def drift_count(self) -> int:
        return sum(1 for f in self.findings if f.drift)

    def passed(self) -> bool:
        return self.drift_count == 0

    def golden_anchor_passed(self) -> bool:
        """Whether the gpt-5.5 golden anchor is clean."""
        return all(not f.drift for f in self.findings if f.model == "openai/gpt-5.5")


# --------------------------------------------------------------------------- #
# Probe execution
# --------------------------------------------------------------------------- #


def _build_request(model: str, prompt: str, max_tool_turns: int) -> dict[str, Any]:
    return {
        "op": "generate",
        "model": model,
        "mcp": True,
        "messages": [{"role": "user", "content": prompt}],
        "max_tool_turns": max_tool_turns,
        "max_tokens": 512,
    }


def _extract_execution_id(response_data: dict[str, Any]) -> str | None:
    return response_data.get("execution_id") or response_data.get("id")


def _count_tool_calls(response_data: dict[str, Any]) -> int:
    """Extract tool_executed count from the dispatch response."""
    # Check top-level field first (preferred).
    if "tool_executed" in response_data:
        return int(response_data["tool_executed"])
    # Fall back to counting tool_use blocks in message content.
    messages = response_data.get("messages", [])
    count = 0
    for msg in messages:
        content = msg.get("content", [])
        if isinstance(content, list):
            count += sum(1 for block in content if block.get("type") == "tool_use")
    return count


def _detect_regression_class(response_data: dict[str, Any]) -> str | None:
    """Check response for known tool-loop regression error classes."""
    error = response_data.get("error", {})
    if isinstance(error, dict):
        error_type = error.get("type", "") or ""
        for cls in _REGRESSION_CLASSES:
            if cls in error_type:
                return cls
    return None


def _probe_one(
    model: str,
    task: str,
    prompt: str,
    max_tool_turns: int,
    min_expected: int,
) -> ToolLoopFinding:
    req_body = _build_request(model, prompt, max_tool_turns)
    t0 = time.monotonic()
    try:
        with make_sync_client(DEFAULT_STARGATE_URL, timeout=120.0) as client:
            resp = client.post("/api/v1/frontier/dispatch", json=req_body)
    except Exception as exc:
        return ToolLoopFinding.from_error(
            model, task, f"network error: {exc}", elapsed_ms=0.0
        )

    elapsed_ms = (time.monotonic() - t0) * 1000
    if resp.status_code >= 500:
        return ToolLoopFinding.from_error(
            model, task, f"HTTP {resp.status_code}", elapsed_ms=elapsed_ms
        )

    try:
        data = resp.json()
    except Exception as exc:
        return ToolLoopFinding.from_error(
            model, task, f"JSON parse error: {exc}", elapsed_ms=elapsed_ms
        )

    execution_id = _extract_execution_id(data)
    tool_executed = _count_tool_calls(data)
    regression_class = _detect_regression_class(data)
    terminated = resp.status_code < 400 and regression_class is None
    drift = (
        tool_executed < min_expected or regression_class is not None or not terminated
    )

    note = ""
    if tool_executed < min_expected:
        note += f"tool_executed={tool_executed} < expected {min_expected}; "
    if regression_class:
        note += f"regression_class={regression_class}; "
    if not terminated:
        note += "loop did not terminate correctly; "

    return ToolLoopFinding(
        model=model,
        task=task,
        execution_id=execution_id,
        tool_executed=tool_executed,
        min_expected=min_expected,
        terminated_correctly=terminated,
        regression_class=regression_class,
        drift=drift,
        elapsed_ms=elapsed_ms,
        note=note.rstrip("; "),
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def run_toolloop_probe(
    *,
    models: list[str] | None = None,
) -> ToolLoopReport:
    """Run the tool-loop fidelity probe for all (or a subset of) matrix models.

    ``models`` — if supplied, restrict probe to these model ids (for targeted
    model-add gate runs). Default: full matrix including the gpt-5.5 anchor.
    """
    report = ToolLoopReport()
    for model, task, prompt, max_tool_turns, min_expected in _PROBE_MATRIX:
        if models and model not in models:
            continue
        finding = _probe_one(model, task, prompt, max_tool_turns, min_expected)
        report.findings.append(finding)
    return report
