"""MCP wrapper for the manage-authoritative fleet liveness snapshot.

The life and code surfaces call the same read-only manage JSON-RPC method so
operators receive identical evidence and cannot accidentally bypass the
load-surface-specific uncertainty rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.manage import _call_manage, _extract_result

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_fleet_liveness_tools(mcp: FastMCP) -> None:
    """Register the direct fleet liveness verb on the current MCP surface."""

    @mcp.tool(title="Fleet Liveness")
    def fleet_liveness(
        code_ref: str | None = None,
        activation_validation_id: str | None = None,
        service: str | None = None,
        services: list[str] | None = None,
        projection: str | None = None,
    ) -> dict[str, Any]:
        """Return fresh service markers, dirty paths, and honest load evidence.

        Container-copy services use hashes from their running load location.
        Host-process and bind-mounted services expose temporal or indeterminate
        evidence without promoting start time into proof of execution.

        ``service`` / ``services`` return only those rows plus full checkout
        porcelain. Omitted services are absent — omit ≠ healthy. Unknown slug
        or empty ``services=[]`` raises (empty list raises even when
        ``service=`` is set). Rows and ``service_filter`` are deduped in
        ``SERVICE_SLUGS`` order. Unfiltered call preserves full fleet.

        ``projection=compact`` omits checkout porcelain and heavy per-service
        fields while keeping ``service``, ``pid``, ``reported_version``, and
        optional ``code_ref_validation.liveness`` answer/relation.
        """
        params: dict[str, Any] = {}
        if code_ref is not None:
            params["code_ref"] = code_ref
        if activation_validation_id is not None:
            params["activation_validation_id"] = activation_validation_id
        if service is not None:
            params["service"] = service
        if services is not None:
            params["services"] = services
        if projection is not None:
            params["projection"] = projection
        raw = _call_manage(
            {
                "jsonrpc": "2.0",
                "method": "fleet_liveness",
                "params": params,
                "id": 1,
            },
            timeout=30.0,
        )
        return _extract_result(raw)


__all__ = ["register_fleet_liveness_tools"]
