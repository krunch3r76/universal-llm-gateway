"""Two-axis routing derivation — orchestration_mode × executor_style."""

from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from agent_seat import seat_to_family

from implement_admission.spec import (
    ExecutorStyle,
    ImplementSpec,
    OrchestrationMode,
    RouteContract,
    Routing,
    RoutingDerivation,
    SourceKind,
)

_POLICY_MARKER_START = "<!-- route-policy:v1:start -->"
_POLICY_MARKER_END = "<!-- route-policy:v1:end -->"
_ULG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_POLICY_PATH = _ULG_ROOT / "config/routing/route_policy.yaml"

_RISK_TIER_RANK = {"mechanical": 0, "material": 1, "critical": 2}

_PATH_GLOBS = (
    "migrations/**",
    "**/migrations/**",
    "**/migration_*.py",
    "**/*.sql",
    "libs/cortex_store/**",
    "libs/agent_bus_store/**",
    "libs/event_store/**",
    "services/*store*/**",
    "libs/implement_admission/**",
    "services/universal-stargate/systems/frontier_consult/**",
    "config/mcp/canonical.yaml",
)

_MATERIAL_TOKENS = (
    "schema migration",
    "migration",
    "entity_rekey",
    "entity_merge",
    "rekey",
    "merge",
    "tombstone",
    "resolver",
    "alias",
    "foreign key",
    "foreign_key",
    "unique index",
    "partial index",
    "dedup",
    "repoint",
    "transaction",
    "atomicity",
    "defer_foreign_keys",
    "dispatch",
    "admission",
    "handoff",
    "executor",
    "pipeline",
)

_CRITICAL_TOKENS = (
    "irreversible",
    "data-destructive",
    "drop table",
    "money",
    "funds",
    "payment",
    "order routing",
    "auth",
    "security",
    "credential",
    "legal",
    "deadline",
)


def normalize_author_family(seat_or_family: str | None) -> str:
    """Map seat slug to canonical family; unknown → claude (conservative)."""
    if not seat_or_family:
        return "claude"
    mapped = seat_to_family(seat_or_family)
    if mapped in {"claude", "gpt", "grok", "gemini"}:
        return mapped
    if seat_or_family in {"claude", "gpt", "grok", "gemini"}:
        return seat_or_family
    return "claude"


def _spec_haystack(spec: ImplementSpec) -> str:
    parts: list[str] = [
        spec.intent.summary,
        spec.intent.description or "",
        spec.source.source_ref,
        *spec.skills,
        *spec.acceptance.criteria,
    ]
    if spec.scope.deck_body:
        parts.append(spec.scope.deck_body)
    return " ".join(parts).lower()


def _token_match(haystack: str, token: str) -> bool:
    if " " in token:
        return token in haystack
    return re.search(rf"\b{re.escape(token)}\b", haystack) is not None


def _path_matches_material(path: str) -> bool:
    lowered = path.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in _PATH_GLOBS)


def _material_signal(spec: ImplementSpec) -> bool:
    if any(_path_matches_material(p) for p in spec.scope.files_expected):
        return True
    haystack = _spec_haystack(spec)
    return any(_token_match(haystack, token) for token in _MATERIAL_TOKENS)


def _critical_signal(spec: ImplementSpec) -> bool:
    haystack = _spec_haystack(spec)
    return any(_token_match(haystack, token) for token in _CRITICAL_TOKENS)


def classify_risk_tier(
    spec: ImplementSpec,
) -> Literal["mechanical", "material", "critical"]:
    """Deterministic risk classifier — critical first, then material, else mechanical."""
    if _critical_signal(spec):
        return "critical"
    if _material_signal(spec):
        return "material"
    return "mechanical"


def risk_tier_rank(tier: str) -> int:
    return _RISK_TIER_RANK.get(tier, 0)


