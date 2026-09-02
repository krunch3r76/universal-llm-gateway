"""Workflow registry — authority: ``route_policy.yaml`` ``workflows`` / ``models`` / ``contract_effort``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from contract_vocab import CANONICAL_CONTRACTS
from cursor_capabilities import CURSOR_MODEL_CAPABILITIES, canonical_cursor_bare_id
from effort_vocabulary import WIRE_LADDER
from model_id import ModelId

from implement_admission.routing import default_policy_path, load_route_policy

_ULG_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_AGENTS_PATH = _ULG_ROOT / "config" / "agents.yaml"
_CONSULT_ROUTING_SKILL_SOT = (
    _ULG_ROOT / "cursor-plugins/ulg-ecosystem/skills/consult-routing/SKILL.md"
)

AUTO_OMIT_CONTRACTS: frozenset[str] = frozenset(CANONICAL_CONTRACTS) | {"light-bounded"}
MECHANICAL_WORKFLOW = "mechanical_implement"
CHECK_REVIEW_WORKFLOW = "check_review"
AUTO_JUDGMENT_WORKFLOW = "auto_judgment"
_CURSOR_SDK_SEAT_KEY = "cursor/sdk"


@dataclass(frozen=True, slots=True)
class WorkflowBinding:
    slug: str
    seat: str
    model: str
    contracts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    bare_id: str
    roaming: bool = False
    deprecated: bool = False
    allowed_workflows: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowRegistry:
    """Parsed workflow registry with contract and roaming lookups."""

    workflows: Mapping[str, WorkflowBinding]
    models: Mapping[str, ModelPolicy]
    contract_effort: Mapping[str, str]

    def workflow_for_contract(self, contract: str) -> WorkflowBinding | None:
        """Return the workflow that claims *contract*, or ``None``."""
        key = (contract or "").strip().lower()
        for binding in self.workflows.values():
            if key in binding.contracts:
                return binding
        return None

    def roaming_bare_models(self) -> frozenset[str]:
        """Bare ids with ``roaming: true`` in the ``models`` block."""
        return frozenset(
            bare_id for bare_id, policy in self.models.items() if policy.roaming
        )

    def default_effort_for_contract(self, contract: str) -> str:
        """Omit/auto effort default for *contract*; fallback ``medium``."""
        key = (contract or "").strip().lower()
        return self.contract_effort.get(key, "medium")


def _valid_models_block_key(bare_id: str) -> bool:
    return bare_id in CURSOR_MODEL_CAPABILITIES or bare_id == "composer-2.5-fast"


def _valid_seats(policy: dict[str, Any]) -> frozenset[str]:
    seats: set[str] = set()
    default_seat = policy.get("default_seat")
    if isinstance(default_seat, str) and default_seat.strip():
        seats.add(default_seat.strip())
    routes = policy.get("routes") or {}
    if isinstance(routes, dict):
        for entries in routes.values():
            if isinstance(entries, dict):
                seats.update(str(k) for k in entries)
    return frozenset(seats)


def registry_errors(policy: dict[str, Any]) -> list[str]:
    """Collect R1–R8 validation errors; never raises."""
    errors: list[str] = []
    valid_seats = _valid_seats(policy)

    raw_workflows = policy.get("workflows")
    if not isinstance(raw_workflows, dict) or not raw_workflows:
        errors.append("workflows must be a non-empty mapping")
        return errors

    workflow_keys: set[str] = set()
    contract_claims: dict[str, str] = {}

    for slug, entry in raw_workflows.items():
        if not isinstance(slug, str) or not slug.strip():
            errors.append("workflows keys must be non-empty strings")
            continue
        workflow_keys.add(slug)
        if not isinstance(entry, dict):
            errors.append(f"workflows.{slug} must be a mapping")
            continue

        seat = entry.get("seat")
        model = entry.get("model")
        contracts_raw = entry.get("contracts")

        if not isinstance(seat, str) or not seat.strip():
            errors.append(f"workflows.{slug}.seat must be a non-empty string")
            continue
        if seat.strip() not in valid_seats:
            errors.append(
                f"workflows.{slug}.seat={seat!r} not in policy seats {sorted(valid_seats)!r}"
            )

        if not isinstance(model, str) or not model.strip():
            errors.append(f"workflows.{slug}.model must be a non-empty string")
            continue

        try:
            parsed = ModelId.parse(model.strip())
        except ValueError as exc:
            errors.append(f"workflows.{slug}.model={model!r} invalid: {exc}")
            continue

        if seat.strip() == "cursor-sdk" and parsed.backend_type == "cursor_sdk":
            try:
                bare = canonical_cursor_bare_id(model.strip())
            except ValueError as exc:
                errors.append(
                    f"workflows.{slug}.model={model!r} bare id invalid: {exc}"
                )
                bare = None
            if bare is not None and bare not in CURSOR_MODEL_CAPABILITIES:
                errors.append(
                    f"workflows.{slug}.model bare id {bare!r} not in CURSOR_MODEL_CAPABILITIES"
                )
        elif seat.strip() == "cursor-sdk":
            errors.append(
                f"workflows.{slug}.model={model!r} must be cursor_sdk when seat=cursor-sdk"
            )

        if not isinstance(contracts_raw, list):
            errors.append(f"workflows.{slug}.contracts must be a list")
            contracts: tuple[str, ...] = ()
        else:
            contract_items: list[str] = []
            for item in contracts_raw:
                if not isinstance(item, str) or not item.strip():
                    errors.append(
                        f"workflows.{slug}.contracts entries must be non-empty strings"
                    )
                    continue
                contract_items.append(item.strip().lower())
            contracts = tuple(contract_items)
            for contract in contracts:
                if contract not in AUTO_OMIT_CONTRACTS:
                    errors.append(
                        f"workflows.{slug}.contracts contains unknown contract {contract!r}"
                    )
                prior = contract_claims.get(contract)
                if prior is not None:
                    errors.append(
                        f"contract {contract!r} claimed by both workflows.{prior} "
                        f"and workflows.{slug}"
                    )
                else:
                    contract_claims[contract] = slug

    for contract in sorted(AUTO_OMIT_CONTRACTS):
        if contract not in contract_claims:
            errors.append(f"contract {contract!r} is not claimed by any workflow")

    raw_models = policy.get("models")
    parsed_models: dict[str, ModelPolicy] = {}
    if raw_models is not None:
        if not isinstance(raw_models, dict):
            errors.append("models must be a mapping when present")
        else:
            for bare_id, model_entry in raw_models.items():
                if not isinstance(bare_id, str) or not bare_id.strip():
                    errors.append("models keys must be non-empty strings")
                    continue
                bare = bare_id.strip()
                if not _valid_models_block_key(bare):
                    errors.append(
                        f"models.{bare} not a known cursor capability or wire id"
                    )
                if not isinstance(model_entry, dict):
                    errors.append(f"models.{bare} must be a mapping")
                    continue

                roaming = model_entry.get("roaming", False)
                deprecated = model_entry.get("deprecated", False)
                allowed_raw = model_entry.get("allowed_workflows")

                if not isinstance(roaming, bool):
                    errors.append(f"models.{bare}.roaming must be a bool")
                    roaming = False
                if not isinstance(deprecated, bool):
                    errors.append(f"models.{bare}.deprecated must be a bool")
                    deprecated = False

                allowed: tuple[str, ...] = ()
                if allowed_raw is not None:
                    if not isinstance(allowed_raw, list):
                        errors.append(f"models.{bare}.allowed_workflows must be a list")
                    else:
                        allowed_items: list[str] = []
                        for wf in allowed_raw:
                            if not isinstance(wf, str) or not wf.strip():
                                errors.append(
                                    f"models.{bare}.allowed_workflows entries must be strings"
                                )
                                continue
                            wf_slug = wf.strip()
                            if wf_slug not in workflow_keys:
                                errors.append(
                                    f"models.{bare}.allowed_workflows references unknown "
                                    f"workflow {wf_slug!r}"
                                )
                            allowed_items.append(wf_slug)
                        allowed = tuple(allowed_items)

                parsed_models[bare] = ModelPolicy(
                    bare_id=bare,
                    roaming=roaming,
                    deprecated=deprecated,
                    allowed_workflows=allowed,
                )

    for slug, entry in raw_workflows.items():
        if not isinstance(entry, dict):
            continue
        model = entry.get("model")
        if not isinstance(model, str):
            continue
        try:
            bare = canonical_cursor_bare_id(model.strip())
        except ValueError:
            continue
        model_policy = parsed_models.get(bare)
        if model_policy is not None and model_policy.deprecated:
            if slug not in model_policy.allowed_workflows:
                errors.append(
                    f"workflows.{slug}.model uses deprecated bare id {bare!r} "
                    f"but {slug!r} not in models.{bare}.allowed_workflows"
                )

    if MECHANICAL_WORKFLOW not in workflow_keys:
        errors.append(f"workflows must include {MECHANICAL_WORKFLOW!r} slot")
    if CHECK_REVIEW_WORKFLOW not in workflow_keys:
        errors.append(f"workflows must include {CHECK_REVIEW_WORKFLOW!r} slot")

    valid_effort = frozenset(WIRE_LADDER)
    raw_effort = policy.get("contract_effort")
    if not isinstance(raw_effort, dict) or not raw_effort:
        errors.append("contract_effort must be a non-empty mapping")
    else:
        seen_effort: set[str] = set()
        for contract, effort in raw_effort.items():
            if not isinstance(contract, str) or not contract.strip():
                errors.append("contract_effort keys must be non-empty strings")
                continue
            ckey = contract.strip().lower()
            if ckey not in CANONICAL_CONTRACTS:
                errors.append(f"contract_effort.{ckey!r} is not a canonical contract")
            if not isinstance(effort, str) or not effort.strip():
                errors.append(f"contract_effort.{ckey} must be a non-empty string")
                continue
            erung = effort.strip().lower()
            if erung not in valid_effort:
                errors.append(
                    f"contract_effort.{ckey}={effort!r} not in {sorted(valid_effort)!r}"
                )
            seen_effort.add(ckey)
        for contract in CANONICAL_CONTRACTS:
            if contract not in seen_effort:
                errors.append(f"contract {contract!r} missing from contract_effort")

    return errors


def parse_workflow_registry(policy: dict[str, Any]) -> WorkflowRegistry:
    """Parse *policy* into a registry; raise ``ValueError`` when invalid."""
    errors = registry_errors(policy)
    if errors:
        raise ValueError("\n".join(errors))

    raw_workflows = policy["workflows"]
    workflows: dict[str, WorkflowBinding] = {}
    for slug, entry in raw_workflows.items():
        contracts_raw = entry.get("contracts") or []
        contracts = tuple(str(c).strip().lower() for c in contracts_raw)
        workflows[str(slug)] = WorkflowBinding(
            slug=str(slug),
            seat=str(entry["seat"]).strip(),
            model=str(entry["model"]).strip(),
            contracts=contracts,
        )

    raw_models = policy.get("models") or {}
    models: dict[str, ModelPolicy] = {}
    if isinstance(raw_models, dict):
        for bare_id, model_entry in raw_models.items():
            if not isinstance(model_entry, dict):
                continue
            allowed_raw = model_entry.get("allowed_workflows") or ()
            allowed = tuple(str(w).strip() for w in allowed_raw)
            models[str(bare_id).strip()] = ModelPolicy(
                bare_id=str(bare_id).strip(),
                roaming=bool(model_entry.get("roaming", False)),
                deprecated=bool(model_entry.get("deprecated", False)),
                allowed_workflows=allowed,
            )

    raw_effort = policy.get("contract_effort") or {}
    contract_effort: dict[str, str] = {}
    if isinstance(raw_effort, dict):
        for contract, effort in raw_effort.items():
            if isinstance(contract, str) and isinstance(effort, str):
                contract_effort[contract.strip().lower()] = effort.strip().lower()

    return WorkflowRegistry(
        workflows=workflows,
        models=models,
        contract_effort=contract_effort,
    )


@lru_cache(maxsize=4)
def load_workflow_registry(path: Path | None = None) -> WorkflowRegistry:
    """Load and parse the workflow registry from disk (process-cached)."""
    policy_path = path or default_policy_path()
    return parse_workflow_registry(load_route_policy(policy_path))


def default_agents_path() -> Path:
    """Return the repo ``config/agents.yaml`` path."""
    return _DEFAULT_AGENTS_PATH


def verify_seat_default_parity(
    *,
    policy: dict[str, Any] | None = None,
    agents_path: Path | None = None,
) -> list[str]:
    """R9 — ``agents.yaml`` ``cursor/sdk.default_model`` == ``workflows.auto_judgment.model``."""
    errors: list[str] = []
    loaded = policy if policy is not None else load_route_policy()
    workflows = loaded.get("workflows") or {}
    auto = workflows.get(AUTO_JUDGMENT_WORKFLOW)
    if not isinstance(auto, dict):
        errors.append(
            "R9: workflows.auto_judgment missing — cannot verify seat default parity"
        )
        return errors
    expected_raw = auto.get("model")
    if not isinstance(expected_raw, str) or not expected_raw.strip():
        errors.append("R9: workflows.auto_judgment.model must be a non-empty string")
        return errors
    expected = expected_raw.strip()

    path = agents_path or default_agents_path()
    if not path.is_file():
        errors.append(f"R9: agents.yaml not found at {path}")
        return errors
    with path.open(encoding="utf-8") as fh:
        agents = yaml.safe_load(fh) or {}
    profiles = agents.get("profiles") or {}
    seat_entry = profiles.get(_CURSOR_SDK_SEAT_KEY)
    if not isinstance(seat_entry, dict):
        errors.append(f"R9: agents.yaml profiles.{_CURSOR_SDK_SEAT_KEY} missing")
        return errors
    seat_default = seat_entry.get("default_model")
    if not isinstance(seat_default, str) or not seat_default.strip():
        errors.append(
            f"R9: agents.yaml profiles.{_CURSOR_SDK_SEAT_KEY}.default_model missing"
        )
        return errors
    if seat_default.strip() != expected:
        errors.append(
            f"R9: agents.yaml profiles.{_CURSOR_SDK_SEAT_KEY}.default_model="
            f"{seat_default.strip()!r} != workflows.auto_judgment.model={expected!r}"
        )
    return errors


def verify_workflow_registry_conformance(
    *, policy_path: Path | None = None
) -> list[str]:
    """Conformance gate — returns error strings (empty when live file is valid)."""
    path = policy_path or default_policy_path()
    policy = load_route_policy(path)
    errors = registry_errors(policy)
    errors.extend(verify_seat_default_parity(policy=policy))
    return errors


def assert_workflow_registry_boot_conformance(
    *, policy_path: Path | None = None
) -> None:
    """Deploy-time refusal — raise when the live registry or R9 parity is invalid."""
    errors = verify_workflow_registry_conformance(policy_path=policy_path)
    if errors:
        raise ValueError(
            "workflow registry boot conformance failed:\n" + "\n".join(errors)
        )


_WORKFLOW_REGISTRY_MARKER_START = "<!-- workflow-registry:v1:start -->"
_WORKFLOW_REGISTRY_MARKER_END = "<!-- workflow-registry:v1:end -->"
_SKILL_EMBED_ANCHOR = "## cursor-sdk model name surfaces"


def render_workflow_registry_block(policy: dict[str, Any] | None = None) -> str:
    """Render the workflow-registry table block from ``route_policy.yaml``."""
    loaded = policy or load_route_policy()
    lines = [
        _WORKFLOW_REGISTRY_MARKER_START,
        "### Workflow registry (generated from config/routing/route_policy.yaml)",
        "",
        f"- **policy_version:** `{loaded.get('policy_version', '')}`",
        "",
        "**Stargate seat default:** `agents.yaml` `cursor/sdk.default_model` is the",
        "Stargate omit-model authority for `team_dispatch(seat=cursor-sdk)`; it must",
        "match `workflows.auto_judgment.model` below (documented invariant — not folded).",
        "",
        "| workflow | seat | model | contracts |",
        "|---|---|---|---|",
    ]
    workflows = loaded.get("workflows") or {}
    for slug, entry in sorted(workflows.items()):
        if not isinstance(entry, dict):
            continue
        contracts = entry.get("contracts") or []
        contract_cell = ", ".join(str(c) for c in contracts) if contracts else "—"
        lines.append(
            f"| {slug} | {entry.get('seat', '')} | {entry.get('model', '')} | "
            f"{contract_cell} |"
        )
    models = loaded.get("models") or {}
    roaming = [
        bare_id
        for bare_id, entry in sorted(models.items())
        if isinstance(entry, dict) and entry.get("roaming")
    ]
    if roaming:
        lines.extend(
            ["", "**Roaming bare ids:** " + ", ".join(f"`{b}`" for b in roaming)]
        )
    effort = loaded.get("contract_effort") or {}
    if isinstance(effort, dict) and effort:
        lines.extend(
            [
                "",
                "**contract_effort** (omit/auto defaults; contract-keyed, not per-workflow):",
                "",
                "| contract | effort |",
                "|---|---|",
            ]
        )
        for contract, erung in sorted(effort.items()):
            lines.append(f"| {contract} | {erung} |")
    lines.append(_WORKFLOW_REGISTRY_MARKER_END)
    return "\n".join(lines)


def verify_workflow_registry_drift(
    skill_path: Path,
    *,
    policy_path: Path | None = None,
) -> bool:
    """Return True when the skill embed matches the machine-readable registry."""
    expected = render_workflow_registry_block(load_route_policy(policy_path))
    text = skill_path.read_text(encoding="utf-8")
    if expected in text:
        return True
    start = text.find(_WORKFLOW_REGISTRY_MARKER_START)
    end = text.find(_WORKFLOW_REGISTRY_MARKER_END)
    if start == -1 or end == -1:
        return False
    embedded = text[start : end + len(_WORKFLOW_REGISTRY_MARKER_END)]
    return embedded.strip() == expected.strip()


def embed_workflow_registry_block(
    text: str, policy: dict[str, Any] | None = None
) -> str:
    """Replace or insert the generated workflow-registry block in skill markdown."""
    block = render_workflow_registry_block(policy)
    start = text.find(_WORKFLOW_REGISTRY_MARKER_START)
    end = text.find(_WORKFLOW_REGISTRY_MARKER_END)
    if start != -1 and end != -1:
        return text[:start] + block + text[end + len(_WORKFLOW_REGISTRY_MARKER_END) :]
    anchor = text.find(_SKILL_EMBED_ANCHOR)
    if anchor == -1:
        msg = f"skill body missing anchor {_SKILL_EMBED_ANCHOR!r}"
        raise ValueError(msg)
    next_heading = text.find("\n## ", anchor + len(_SKILL_EMBED_ANCHOR))
    if next_heading == -1:
        return text.rstrip() + "\n\n" + block + "\n"
    return text[:next_heading].rstrip() + "\n\n" + block + "\n" + text[next_heading:]
