"""Bridge normalize/materialize into team handoff admission (Phase 2)."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.materialize import materialize
from implement_admission.normalize import normalize
from implement_admission.drift_gates import check_packet_hash_drift
from implement_admission.spec import ReadinessState, implement_spec_hash
from admission_common.tree_probe import probe_working_tree
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from .admission import FrontierEndpointError
from .handoff import _resolve_packet_file, _workspaces_root

_ULG_REPO_DIRNAME = "universal-llm-gateway"
_FRONTMATTER_HASH = re.compile(r"^implement_spec_hash:\s*(\S+)", re.MULTILINE)
_CORTEX_TIMEOUT = 15.0


@dataclass(frozen=True, slots=True)
class BridgeResult:
    gated: bool
    gated_reason: str | None = None
    source_ref: str | None = None
    packet_path: str | None = None
    implement_spec_hash: str | None = None
    packet_sha256: str | None = None


class StargateCortexReader:
    """Thin sync HTTP relay to cortex-api for implement_admission readers."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        path = f"/entities/{urllib.parse.quote(entity_id, safe=':')}"
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
            resp = client.get(path)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        raw_assertions = data.get("assertions") or []
        data["assertions"] = [
            {
                "superseded": bool(item.get("superseded_by")),
                "confidence": item.get("confidence"),
                **item,
            }
            for item in raw_assertions
        ]
        return data


def _repo_base(workspaces_root: Path) -> Path:
    root = workspaces_root.resolve()
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
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        repo = _repo_base(root)
        rel = resolved.relative_to(repo)
        return f"{_ULG_REPO_DIRNAME}/{rel.as_posix()}"
    return rel.as_posix()


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
) -> str:
    """Compare frontmatter implement_spec_hash to normalize(source_ref); return hash."""
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
    )
    expected = spec.provenance.implement_spec_hash or implement_spec_hash(spec)

    if not frontmatter_hash or frontmatter_hash != expected:
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
    _enforce_gate_b_or_warn(
        request_id=request_id,
        spec=spec,
        packet_sha256=_sha256_file(candidate),
    )
    return expected


def resolve_source_ref_to_packet(
    source_ref: str,
    *,
    cortex: StargateCortexReader,
    workspaces_root: Path | None = None,
    enable_dirty_tree_risk: bool = False,
    cwd: str | None = None,
    request_id: str | None = None,
) -> BridgeResult:
    """Normalize + materialize source_ref into a workspaces-relative packet path."""
    root = (workspaces_root or _workspaces_root()).resolve()
    dirty_tree_risk = _resolve_dirty_tree_risk(
        enable_dirty_tree_risk=enable_dirty_tree_risk,
        cwd=cwd or str(_repo_base(root)),
    )
    spec = normalize(
        source_ref,
        cortex=cortex,
        workspaces_root=root,
        dirty_tree_risk=dirty_tree_risk,
    )

    if spec.readiness.state == ReadinessState.GATED:
        return BridgeResult(
            gated=True,
            gated_reason=spec.readiness.gated_reason,
            source_ref=source_ref,
        )

    out_dir = _materialized_out_dir(root)
    mp = materialize(spec, out_dir=out_dir)
    rel_path = _path_relative_to_workspaces(Path(mp.path), root)
    spec_hash = spec.provenance.implement_spec_hash or implement_spec_hash(spec)
    if request_id is not None:
        _enforce_gate_b_or_warn(
            request_id=request_id,
            spec=spec,
            packet_sha256=mp.packet_sha256,
        )
    else:
        check_packet_hash_drift(spec, on_disk_sha256=mp.packet_sha256)
    return BridgeResult(
        gated=False,
        source_ref=source_ref,
        packet_path=rel_path,
        implement_spec_hash=spec_hash,
        packet_sha256=mp.packet_sha256,
    )
