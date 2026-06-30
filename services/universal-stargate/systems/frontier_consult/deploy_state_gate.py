"""Deploy-state verification gate for implement-closeout (thread 3128 class)."""

from __future__ import annotations

import fnmatch
import json
import os
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from implement_admission.source_ref import SourceRef, SourceRefError, parse_source_ref
from implement_admission.spec import SourceKind
from transport_utils import (
    DEFAULT_CORTEX_URL,
    DEFAULT_STARGATE_URL,
    MANAGE_SOCKET,
    make_sync_client,
)
from universal_logging import get_logger

from .admission import FrontierEndpointError
from .handoff import _workspaces_root
from .implement_admission_bridge import StargateCortexReader, _repo_base

logger = get_logger(__name__)

_SAFETY_MARGIN_S = 5.0
_MANAGE_TIMEOUT_S = 5.0
_HEALTH_TIMEOUT_S = 3.0

SMOKE_RECIPES: dict[str, dict[str, str]] = {
    "mcp_tool": {
        "kind": "mcp_tool_call",
        "how": "invoke the changed tool with minimal valid params; embed raw response",
    },
    "fastapi_route": {
        "kind": "http_get",
        "how": "GET the specific changed route; embed status + raw body",
    },
    "stargate_model": {
        "kind": "endpoint_probe",
        "how": "hit the endpoint using the changed model with a minimal valid request",
    },
    "mcp_canonical": {
        "kind": "registration_read",
        "how": "read back the registered surface from the canonical registry",
    },
}

_TIER_A_GLOBS: tuple[str, ...] = (
    "services/mcp-server/**",
    "services/cortex-api/**",
    "services/universal-stargate/**",
    "libs/cortex_store/**",
    "config/mcp/canonical.yaml",
    "**/routes/**",
    "**/models/**",
    "**/main.py",
    "services/**/__init__.py",
    "libs/**/__init__.py",
)

_TIER_B_GLOBS: tuple[tuple[str, str], ...] = (
    ("services/mcp-server/tools/**", "mcp_tool"),
    ("**/route.py", "fastapi_route"),
    ("**/routes/**", "fastapi_route"),
    ("services/universal-stargate/**/frontier_consult/**", "stargate_model"),
    ("**/models/**", "stargate_model"),
    ("config/mcp/**", "mcp_canonical"),
)

_PRODUCER_HEALTH_URLS: dict[str, str] = {
    "cortex_api": DEFAULT_CORTEX_URL,
    "stargate": DEFAULT_STARGATE_URL,
}


def _glob_match(path: str, pattern: str) -> bool:
    normalized = path.lstrip("/")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return normalized == prefix or normalized.startswith(f"{prefix}/")
    return fnmatch.fnmatch(normalized, pattern)


