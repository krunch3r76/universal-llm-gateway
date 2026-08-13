"""Declared per-service lib ownership for propagation resolution.

The manifest is the **actor** for closeout propagation: ``libs/`` edits resolve
through declared ownership, not import-graph inference at runtime. The closure in
``propagation_libs_closure`` audits that declared sets stay complete (CI).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_LIBS_DIR = "libs"


@dataclass(frozen=True, slots=True)
class ServiceOwnership:
    """One manage service slug and the repo paths it owns."""

    path_prefix: str
    owned_libs: frozenset[str]


_SERVICE_OWNERSHIP: dict[str, ServiceOwnership] = {
    "agent_bus": ServiceOwnership(
        path_prefix="services/agent-bus/",
        owned_libs=frozenset({"agent_bus_store", "agent_seat", "cdp_ask", "claude_bundles", "cortex_store", "cursor_capabilities", "gen_rules", "implement_admission", "llm_adapters", "markdown_fence", "markdown_sections", "markdown_xml_blocks", "model_capabilities", "model_id", "ocr_core", "predicate_form", "role_lint", "sse", "stargate_chat", "transport_utils", "universal_event_bus", "universal_logging"}),
    ),
    "cortex_api": ServiceOwnership(
        path_prefix="libs/cortex_store/",
        owned_libs=frozenset({"agent_bus_store", "agent_seat", "cdp_ask", "claude_bundles", "cortex_store", "cursor_capabilities", "gen_rules", "implement_admission", "llm_adapters", "markdown_fence", "markdown_sections", "markdown_xml_blocks", "model_capabilities", "model_id", "ocr_core", "predicate_form", "role_lint", "sse", "stargate_chat", "transport_utils", "universal_event_bus", "universal_logging"}),
    ),
    "event_service": ServiceOwnership(
        path_prefix="services/event-service/",
        owned_libs=frozenset({}),
    ),
    "git_integration_worker": ServiceOwnership(
        path_prefix="services/git_integration_worker/",
        owned_libs=frozenset({"agent_bus_store", "agent_seat", "cdp_ask", "charter_runner_store", "claude_bundles", "contract_vocab", "cortex_store", "cursor_capabilities", "deploy_identity", "email_routing", "foo", "gen_rules", "git_integrate", "implement_admission", "llm_adapters", "markdown_fence", "markdown_sections", "markdown_xml_blocks", "model_capabilities", "model_id", "ocr_core", "pager_notify", "predicate_form", "process_ipc", "role_lint", "sse", "stargate_chat", "transport_utils", "universal_concurrency", "universal_event_bus", "universal_logging", "universal_protocol", "universal_transport", "universal_workspace"}),
    ),
    "mcp": ServiceOwnership(
        path_prefix="services/mcp-server/",
        owned_libs=frozenset({"agent_bus_store", "agent_seat", "cdp_ask", "claude_bundles", "contract_vocab", "cortex_store", "cursor_capabilities", "deploy_identity", "document_text", "durable_sink", "email_routing", "gen_rules", "implement_admission", "life_intent", "llm_adapters", "markdown_fence", "markdown_sections", "markdown_xml_blocks", "mcp_dispatch", "model_capabilities", "model_id", "ocr_core", "pager_notify", "predicate_form", "provider_model_limits", "role_lint", "sse", "stargate_chat", "transport_utils", "universal_event_bus", "universal_logging", "universal_workspace"}),
    ),
    "rag": ServiceOwnership(
        path_prefix="services/rag/",
        owned_libs=frozenset({"agent_bus_store", "agent_seat", "cdp_ask", "claude_bundles", "cortex_store", "cursor_capabilities", "gen_rules", "implement_admission", "llm_adapters", "markdown_fence", "markdown_sections", "markdown_xml_blocks", "model_capabilities", "model_id", "ocr_core", "predicate_form", "role_lint", "sse", "stargate_chat", "transport_utils", "universal_concurrency", "universal_event_bus", "universal_hot_reload", "universal_logging"}),
    ),
    "cdp_ask": ServiceOwnership(
        path_prefix="libs/cdp_ask/",
        owned_libs=frozenset({"admission_common", "cdp_ask", "claude_bundles", "cortex_store", "deploy_identity", "pager_notify", "transport_utils", "universal_event_bus", "universal_logging", "universal_workspace"}),
    ),
    "cloud_proxy": ServiceOwnership(
        path_prefix="services/universal_cloud_proxy/",
        owned_libs=frozenset({"agent_bus_store", "agent_seat", "cdp_ask", "claude_bundles", "cortex_store", "cursor_capabilities", "event_store", "gen_rules", "implement_admission", "llm_adapters", "markdown_fence", "markdown_sections", "markdown_xml_blocks", "model_capabilities", "model_id", "ocr_core", "predicate_form", "process_ipc", "role_lint", "sse", "stargate_chat", "transport_utils", "universal_concurrency", "universal_event_bus", "universal_logging", "universal_protocol", "universal_transport"}),
    ),
    "gateway": ServiceOwnership(
        path_prefix="services/_universal-llm-gateway/",
        owned_libs=frozenset({"inference_djinn", "llm_adapters", "model_id", "process_ipc", "sse", "universal_concurrency", "universal_event_bus", "universal_hot_reload", "universal_logging", "universal_protocol", "universal_transport", "universal_workspace"}),
    ),
    "stargate": ServiceOwnership(
        path_prefix="services/universal-stargate/",
        owned_libs=frozenset({"admission_common", "agent_bus_store", "agent_seat", "cdp_ask", "claude_bundles", "cortex_store", "cursor_capabilities", "dispatch_knob_policy", "event_store", "frontier_observability", "gen_rules", "implement_admission", "intelligence_profiles", "life_intent", "llm_adapters", "markdown_fence", "markdown_sections", "markdown_xml_blocks", "model_capabilities", "model_id", "ocr_core", "pipeline_assess_registry", "predicate_form", "process_ipc", "provenance", "role_lint", "skills_mount", "sse", "stargate_chat", "transport_utils", "universal_concurrency", "universal_event_bus", "universal_hot_reload", "universal_logging", "universal_protocol", "universal_transport"}),
    ),
}


def service_ownership() -> dict[str, ServiceOwnership]:
    """Return the declared ownership map (read-only view)."""
    return _SERVICE_OWNERSHIP


def path_prefixes() -> tuple[tuple[str, str], ...]:
    """``(services/ prefix, slug)`` pairs for closure audit."""
    return tuple(
        (own.path_prefix, slug) for slug, own in sorted(_SERVICE_OWNERSHIP.items())
    )


def slug_for_service_path(path: str) -> str | None:
    """Map a repo-relative ``services/`` Python path to a manage slug."""
    for slug, own in _SERVICE_OWNERSHIP.items():
        if path.startswith(own.path_prefix) and path.endswith(".py"):
            return slug
    return None


def declared_services_for_lib(lib_name: str) -> tuple[str, ...]:
    """Service slugs that declare ownership of a top-level ``libs/`` name."""
    owners = [slug for slug, own in _SERVICE_OWNERSHIP.items() if lib_name in own.owned_libs]
    return tuple(sorted(owners))


def lib_name_for_path(path: str) -> str | None:
    """Return the top-level ``libs/`` name a repo-relative *path* belongs to."""
    parts = Path(str(path or "")).parts
    if len(parts) < 2 or parts[0] != _LIBS_DIR or not str(path).endswith(".py"):
        return None
    second = parts[1]
    return second[:-3] if len(parts) == 2 and second.endswith(".py") else second


def declared_services_for_lib_path(path: str) -> tuple[str, ...]:
    """Declared service slugs for a ``libs/`` edit path."""
    name = lib_name_for_path(path)
    if name is None:
        return ()
    return declared_services_for_lib(name)


def audit_sync_restart_slug(slug: str, lib_paths: list[str]) -> list[str]:
    """Report manifest disagreements for explicit ``sync_restart`` residue lines."""
    disagreements: list[str] = []
    for path in lib_paths:
        declared = declared_services_for_lib_path(path)
        if not declared or slug in declared:
            continue
        joined = ", ".join(declared)
        disagreements.append(
            f"manifest_audit: sync_restart:{slug} disagrees with declared owners of {path} ({joined})"
        )
    return disagreements


__all__ = [
    "ServiceOwnership",
    "audit_sync_restart_slug",
    "declared_services_for_lib",
    "declared_services_for_lib_path",
    "path_prefixes",
    "service_ownership",
    "slug_for_service_path",
]

