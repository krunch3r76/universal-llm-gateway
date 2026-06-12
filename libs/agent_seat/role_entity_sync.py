"""Cortex ``role:*`` execution-contract attribute builder for entity sync."""

from __future__ import annotations

from typing import Any

from agent_seat.profiles import CapabilityProfile, RoleProfile, derive_inline_only


def build_role_execution_attributes(
    role_name: str,
    role: RoleProfile,
    profile: CapabilityProfile,
) -> dict[str, Any]:
    """Execution-contract fields merged into Cortex ``role:`` attributes."""
    inline = derive_inline_only(profile)
    required_tools: list[str] = [] if inline else ["cortex", "fs", "agent_bus"]
    verification: list[dict[str, str]] = []
    if role_name == "reviewer":
        verification = [
            {"skill": "skill:named-entity-verification-gate", "hook": "admit"}
        ]
    return {
        "purpose": role.description,
        "required_tools": required_tools,
        "mcp_required": not inline,
        "verification": verification,
        "failure_mode": {
            "on_tool_unavailable": "fail_closed",
            "on_model_unavailable": "escalate_to_operator",
            "on_uncertainty": "escalate_to_operator",
            "on_contract_violation": "reject_dispatch",
        },
        "output_schema": [
            "markdown_response",
            "optional_cortex_assertions_with_evidence_uris",
        ],
        "capability_tier": "inline-only" if inline else None,
        "tool_surface": profile.tool_surface,
        "required_model_substring": None,
    }


def resolve_dispatch_capabilities(
    *, model: str, mcp_enabled: bool | None = None
) -> dict[str, Any]:
    """Effective tool-surface contract for a resolved dispatch model.

    ``mcp_enabled`` is the *effective* admission gate after the caller ``mcp``
    knob (``mcp_enabled_for_team_dispatch`` /
    ``mcp_enabled_for_frontier_dispatch``). Passing it makes the echoed
    transparency surface (``capabilities``) single-sourced with the pipeline
    gate (``pipeline_options["mcp"]``) so they cannot drift — the prior
    model-only resolution forced callers to re-patch ``mcp_enabled`` /
    ``inline_only`` / ``tool_surface`` by hand after admission (thread 1653
    finalize arc). When omitted the base model-only admission applies — a pure
    capability preview with no caller intent (panel members, introspection).
    """
    if mcp_enabled is None:
        from agent_seat.profiles import client_side_mcp_tool_loop_admitted

        mcp_enabled = client_side_mcp_tool_loop_admitted(model)
    return {
        "resolved_model": model,
        "inline_only": not mcp_enabled,
        "mcp_enabled": mcp_enabled,
        "tool_surface": "inline-only" if not mcp_enabled else "mcp",
    }