def derive_orchestration_mode(
    source_kind: str,
    *,
    multi_phase: bool = False,
    trips_todo_plan_threshold: bool = False,
    packet_shape: str = "single",
    ambiguous_bus: bool = False,
) -> str | None:
    """Axis 1 — return mode string or None when route must gate."""
    if source_kind == SourceKind.AGENT_BUS.value and ambiguous_bus:
        return None
    if source_kind == SourceKind.PLAN.value and multi_phase:
        return OrchestrationMode.COORDINATOR.value
    if source_kind == SourceKind.TODO.value and trips_todo_plan_threshold:
        return OrchestrationMode.COORDINATOR.value
    if source_kind == SourceKind.PACKET.value and packet_shape == "multi":
        return OrchestrationMode.COORDINATOR.value
    if source_kind in {
        SourceKind.PLAN_PHASE.value,
        SourceKind.TODO.value,
        SourceKind.PACKET.value,
        SourceKind.AGENT_BUS.value,
    }:
        return OrchestrationMode.SINGLE.value
    if source_kind == SourceKind.PLAN.value:
        return OrchestrationMode.SINGLE.value
    return OrchestrationMode.SINGLE.value


def derive_executor_style(
    *,
    has_complete_file_list: bool = False,
    has_dense_acs: bool = False,
    open_design: bool = False,
    dirty_tree_risk: bool = False,
    irreversible_gate: bool = False,
) -> tuple[str, bool]:
    """Axis 2 — return (executor_style, checkpoint_required)."""
    if dirty_tree_risk or irreversible_gate or open_design:
        return ExecutorStyle.REASONING.value, True
    if has_complete_file_list and has_dense_acs and not open_design:
        return ExecutorStyle.MECHANICAL.value, False
    return ExecutorStyle.REASONING.value, False


def derive_routing(
    source_kind: str,
    *,
    multi_phase: bool = False,
    trips_todo_plan_threshold: bool = False,
    packet_shape: str = "single",
    ambiguous_bus: bool = False,
    has_complete_file_list: bool = False,
    has_dense_acs: bool = False,
    open_design: bool = False,
    dirty_tree_risk: bool = False,
    irreversible_gate: bool = False,
    requested_execution_mode: str | None = None,
) -> Routing | None:
    """Compose both axes; return None when orchestration cannot be derived."""
    mode = derive_orchestration_mode(
        source_kind,
        multi_phase=multi_phase,
        trips_todo_plan_threshold=trips_todo_plan_threshold,
        packet_shape=packet_shape,
        ambiguous_bus=ambiguous_bus,
    )
    if mode is None:
        return None

    style, checkpoint = derive_executor_style(
        has_complete_file_list=has_complete_file_list,
        has_dense_acs=has_dense_acs,
        open_design=open_design,
        dirty_tree_risk=dirty_tree_risk,
        irreversible_gate=irreversible_gate,
    )

    mode_rule = _mode_rule(
        source_kind,
        multi_phase=multi_phase,
        trips_todo_plan_threshold=trips_todo_plan_threshold,
        packet_shape=packet_shape,
        ambiguous_bus=ambiguous_bus,
        mode=mode,
    )
    style_rule = _style_rule(
        has_complete_file_list=has_complete_file_list,
        has_dense_acs=has_dense_acs,
        open_design=open_design,
        dirty_tree_risk=dirty_tree_risk,
        irreversible_gate=irreversible_gate,
        style=style,
        checkpoint=checkpoint,
    )

    return Routing(
        orchestration_mode=OrchestrationMode(mode),
        executor_style=ExecutorStyle(style),
        checkpoint_required=checkpoint,
        derivation=RoutingDerivation(mode_rule=mode_rule, style_rule=style_rule),
        requested_execution_mode=requested_execution_mode,
    )