def _gate_files_from_closeout(closeout: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for key in ("files_created", "files_modified", "files_deleted"):
        raw = closeout.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    paths.add(item.strip().lstrip("/"))
    return paths


def _producers_for_file(path: str) -> set[str]:
    normalized = path.lstrip("/")
    if normalized.startswith(("services/mcp-server/", "config/mcp/")):
        return {"mcp"}
    if normalized.startswith("services/universal-stargate/"):
        return {"stargate"}
    if normalized.startswith(("libs/cortex_store/", "services/cortex-api/")):
        return {"cortex_api"}
    if normalized.startswith("services/git_integration_worker/"):
        return {"git_integration_worker"}
    if normalized.startswith(("libs/agent_bus", "services/agent_bus")):
        return {"agent_bus"}
    return set()


def _matched_producers(gate_files: set[str]) -> set[str]:
    producers: set[str] = set()
    for path in gate_files:
        if not any(_glob_match(path, glob) for glob in _TIER_A_GLOBS):
            continue
        producers.update(_producers_for_file(path))
    return producers


def _matched_surfaces(gate_files: set[str]) -> set[str]:
    surfaces: set[str] = set()
    for path in gate_files:
        for pattern, surface in _TIER_B_GLOBS:
            if _glob_match(path, pattern):
                surfaces.add(surface)
    return surfaces


def _call_manage_health(producer: str) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "method": "health",
        "params": {"service": producer},
        "id": 1,
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_MANAGE_TIMEOUT_S)
            sock.connect(MANAGE_SOCKET)
            sock.sendall(json.dumps(body).encode() + b"\n")
            data = b""
            while True:
                chunk = sock.recv(65_536)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            raw = json.loads(data.strip())
    except FileNotFoundError:
        return {"error": "manage.sock not found"}
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc)}

    if "error" in raw:
        err = raw["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"error": msg}
    result = raw.get("result", raw)
    return result if isinstance(result, dict) else {"result": result}


def _fetch_producer_health_json(producer: str) -> dict[str, Any]:
    url = _PRODUCER_HEALTH_URLS.get(producer)
    if producer == "mcp":
        url = os.environ.get("MCP_PUBLIC_URL", "").strip() or None
    if not url:
        return {}
    try:
        with make_sync_client(url, timeout=_HEALTH_TIMEOUT_S) as client:
            resp = client.get("/health")
            if resp.status_code != 200:
                return {}
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _classify_health_state(
    manage_result: dict[str, Any],
) -> tuple[str, bool, bool]:
    """Return (state, retryable, hard_fail)."""
    if "error" in manage_result:
        err = str(manage_result["error"]).lower()
        if "not found" in err or "not reachable" in err:
            return "unreachable", True, False
        return "unknown", True, False

    detail = str(manage_result.get("detail") or "").lower()
    status = str(manage_result.get("status") or "").lower()
    if "exited (1)" in detail or "exit code 1" in detail:
        return "crashed_exit_1", False, True
    if status == "running":
        return "healthy", False, False
    if status == "unhealthy" and ("starting" in detail or "not ready" in detail):
        return "starting", True, False
    if status in {"stopped", "unknown"}:
        return "unreachable", True, False
    if status == "unhealthy":
        return "starting", True, False
    return "unknown", True, False


def _parse_ts(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _git_repo_root() -> Path | None:
    root = _repo_base(_workspaces_root())
    return root if (root / ".git").is_dir() else None


def _git_commit_ts(repo: Path, commit: str) -> datetime | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "show", "-s", "--format=%cI", commit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_ts(proc.stdout.strip())


def _git_latest_landing_ts(repo: Path, paths: set[str]) -> datetime | None:
    if not paths:
        return None
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "-1",
                "--format=%cI",
                "--",
                *sorted(paths),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return _parse_ts(proc.stdout.strip())


def _change_ts_for_source(
    source_ref: str | None, *, gate_files: set[str]
) -> datetime | None:
    """Landing/commit timestamp for source_ref (not closeout or worker observed_at)."""
    repo = _git_repo_root()
    if repo is None:
        return None
    if source_ref and source_ref.startswith("git:"):
        sha = source_ref[4:].strip()
        if sha:
            return _git_commit_ts(repo, sha)
    return _git_latest_landing_ts(repo, gate_files)


def _expected_tree_hash(source_ref: str | None, *, gate_files: set[str]) -> str | None:
    repo = _git_repo_root()
    if repo is None:
        return None
    commit: str | None = None
    if source_ref and source_ref.startswith("git:"):
        commit = source_ref[4:].strip() or None
    if commit is None and gate_files:
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    "-1",
                    "--format=%H",
                    "--",
                    *sorted(gate_files),
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode == 0 and proc.stdout.strip():
            commit = proc.stdout.strip()
    if not commit:
        return None
    try:
        tree = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"{commit}^{{tree}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return tree.stdout.strip() if tree.returncode == 0 else None


def _identity_passes(
    health: dict[str, Any],
    *,
    source_ref: str,
    change_ts: datetime | None,
    expected_tree_hash: str | None,
) -> tuple[bool, bool]:
    deploy_mode = str(health.get("deploy_mode") or "").strip()
    if deploy_mode and deploy_mode != "source_synced":
        return False, True

    h_ref = str(health.get("source_ref") or "").strip()
    if h_ref and source_ref and h_ref == source_ref:
        return True, True

    h_tree = str(health.get("source_tree_hash") or "").strip()
    if h_tree and expected_tree_hash and h_tree == expected_tree_hash:
        return True, True

    if h_ref and source_ref:
        src_kind = source_ref.split(":", 1)[0]
        h_kind = h_ref.split(":", 1)[0]
        if src_kind and h_kind == src_kind and h_ref != source_ref:
            return False, True

    synced_at = _parse_ts(health.get("source_synced_at"))
    if synced_at is None or change_ts is None:
        return False, True
    margin = timedelta(seconds=_SAFETY_MARGIN_S)
    return synced_at >= (change_ts + margin), True


def _has_smoke_artifact(closeout: dict[str, Any], surface: str) -> bool:
    for item in closeout.get("verification") or []:
        if not isinstance(item, dict):
            continue
        cmd = str(item.get("command") or "")
        if cmd.startswith(f"deploy_smoke:{surface}:") and "raw_response=" in cmd:
            return True
    manifest = closeout.get("effects_manifest")
    if not isinstance(manifest, dict):
        return False
    surfaces = manifest.get("surfaces")
    if not isinstance(surfaces, dict):
        return False
    for section in surfaces.values():
        if not isinstance(section, dict):
            continue
        cross = section.get("cross_check")
        if isinstance(cross, str) and surface in cross and "raw_response" in cross:
            return True
        for entry in section.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            detail = entry.get("detail")
            if isinstance(detail, dict) and detail.get("raw_response"):
                if detail.get("surface_kind", surface) == surface:
                    return True
    return False


def _reject(
    *,
    request_id: str,
    code: str,
    reason: str,
    retryable: bool = True,
) -> None:
    raise FrontierEndpointError(
        request_id=request_id,
        field="closeout",
        reason=reason,
        status_code=422,
        code=code,
        details={"retryable": retryable},
    )


def _changed_files_from_source_ref(
    ref: SourceRef,
    *,
    cortex: StargateCortexReader,
) -> set[str]:
    """Derive ``actual_changed_files_from_source_ref`` (design 3163 firing model).

    Mirrors how a TODO's ``files_expected`` is read, generalized across every
    resolvable source_ref kind:
      * todo / plan / plan_phase -- cortex entities exposing
        ``attrs.files_expected`` distilled at Gate-2 (read via the canonical
        ``_files_from_entity`` adapter).
      * packet -- lift expected files from the packet body via the admission
        read machinery. Best-effort: the packet file may be ephemeral and gone
        by closeout time, in which case this contributes nothing.
      * agent-bus (and any other resolvable-but-file-less kind) -- no standard
        changed-file surface without a larger refactor; contributes nothing.

    An empty return is handled fail-closed by the caller (never a silent pass).
    """
    kind = ref.source_kind
    if kind in (
        SourceKind.TODO.value,
        SourceKind.PLAN.value,
        SourceKind.PLAN_PHASE.value,
    ):
        try:
            entity = cortex.entity_get(ref.canonical_ref, intent="full")
        except Exception:
            return set()
        attrs = entity.get("attributes") if isinstance(entity, dict) else None
        if not isinstance(attrs, dict):
            return set()
        from implement_admission.normalize import _files_from_entity

        return {
            str(p).strip().lstrip("/")
            for p in _files_from_entity(attrs)
            if isinstance(p, str) and str(p).strip()
        }
    if kind == SourceKind.PACKET.value:
        try:
            from implement_admission.admission_read import read_packet
            from implement_admission.normalize import _files_from_packet

            path = ref.external_ref.split(":", 1)[1]
            packet = read_packet(path, workspaces_root=_workspaces_root())
            return {
                p.strip().lstrip("/")
                for p in _files_from_packet(packet.text)
                if isinstance(p, str) and p.strip()
            }
        except Exception:
            return set()
    return set()


def _closeout_declares_no_runtime_surface(closeout: dict[str, Any]) -> bool:
    """Positive, explicit declaration that a closeout touches no runtime surface.

    True ONLY when the producer affirmatively supplied an ``effects_manifest``
    whose ``surfaces`` map is present and empty -- the documented
    genuinely-empty pass (e.g. a docs-only / cortex-only change with nothing
    for the deploy-state gate to verify). An absent or malformed
    ``effects_manifest`` is NOT such a declaration and must fail closed (an
    under-specified closeout never passes on an empty ``gate_files`` set).
    """
    manifest = closeout.get("effects_manifest")
    if not isinstance(manifest, dict):
        return False
    if "surfaces" not in manifest:
        return False
    surfaces = manifest.get("surfaces")
    if not isinstance(surfaces, dict):
        return False
    return len(surfaces) == 0


def require_deploy_state(
    *,
    request_id: str,
    source_ref: str | None,
    closeout: dict[str, Any],
    cortex: StargateCortexReader,
    admin_override: bool = False,
) -> None:
    """Verify producer deploy-state before implement-closeout pipeline runs."""
    effective_ref = (source_ref or closeout.get("source_ref") or "").strip() or None
    derived_files: set[str] = set()
    ref_resolved = False
    if effective_ref:
        try:
            ref = parse_source_ref(effective_ref)
            ref_resolved = True
            # Spec firing model (design 3163):
            #   gate_files = actual_changed_files_from_source_ref U files_expected
            # Derive the actual changed files from ANY resolvable source_ref
            # kind (todo / plan / plan_phase / packet), not only a TODO's
            # files_expected. Without this a resolvable non-TODO ref
            # contributed nothing and an empty gate_files fell through to a
            # fail-OPEN pass (reviewer defect-2, thread 3185).
            derived_files = _changed_files_from_source_ref(ref, cortex=cortex)
        except SourceRefError:
            if not admin_override:
                _reject(
                    request_id=request_id,
                    code="deploy_state_source_unresolvable",
                    reason=(
                        "source_ref not adapter-resolvable for deploy-state gate; "
                        "recovery: supply closeout file lists "
                        "(files_created/modified/deleted), a resolvable source_ref, "
                        "an effects_manifest with surfaces=={} declaring zero runtime "
                        "surfaces, or deploy_state_admin_override"
                    ),
                )

    gate_files = _gate_files_from_closeout(closeout)
    gate_files.update(derived_files)

    if not gate_files:
        # Fail-closed: an empty changed-file set after attempted source_ref
        # derivation means we cannot prove the producer is freshly deployed
        # for this change. Reject (retryable) rather than pass -- this closes
        # the fail-OPEN bypass where a syntactically-resolvable but file-less
        # source_ref (no derivable files, no TODO files_expected, no closeout
        # file lists) skipped Tier-A health and Tier-B smoke entirely.
        #
        # Two admitted exceptions, both EXPLICIT (never a silent fall-through):
        #   1. admin_override -- audited operator escape hatch.
        #   2. genuinely-empty, no-runtime-surface closeout -- the source_ref
        #      resolved AND the producer affirmatively declared zero runtime
        #      surfaces via effects_manifest. Such a closeout (e.g. docs-only
        #      / cortex-only) has nothing for the gate to verify. An absent or
        #      under-specified effects_manifest is NOT this case; it fails
        #      closed.
        if admin_override:
            return
        if ref_resolved and _closeout_declares_no_runtime_surface(closeout):
            return
        _reject(
            request_id=request_id,
            code="deploy_state_fail_closed",
            reason=(
                "empty gate_files after source_ref derivation: cannot verify "
                "producer deploy-state (no derivable changed files, no "
                "files_expected, no closeout file lists)"
            ),
        )

    producers = _matched_producers(gate_files)
    if not producers:
        return

    change_ts = _change_ts_for_source(effective_ref, gate_files=gate_files)
    expected_tree = _expected_tree_hash(effective_ref, gate_files=gate_files)
    failures: list[str] = []

    for producer in sorted(producers):
        manage_result = _call_manage_health(producer)
        if "error" in manage_result:
            err = manage_result["error"]
            failures.append(f"{producer}: manage unreachable ({err})")
            continue

        state, retryable, hard_fail = _classify_health_state(manage_result)
        if hard_fail or state == "crashed_exit_1":
            detail = manage_result.get("detail", state)
            _reject(
                request_id=request_id,
                code="deploy_state_crashed",
                reason=f"{producer} crashed_exit_1: {detail}",
                retryable=False,
            )
        if state != "healthy":
            failures.append(f"{producer}: {state}")
            continue

        health = _fetch_producer_health_json(producer)
        merged = {**manage_result, **health}
        passed, _ = _identity_passes(
            merged,
            source_ref=effective_ref or "",
            change_ts=change_ts,
            expected_tree_hash=expected_tree,
        )
        if not passed:
            failures.append(f"{producer}: source identity not fresh")

    if failures:
        logger.warning(
            "deploy_state_gate reject: ref=%s failures=%s",
            effective_ref,
            failures,
        )
        _reject(
            request_id=request_id,
            code="deploy_state_pending",
            reason="; ".join(failures),
            retryable=True,
        )

    surfaces = _matched_surfaces(gate_files)
    missing_smoke = sorted(
        surface for surface in surfaces if not _has_smoke_artifact(closeout, surface)
    )
    if missing_smoke:
        _reject(
            request_id=request_id,
            code="deploy_state_smoke_missing",
            reason=f"missing Tier-B smoke for: {', '.join(missing_smoke)}",
            retryable=True,
        )


__all__ = ["SMOKE_RECIPES", "require_deploy_state"]
