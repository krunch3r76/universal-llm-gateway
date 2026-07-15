"""MCP tool — inline advisor consultation via a higher-tier model.

Sends a focused problem description to a high-intelligence model (Opus by
default) through the chat-dispatch pipeline, with a system prompt constraining
output to concise, actionable guidance. No tools, no Cortex, no RAG — pure
reasoning over the provided context.

Designed for the advisor timing pattern: checkpoint 1 (before substantive
work), checkpoint 3 (recurring failure), and lightweight checkpoint 2 (before
declaring done). Heavier consultations use ``team_dispatch``, ``panel_dispatch``,
or ``/consult-*``. This is a consult checkpoint, not a ``team_dispatch`` role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record
from universal_logging import get_logger

from tools.pipeline import _pipeline_run

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_DEFAULT_MODEL = "anthropic/claude-opus-4-6"
_MAX_ADVICE_TOKENS = 1024
_ADVISOR_PIPELINE_TIMEOUT = 120.0
_ADVISOR_TEMPERATURE = 0.2

_ADVISOR_SYSTEM = """\
You are a senior engineering advisor. Review the problem context provided and \
return a concise, actionable plan.

Rules:
- Respond in under 200 words using enumerated steps, not explanations.
- Focus on the specific decision point, failure mode, or trade-off presented.
- If the framing seems wrong, say so — reframe the problem before advising.
- If you see a likely root cause for a recurring failure, lead with that.
- Do not generate code. Describe what to do and where, not how to type it.
- If the problem is trivial and needs no advice, say "Proceed as planned" \
and nothing else.\
"""


def build_advisor_pipeline_options(
    model: str,
    max_tokens: int = _MAX_ADVICE_TOKENS,
) -> dict[str, Any]:
    """Build ``pipeline_options`` for the chat-dispatch advisor path.

    ``max_tokens`` / ``temperature`` must nest under ``generation_parameters`` —
    top-level keys are rejected by ``frontier_dispatch_v1`` admission
    (``UnknownPipelineOptionsError``).
    """
    return {
        "model": model,
        "mcp": False,
        "generation_parameters": {
            "max_tokens": max_tokens,
            "temperature": _ADVISOR_TEMPERATURE,
        },
    }


def register_advisor_tools(mcp: FastMCP) -> None:
    """Register the advisor tool on the MCP server instance."""

    @mcp.tool(title="Advisor")
    def advisor(
        problem: str,
        context: str = "",
        model: str = _DEFAULT_MODEL,
        max_tokens: int = _MAX_ADVICE_TOKENS,
    ) -> dict[str, Any]:
        """Consult a higher-intelligence model for strategic guidance mid-task.

        Sends a focused problem description to a reasoning model (Opus 4.6 by
        default) and returns concise, actionable advice. The advisor has no
        tool access — it reasons purely over what you provide.

        This tool is distinct from ``team_dispatch`` role-based consults. Use it
        as a lightweight checkpoint when the lead agent can package the relevant
        session concern directly; use ``team_dispatch`` when the consult needs
        MCP tools, bus-thread delivery, or a named dispatch role.

        **When to use** (advisor timing checkpoints):

        1. **Before substantive work** — you've gathered context and are about
           to commit to an approach. Describe the approach and ask whether it's
           sound.
        2. **On recurring failure** — same fix failed twice. Describe what was
           tried, what failed, and the error. The advisor escapes your framing.
        3. **Before declaring done** — quick sanity check on completed work.
        4. **At a decision fork** — two approaches, unclear trade-offs.

        **Session context packaging**:

        - Cursor sessions can summarize or quote the relevant transcript/tool
          evidence in ``context``.
        - Web sessions should provide a compact "session concern bundle":
          goal, evidence already seen, candidate approach, uncertainty, and the
          exact decision requested. The advisor cannot inspect the browser/chat
          transcript unless you include it.
        - If the concern depends on live files, events, Cortex, RAG, or bus
          history the advisor cannot fetch, gather that evidence first or use a
          tool-capable consult path instead.

        **When NOT to use**:

        - Trivial/mechanical changes (rename, typo, format)
        - When you need tool access or code execution (use ``/consult-implement``)
        - Complex multi-model advisory (use ``panel_dispatch`` or paired ``team_dispatch``; see skill ``dispatch-workflow``)
        - Architectural planning (use ``/consult-plan`` or Plan mode)

        Args:
            problem: The decision point, failure description, or trade-off to
                evaluate. Be specific — include what was tried and what failed.
            context: Optional additional context: file contents, error output,
                prior approach description, or a compact session concern bundle.
                Keep concise — the advisor budget is small.
            model: Model ID for the advisor. Default: ``anthropic/claude-opus-4-6``.
                Must be a cloud model reachable via Stargate.
            max_tokens: Maximum tokens for the advisor response. Default: 1024.

        Returns:
            ``{"advice": str, "model": str, "usage": {...}}``
            or ``{"error": "..."}`` on failure.
        """
        t0 = monotonic_now()
        record("mcp.advisor.called", model=model)

        user_content = problem
        if context:
            user_content = f"{problem}\n\n---\nContext:\n{context}"

        messages = [
            {"role": "system", "content": _ADVISOR_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        options = build_advisor_pipeline_options(model, max_tokens)

        result = _pipeline_run(
            "chat-dispatch",
            messages,
            options,
            _ADVISOR_PIPELINE_TIMEOUT,
        )

        if "error" in result:
            err_text = str(result["error"])
            if "timed out" in err_text.lower():
                record("mcp.advisor.error", error="timeout", model=model)
                return {"error": "Advisor timeout — model may be overloaded"}
            record("mcp.advisor.error", error="pipeline", model=model)
            detail = result.get("detail")
            if detail:
                return {
                    "error": "Advisor error — pipeline failed",
                    "detail": str(detail)[:500],
                }
            return {"error": "Advisor error — pipeline failed"}

        duration = monotonic_now() - t0
        usage_raw = result.get("usage") or {}
        returned_model = result.get("model", model)
        advice = result.get("content", "")

        record(
            "mcp.advisor.completed",
            duration_s=round(duration, 3),
            model=returned_model,
            advice_tokens=usage_raw.get("completion_tokens", 0),
        )
        logger.info("advisor completed: %.3fs, model=%s", duration, returned_model)

        return {
            "advice": advice,
            "model": str(returned_model),
            "usage": {
                "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                "completion_tokens": usage_raw.get("completion_tokens", 0),
            },
        }