def _mode_rule(
    source_kind: str,
    *,
    multi_phase: bool,
    trips_todo_plan_threshold: bool,
    packet_shape: str,
    ambiguous_bus: bool,
    mode: str,
) -> str:
    if ambiguous_bus:
        return "agent-bus:* ambiguous — no route (gated)"
    if trips_todo_plan_threshold:
        return "todo tripping Todo→Plan threshold → coordinator"
    if source_kind == SourceKind.PLAN.value and multi_phase:
        return "plan multi-phase arc → coordinator"
    if packet_shape == "multi":
        return "packet with phase deck / parallel groups → coordinator"
    return f"single bounded {source_kind} → {mode}"


def _style_rule(
    *,
    has_complete_file_list: bool,
    has_dense_acs: bool,
    open_design: bool,
    dirty_tree_risk: bool,
    irreversible_gate: bool,
    style: str,
    checkpoint: bool,
) -> str:
    if dirty_tree_risk:
        return "code-modifying with dirty/shared-tree risk → reasoning + checkpoint"
    if irreversible_gate:
        return "legal/financial/irreversible gate → reasoning + checkpoint"
    if open_design:
        return "sparse/architectural, open substrate choice → reasoning"
    if has_complete_file_list and has_dense_acs:
        return "complete file list + dense ACs, no open design → mechanical"
    return f"default style derivation → {style}" + (
        " + checkpoint" if checkpoint else ""
    )


def default_policy_path() -> Path:
    return _DEFAULT_POLICY_PATH


def load_route_policy(path: Path | None = None) -> dict[str, Any]:
    """Load canonical routing policy from the machine-readable artifact."""
    policy_path = path or _DEFAULT_POLICY_PATH
    with policy_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        msg = f"route policy must be a mapping: {policy_path}"
        raise ValueError(msg)
    return data


def _route_lookup_key(*, seat: str | None, role: str | None) -> str:
    if role:
        return role.strip()
    if seat:
        return seat.strip()
    return "cursor-sdk"


def resolve_route_contract(
    spec: ImplementSpec,
    routing: Routing,
    risk_tier: str,
    *,
    contract: str,
    seat: str | None = None,
    role: str | None = None,
    transport: str = "team_dispatch",
    policy: dict[str, Any] | None = None,
) -> RouteContract:
    """Map canonical policy + derived routing into a populated RouteContract."""
    del spec, routing, risk_tier  # reserved for future style/seat-aware overrides
    loaded = policy or load_route_policy()
    routes = loaded.get("routes") or {}
    contract_routes = routes.get(contract)
    if not isinstance(contract_routes, dict):
        msg = f"unknown contract {contract!r} in route policy"
        raise ValueError(msg)

    lookup = _route_lookup_key(seat=seat, role=role)
    entry = contract_routes.get(lookup)
    if entry is None and lookup.endswith("-implement"):
        entry = contract_routes.get(lookup)
    if entry is None and contract == "implement" and lookup == "cursor-sdk":
        entry = contract_routes.get("cursor-sdk")
    if not isinstance(entry, dict):
        msg = f"no route policy entry for contract={contract!r} seat/role={lookup!r}"
        raise ValueError(msg)

    canonical_transport = str(entry.get("transport") or "team_dispatch")
    if transport != canonical_transport:
        msg = (
            f"transport {transport!r} is not admitted for contract={contract!r} "
            f"(canonical {canonical_transport!r})"
        )
        raise ValueError(msg)

    autonomy = entry.get("autonomy")
    if autonomy not in {"auto_executed", "manual_pickup"}:
        msg = f"invalid autonomy in route policy: {autonomy!r}"
        raise ValueError(msg)

    return RouteContract(
        policy_source=str(loaded.get("policy_source") or "consult-routing"),
        policy_version=str(loaded.get("policy_version") or ""),
        dispatch_kind=str(entry.get("dispatch_kind") or contract),
        transport=canonical_transport,
        autonomy=autonomy,
        operator_pickup_required=bool(entry.get("operator_pickup_required")),
        lead_claim_authority=str(
            loaded.get("lead_claim_authority")
            or "server_contract_overrides_packet_prose"
        ),
    )


