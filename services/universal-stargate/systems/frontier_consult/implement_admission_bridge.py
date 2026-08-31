"""Bridge normalize/materialize into team handoff admission (Phase 2)."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from admission_common.tree_probe import probe_working_tree
from implement_admission.conductor_materialize import materialize_conductor
from implement_admission.drift_gates import (
    DriftGateState,
    check_packet_hash_drift,
    check_review_attestation,
    gate_state,
    review_attestation_findings,
)
from implement_admission.materialize import materialize
from implement_admission.normalize import normalize
from implement_admission.preflight import (
    admission_route_contract_payload,
    run_route_preflight,
)
from implement_admission.source_ref import (
    MATERIALIZE_KIND_CONDUCTOR,
    parse_source_ref,
    resolve_materialize_kind,
)
from implement_admission.spec import ReadinessState, SourceKind, implement_spec_hash

from .admission import FrontierEndpointError
from .handoff import _resolve_packet_file, _workspaces_root
from .stargate_cortex_reader import StargateCortexReader

_ULG_REPO_DIRNAME = "universal-llm-gateway"
_FRONTMATTER_HASH = re.compile(r"^implement_spec_hash:\s*(\S+)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class VerifyHashResult:
    implement_spec_hash: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BridgeResult:
    gated: bool
    gated_reason: str | None = None
    source_ref: str | None = None
    packet_path: str | None = None
    implement_spec_hash: str | None = None
    packet_sha256: str | None = None
    materialization_present: bool | None = None
    warnings: list[str] = field(default_factory=list)
    route_contract: dict[str, Any] | None = None


def probe_packet_presence(
    packet_path: str,
    *,
    workspaces_root: Path,
    probe_root: Path | None = None,
) -> bool:
    """Return True when packet_path resolves to a file under probe_root."""
    root = probe_root or workspaces_root
    return _resolve_packet_file(root, packet_path) is not None


def _executor_probe_root(workspaces_root: Path) -> Path:
    """Resolve executor visibility root; defaults to Stargate workspaces_root."""
    raw = os.environ.get("HANDOFF_EXECUTOR_WORKSPACES_ROOT", "").strip()
    if not raw:
        return workspaces_root
    return Path(raw).resolve()


def _repo_base(workspaces_root: Path) -> Path:
    root = workspaces_root.resolve()
    if root.name == _ULG_REPO_DIRNAME:
        return root
    nested = root / _ULG_REPO_DIRNAME
    return nested if nested.is_dir() else root


def _resolve_dirty_tree_risk(
    *,
    enable_dirty_tree_risk: bool,
    cwd: str | None,
) -> bool:
    if not enable_dirty_tree_risk or not cwd:
        return False
    _, dirty = probe_working_tree(cwd)
    return dirty


def _materialized_out_dir(workspaces_root: Path) -> Path:
    return _repo_base(workspaces_root) / "tmp/implement-admission/materialized"


def _path_relative_to_workspaces(full_path: Path, workspaces_root: Path) -> str:
    root = workspaces_root.resolve()
    resolved = full_path.resolve()
    repo = _repo_base(root)
    try:
        rel = resolved.relative_to(repo.resolve()).as_posix()
    except ValueError:
        rel = resolved.relative_to(root).as_posix()
    if repo != root and repo.name == _ULG_REPO_DIRNAME:
        return f"{_ULG_REPO_DIRNAME}/{rel}"
    if repo != root:
        return f"{repo.name}/{rel}"
    return rel


def _read_frontmatter_implement_spec_hash(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    match = _FRONTMATTER_HASH.search(text[3:end])
    return match.group(1) if match else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _is_packet_lane(source_ref: str) -> bool:
    return parse_source_ref(source_ref).source_kind == SourceKind.PACKET.value


def _headless_vs_human(author_family: str | None) -> Literal["headless", "human"]:
    """Automated dispatch (no seat) vs operator/agent-mediated admission."""
    if author_family is None or author_family.strip() in {"", "dispatch"}:
        return "headless"
    return "human"


def _enforce_gate_ra_or_warn(
    *,
    request_id: str,
    spec: Any,
    headless_vs_human: Literal["headless", "human"],
) -> list[str]:
    gate_ra = check_review_attestation(spec, headless_vs_human=headless_vs_human)
    if gate_ra.action == "reject":
        findings = review_attestation_findings(spec)
        failing_codes = [f.code.value for f in findings if f.rejectable_under_enforce]
        remediation = (
            "attach a non-claude pass/pass_with_conditions review "
            "bound to current implement_spec_hash"
        )
        raise FrontierEndpointError(
            request_id=request_id,
            field="source_ref",
            reason=f"{', '.join(failing_codes)}: {remediation}",
            status_code=422,
            code="handoff_review_attestation_blocked",
        )
    if gate_state("ra") == DriftGateState.OFF:
        return []
    return [f.message for f in review_attestation_findings(spec)]


def _attestation_warnings_for_spec(
    spec: Any,
    *,
    request_id: str | None,
    headless_vs_human: Literal["headless", "human"],
) -> list[str]:
    if request_id is not None:
        return _enforce_gate_ra_or_warn(
            request_id=request_id,
            spec=spec,
            headless_vs_human=headless_vs_human,
        )
    check_review_attestation(spec, headless_vs_human=headless_vs_human)
    if gate_state("ra") == DriftGateState.OFF:
        return []
    return [f.message for f in review_attestation_findings(spec)]


def _enforce_gate_b_or_warn(
    *,
    request_id: str,
    spec: Any,
    packet_sha256: str | None,
) -> None:
    gate_b = check_packet_hash_drift(spec, on_disk_sha256=packet_sha256)
    if gate_b.action != "reject":
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="packet_path",
        reason=(
            "packet hash drift detected: stored "
            f"{gate_b.stored!r} vs recomputed {gate_b.recomputed!r}"
        ),
        status_code=422,
        code="handoff_packet_hash_drift",
    )


def verify_both_present_hash(
    *,
    request_id: str,
    source_ref: str,
    packet_path: str,
    cortex: StargateCortexReader,
    workspaces_root: Path | None = None,
    enable_dirty_tree_risk: bool = False,
    cwd: str | None = None,
    author_family: str | None = None,
) -> VerifyHashResult:
    """Compare frontmatter implement_spec_hash to normalize(source_ref); return hash.

    Stamp-on-admit: an **absent** frontmatter ``implement_spec_hash`` is trusted
    and stamped server-side (the server recomputes ``expected`` from
    ``normalize(source_ref)`` here regardless — it is the hash authority). A
    non-shell authoring seat cannot run ``normalize()`` to precompute the stamp,
    so requiring it would force a downgrade to the consult wire. The 422 is
    reserved for a genuine *mismatch*: a present hash that contradicts the
    normalized spec.
    """
    root = (workspaces_root or _workspaces_root()).resolve()
    candidate = _resolve_packet_file(root, packet_path)
    if candidate is None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=f"Packet file not found at workspaces path {packet_path!r}",
            status_code=422,
            code="implement_spec_hash_mismatch",
        )

    frontmatter_hash = _read_frontmatter_implement_spec_hash(
        candidate.read_text(encoding="utf-8", errors="replace")
    )
    dirty_tree_risk = _resolve_dirty_tree_risk(
        enable_dirty_tree_risk=enable_dirty_tree_risk,
        cwd=cwd or str(_repo_base(root)),
    )
    spec = normalize(
        source_ref,
        cortex=cortex,
        workspaces_root=root,
        dirty_tree_risk=dirty_tree_risk,
        author_family=author_family,
    )
    expected = spec.provenance.implement_spec_hash or implement_spec_hash(spec)
    hvh = _headless_vs_human(author_family)
    attestation_warnings = _attestation_warnings_for_spec(
        spec,
        request_id=request_id,
        headless_vs_human=hvh,
    )

    if frontmatter_hash is not None and frontmatter_hash != expected:
        raise FrontierEndpointError(
            request_id=request_id,
            field="source_ref",
            reason=(
                "source_ref and packet_path both present but implement_spec_hash "
                f"frontmatter ({frontmatter_hash!r}) does not match normalized spec "
                f"({expected!r})"
            ),
            status_code=422,
            code="implement_spec_hash_mismatch",
        )
    if not _is_packet_lane(source_ref):
        _enforce_gate_b_or_warn(
            request_id=request_id,
            spec=spec,
            packet_sha256=_sha256_file(candidate),
        )
    return VerifyHashResult(
        implement_spec_hash=expected,
        warnings=attestation_warnings,
    )


def resolve_source_ref_to_packet(
    source_ref: str,
    *,
    cortex: StargateCortexReader,
    workspaces_root: Path | None = None,
    enable_dirty_tree_risk: bool = False,
    cwd: str | None = None,
    request_id: str | None = None,
    author_family: str | None = None,
    contract: str | None = None,
    role: str | None = None,
    seat: str | None = None,
    transport: str = "team_dispatch",
    operator_pickup_required: bool | None = None,
    autonomy: str | None = None,
    packet_text: str | None = None,
    packet_kind: str | None = None,
    caller_agent: str | None = None,
    summon_text: str | None = None,
    summon_mode: str | None = None,
    summoning_thread_id: str | None = None,
    summoning_turn_count: int | None = None,
) -> BridgeResult:
    """Normalize + materialize source_ref into a workspaces-relative packet path."""
    root = (workspaces_root or _workspaces_root()).resolve()
    materialize_kind = resolve_materialize_kind(packet_kind=packet_kind)
    if materialize_kind == MATERIALIZE_KIND_CONDUCTOR:
        from implement_admission.conductor_witness_defaults import fold_deps_for_admit

        out_dir = _materialized_out_dir(root)
        repo = _repo_base(root)
        mp = materialize_conductor(
            source_ref,
            cortex=cortex,
            out_dir=out_dir,
            caller_agent=caller_agent,
            summon_text=summon_text,
            summon_mode=summon_mode,
            summoning_turn_count=summoning_turn_count,
            fold_deps=fold_deps_for_admit(
                source_ref,
                cortex=cortex,
                repo=repo,
                summon_mode=summon_mode,
                summoning_thread_id=summoning_thread_id,
            ),
            summoning_thread_id=summoning_thread_id,
        )
        rel_path = _path_relative_to_workspaces(Path(mp.path), root)
        probe_root = _executor_probe_root(root)
        present = probe_packet_presence(
            rel_path, workspaces_root=root, probe_root=probe_root
        )
        warnings: list[str] = []
        if not present:
            warnings.append(
                "materialization.executor_absent: "
                f"{rel_path} not visible at executor root {probe_root}; "
                "use source_ref fallback"
            )
        return BridgeResult(
            gated=False,
            source_ref=source_ref,
            packet_path=rel_path,
            implement_spec_hash=None,
            packet_sha256=mp.packet_sha256,
            materialization_present=present,
            warnings=warnings,
            route_contract={"packet_kind": "conductor", "lane": "B"},
        )
    dirty_tree_risk = _resolve_dirty_tree_risk(
        enable_dirty_tree_risk=enable_dirty_tree_risk,
        cwd=cwd or str(_repo_base(root)),
    )
    spec = normalize(
        source_ref,
        cortex=cortex,
        workspaces_root=root,
        dirty_tree_risk=dirty_tree_risk,
        author_family=author_family,
        contract=contract,
        role=role,
        seat=seat,
        transport=transport,
    )

    if spec.readiness.state == ReadinessState.GATED:
        return BridgeResult(
            gated=True,
            gated_reason=spec.readiness.gated_reason,
            source_ref=source_ref,
        )

    route_warnings = run_route_preflight(
        spec,
        operator_pickup_required=operator_pickup_required,
        autonomy=autonomy,
        transport=transport,
        packet_text=packet_text,
    )
    route_payload = admission_route_contract_payload(spec)
    route_contract = route_payload.get("route_contract")

    spec_hash = spec.provenance.implement_spec_hash or implement_spec_hash(spec)
    hvh = _headless_vs_human(author_family)
    attestation_warnings = [
        *_attestation_warnings_for_spec(
            spec,
            request_id=request_id,
            headless_vs_human=hvh,
        ),
        *route_warnings,
    ]

    if _is_packet_lane(source_ref):
        packet_path_part = source_ref.split(":", 1)[1]
        candidate = _resolve_packet_file(root, packet_path_part)
        if candidate is None:
            raise FrontierEndpointError(
                request_id=request_id or "",
                field="source_ref",
                reason=f"Packet file not found for {source_ref!r}",
                status_code=422,
                code="handoff_packet_missing",
            )
        rel_path = _path_relative_to_workspaces(candidate, root)
        return BridgeResult(
            gated=False,
            source_ref=source_ref,
            packet_path=rel_path,
            implement_spec_hash=spec_hash,
            packet_sha256=spec.source.source_version.packet_sha256,
            warnings=attestation_warnings,
            route_contract=route_contract,
        )

    out_dir = _materialized_out_dir(root)
    mp = materialize(spec, out_dir=out_dir)
    rel_path = _path_relative_to_workspaces(Path(mp.path), root)
    if request_id is not None:
        _enforce_gate_b_or_warn(
            request_id=request_id,
            spec=spec,
            packet_sha256=mp.packet_sha256,
        )
    else:
        check_packet_hash_drift(spec, on_disk_sha256=mp.packet_sha256)

    probe_root = _executor_probe_root(root)
    present = probe_packet_presence(
        rel_path, workspaces_root=root, probe_root=probe_root
    )
    bridge_warnings: list[str] = list(attestation_warnings)
    if not present:
        bridge_warnings.append(
            "materialization.executor_absent: "
            f"{rel_path} not visible at executor root {probe_root}; "
            "use source_ref fallback"
        )
    return BridgeResult(
        gated=False,
        source_ref=source_ref,
        packet_path=rel_path,
        implement_spec_hash=spec_hash,
        packet_sha256=mp.packet_sha256,
        materialization_present=present,
        warnings=bridge_warnings,
        route_contract=route_contract,
    )