def with_route_contract(
    spec: ImplementSpec,
    *,
    contract: str,
    seat: str | None = None,
    role: str | None = None,
    transport: str = "team_dispatch",
    policy: dict[str, Any] | None = None,
) -> ImplementSpec:
    """Attach route_contract once routing is present."""
    if spec.routing is None:
        return spec
    risk_tier = classify_risk_tier(spec)
    route_contract = resolve_route_contract(
        spec,
        spec.routing,
        risk_tier,
        contract=contract,
        seat=seat,
        role=role,
        transport=transport,
        policy=policy,
    )
    return spec.model_copy(update={"route_contract": route_contract})


def render_consult_routing_policy_block(policy: dict[str, Any] | None = None) -> str:
    """Render the consult-routing canonical policy block from route_policy.yaml."""
    loaded = policy or load_route_policy()
    lines = [
        _POLICY_MARKER_START,
        "### Canonical routing policy (generated from config/routing/route_policy.yaml)",
        "",
        f"- **policy_source:** `{loaded.get('policy_source', 'consult-routing')}`",
        f"- **policy_version:** `{loaded.get('policy_version', '')}`",
        f"- **lead_claim_authority:** `{loaded.get('lead_claim_authority', '')}`",
        "",
        "| contract | seat/role | autonomy | operator_pickup_required |",
        "|---|---|---|---|",
    ]
    routes = loaded.get("routes") or {}
    for contract, entries in sorted(routes.items()):
        if not isinstance(entries, dict):
            continue
        for seat_role, entry in sorted(entries.items()):
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"| {contract} | {seat_role} | {entry.get('autonomy')} | "
                f"{str(entry.get('operator_pickup_required')).lower()} |"
            )
    lines.append(_POLICY_MARKER_END)
    return "\n".join(lines)


def policy_block_sha256(policy: dict[str, Any] | None = None) -> str:
    block = render_consult_routing_policy_block(policy)
    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_consult_routing_policy_drift(
    consult_routing_path: Path,
    *,
    policy_path: Path | None = None,
) -> bool:
    """Return True when consult-routing embed matches the machine-readable policy."""
    expected = render_consult_routing_policy_block(load_route_policy(policy_path))
    text = consult_routing_path.read_text(encoding="utf-8")
    if expected in text:
        return True
    start = text.find(_POLICY_MARKER_START)
    end = text.find(_POLICY_MARKER_END)
    if start == -1 or end == -1:
        return False
    embedded = text[start : end + len(_POLICY_MARKER_END)]
    return embedded.strip() == expected.strip()


def verify_role_substrate_vocab_conformance(
    *,
    policy_path: Path | None = None,
) -> list[str]:
    """Conformance gate for default_seat / platform:sdk dispatch_lane keys (AC5)."""
    policy = load_route_policy(policy_path)
    errors: list[str] = []
    if policy.get("default_seat") != "cursor-sdk":
        errors.append("default_seat must be 'cursor-sdk'")
    code_lane = (policy.get("dispatch_lane") or {}).get("code") or {}
    if code_lane.get("platform") != "sdk":
        errors.append("dispatch_lane.code.platform must be 'sdk'")
    if code_lane.get("seat") != "cursor-sdk":
        errors.append("dispatch_lane.code.seat must be 'cursor-sdk'")
    return errors


def verify_check_review_default_policy(
    *,
    policy_path: Path | None = None,
) -> list[str]:
    """Conformance gate for check_review_default_model (AC9)."""
    from implement_admission.check_review_substrate import (
        verify_check_review_default_conformance,
    )

    path = policy_path or _DEFAULT_POLICY_PATH
    policy = load_route_policy(path)
    policy_text = path.read_text(encoding="utf-8")
    return verify_check_review_default_conformance(policy, policy_text=policy_text)
