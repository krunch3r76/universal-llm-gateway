"""Structured propagation rows for harvest-time restart proof closure.

Rows are **path-derived obligations** (owed restart + typed proof templates), not
observed liveness. Mint-time proof text says what to VERIFY after fire; it does
not claim the process is currently not-live. Legacy ``propagation_residue`` prose
lines coerce into rows when the structured ``propagation`` field is absent.
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from deploy_identity.code_version import normalize_code_ref
from pydantic import BaseModel, model_validator

from implement_admission.propagation_admit_validation import (
    validate_proof_class,
    validate_safe_window,
    validate_service_slug,
)
from implement_admission.service_lib_ownership import slug_for_service_path

logger = logging.getLogger(__name__)

SafeWindow = Literal["harvest", "standalone_ok", "drain_required"]
ProofClass = Literal["process_live", "client_visible", "served_artifact"]
PropagationAction = Literal["sync_restart"]

_SYNC_RESTART_SLUG_RE = re.compile(
    r"^sync_restart:\s*([a-z][a-z0-9_]*)",
    re.IGNORECASE,
)

_DEFAULT_SAFE_WINDOW: dict[str, SafeWindow] = {
    "git_integration_worker": "drain_required",
    "mcp": "standalone_ok",
    "agent_bus": "harvest",
    "cortex_api": "harvest",
    "event_service": "harvest",
    "rag": "harvest",
    "cloud_proxy": "harvest",
    "gateway": "harvest",
    "stargate": "harvest",
}

_DEFAULT_PROOF_CLASS: dict[str, ProofClass] = {
    "git_integration_worker": "served_artifact",
    "mcp": "client_visible",
    "agent_bus": "served_artifact",
    "cortex_api": "served_artifact",
    "event_service": "process_live",
    "rag": "served_artifact",
    "cloud_proxy": "process_live",
    "gateway": "process_live",
    "stargate": "process_live",
}

_PROCESS_LIVE_PROOF = (
    "AFTER restart VERIFY code_ref is ancestor-of-or-equal-to observed code_version "
    "AND VERIFY process identity changed "
    "(pid/process_start_time/process_age_s/uptime_s) since the pre-restart probe"
)

# Optional service specialization for client_visible (class base alone is insufficient).
_CLIENT_VISIBLE_PROOF_BY_SERVICE: dict[str, str] = {
    "mcp": (
        "client_visible: GET /health AND cortex-api /health → "
        "AFTER restart VERIFY both surfaces satisfy the code_ref ancestry check"
    ),
}

# Optional service specialization prefixes for served_artifact.
_SERVED_ARTIFACT_PREFIX_BY_SERVICE: dict[str, str] = {
    "git_integration_worker": "GET served OpenAPI (direct + stargate) → ",
    "cortex_api": "GET served OpenAPI (uds + http when bound) → ",
    "agent_bus": "GET served OpenAPI (uds) → ",
    "rag": "GET served OpenAPI (uds) → ",
}

# Past-tense claim that must never appear in mint-time / open-row proof text.
_PERFORMED_ANCESTRY_CLAIM_RE = re.compile(r"ancestry\s+satisfied", re.IGNORECASE)

# Path/CONSUMERS mint labels obligation; no process probe at this site.
_PATH_DERIVED_OBLIGATION_REASON = "path-derived obligation; liveness: unknown"


class MissingProofTemplateError(ValueError):
    """Raised when no compose template exists for ``(service, proof_class)``."""


def proof_claims_performed_ancestry(proof: str) -> bool:
    """True when proof text asserts a completed ancestry check (mint-time fiction)."""
    return bool(_PERFORMED_ANCESTRY_CLAIM_RE.search(proof or ""))


def _served_artifact_body(*, expected_x_mcp_count: int | None) -> str:
    """Served-artifact obligation body — count clause only when a bound is present."""
    if expected_x_mcp_count is None:
        count_clause = ""
    else:
        count_clause = f"x-mcp count >= {expected_x_mcp_count}, "
    return (
        "served OpenAPI from every client-reachable surface → "
        f"{count_clause}"
        "all surfaces byte-identical, document parses; AFTER restart VERIFY code_ref "
        "is ancestor-of-or-equal-to observed code_version"
    )


def compose_proof(
    service: str,
    proof_class: str,
    *,
    expected_x_mcp_count: int | None = None,
) -> str:
    """Compose mint-time proof obligation from ``proof_class``, with optional service specialization.

    ``proof_class`` is the base; service may specialize. A missing
    ``(service, proof_class)`` pair raises — never substitute another class's prose.
    """
    slug = (service or "").strip().lower()
    pc = (proof_class or "").strip()
    if pc == "process_live":
        return f"service health/liveness → {_PROCESS_LIVE_PROOF} ({slug})"
    if pc == "client_visible":
        template = _CLIENT_VISIBLE_PROOF_BY_SERVICE.get(slug)
        if template is None:
            raise MissingProofTemplateError(
                f"no proof template for (service={slug!r}, proof_class={pc!r})"
            )
        return template
    if pc == "served_artifact":
        prefix = _SERVED_ARTIFACT_PREFIX_BY_SERVICE.get(slug)
        if prefix is None:
            raise MissingProofTemplateError(
                f"no proof template for (service={slug!r}, proof_class={pc!r})"
            )
        return prefix + _served_artifact_body(expected_x_mcp_count=expected_x_mcp_count)
    raise MissingProofTemplateError(
        f"no proof template for (service={slug!r}, proof_class={pc!r})"
    )


class PropagationRow(BaseModel):
    """One harvest-tracked restart requirement with proof obligation."""

    service: str
    action: PropagationAction = "sync_restart"
    code_ref: str
    safe_window: SafeWindow
    hazard: str | None = None
    reason: str | None = None
    proof: str
    proof_class: ProofClass
    proof_class_requested: ProofClass | None = None
    expected_x_mcp_count: int | None = None
    mint_thread: str | None = None
    mint_turn: int | None = None
    # mcp-only: operator-proxy self-preempt of own cdp_ask_live CSE (restart-drain carve-out)
    force: bool = False
    # When False, suppress auto-escalation to force on self-preemptable busy deferrals.
    allow_self_preempt: bool = True

    @model_validator(mode="before")
    @classmethod
    def _apply_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        service = data.get("service")
        if not isinstance(service, str):
            return data
        if not data.get("proof_class"):
            data["proof_class"] = default_proof_class(service)
        if not data.get("proof_class_requested"):
            data["proof_class_requested"] = data.get("proof_class")
        if not data.get("safe_window"):
            data["safe_window"] = default_safe_window(service)
        if not data.get("proof"):
            data["proof"] = compose_proof(
                service,
                str(data["proof_class"]),
                expected_x_mcp_count=data.get("expected_x_mcp_count"),
            )
        if not data.get("action"):
            data["action"] = "sync_restart"
        return data


def slug_for_path(path: str) -> str | None:
    """Map a repo-relative service path to a manage service slug."""
    return slug_for_service_path(path)


def default_safe_window(service: str) -> SafeWindow:
    """Return the matrix default safe window for a service slug."""
    return _DEFAULT_SAFE_WINDOW.get(service, "harvest")


def default_proof(
    service: str,
    proof_class: ProofClass | str | None = None,
    *,
    expected_x_mcp_count: int | None = None,
) -> str:
    """Return the default probe description for a service (+ optional proof_class)."""
    pc = proof_class or default_proof_class(service)
    return compose_proof(service, str(pc), expected_x_mcp_count=expected_x_mcp_count)


def default_proof_class(service: str) -> ProofClass:
    """Return the default proof class for a service slug."""
    return _DEFAULT_PROOF_CLASS.get(service, "process_live")


def coerce_force_flag(raw: Any) -> bool:
    """Parse YAML/JSON ``force`` — only explicit truthy values count."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw == 1
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return False


def coerce_allow_self_preempt_flag(raw: Any) -> bool:
    """Parse ``allow_self_preempt`` — default True when absent; explicit false only."""
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return True


def row_from_mapping(raw: dict[str, Any]) -> PropagationRow:
    """Parse one structured propagation mapping into a row."""
    service = str(raw["service"])
    proof_class = raw.get("proof_class") or default_proof_class(service)
    safe_window = raw.get("safe_window") or default_safe_window(service)
    expected_x_mcp_count = raw.get("expected_x_mcp_count")
    proof = raw.get("proof") or compose_proof(
        service,
        str(proof_class),
        expected_x_mcp_count=expected_x_mcp_count,
    )
    return PropagationRow(
        service=service,
        action=raw.get("action") or "sync_restart",
        code_ref=normalize_code_ref(str(raw["code_ref"])),
        safe_window=safe_window,
        hazard=raw.get("hazard"),
        reason=raw.get("reason"),
        proof=str(proof),
        proof_class=proof_class,
        expected_x_mcp_count=expected_x_mcp_count,
        mint_thread=raw.get("mint_thread"),
        mint_turn=raw.get("mint_turn"),
        force=coerce_force_flag(raw.get("force")),
        allow_self_preempt=coerce_allow_self_preempt_flag(
            raw.get("allow_self_preempt")
        ),
    )


def row_from_mapping_strict(
    raw: dict[str, Any],
) -> tuple[PropagationRow | None, str | None]:
    """Parse §4-sourced row — reject when ``proof_class`` is absent or unknown."""
    proof_class = raw.get("proof_class")
    if not isinstance(proof_class, str) or not proof_class.strip():
        return None, "missing_proof_class"
    if proof_class.strip() not in {"process_live", "client_visible", "served_artifact"}:
        return None, f"unknown_proof_class:{proof_class}"
    service = str(raw["service"]).strip().lower()
    service_error = validate_service_slug(service)
    if service_error:
        return None, service_error
    safe_window_error = validate_safe_window(raw.get("safe_window"))
    if safe_window_error:
        return None, safe_window_error
    proof_class_error = validate_proof_class(service, proof_class.strip())
    if proof_class_error:
        return None, proof_class_error
    safe_window = raw.get("safe_window") or default_safe_window(service)
    expected_x_mcp_count = raw.get("expected_x_mcp_count")
    pc = proof_class.strip()
    try:
        proof = raw.get("proof") or compose_proof(
            service, pc, expected_x_mcp_count=expected_x_mcp_count
        )
    except MissingProofTemplateError as exc:
        return None, f"missing_proof_template:{exc}"
    force = coerce_force_flag(raw.get("force"))
    # Align with handler_propagation._FORCE_ALLOWED_SERVICES (SoT); ¬ narrow handler.
    if force and service not in {"mcp", "cdp_ask"}:
        return None, "force_only_allowed_for_mcp_or_cdp_ask"
    return (
        PropagationRow(
            service=service,
            action=raw.get("action") or "sync_restart",
            code_ref=normalize_code_ref(str(raw.get("code_ref") or "HEAD")),
            safe_window=safe_window,  # type: ignore[arg-type]
            hazard=raw.get("hazard"),
            reason=raw.get("reason"),
            proof=str(proof),
            proof_class=pc,  # type: ignore[arg-type]
            expected_x_mcp_count=expected_x_mcp_count,
            mint_thread=raw.get("mint_thread"),
            mint_turn=raw.get("mint_turn"),
            force=force,
            allow_self_preempt=coerce_allow_self_preempt_flag(
                raw.get("allow_self_preempt")
            ),
        ),
        None,
    )


def rows_from_parsed_block(
    raw_rows: list[dict[str, Any]],
) -> tuple[list[PropagationRow], list[str]]:
    """Materialize §4-parsed mappings with strict ``proof_class`` enforcement."""
    rows: list[PropagationRow] = []
    flags: list[str] = []
    for index, raw in enumerate(raw_rows):
        row, flag = row_from_mapping_strict(raw)
        if flag:
            flags.append(f"propagation_row_{index}_{flag}")
            continue
        if row is not None:
            rows.append(row)
    return rows, flags


def rows_from_residue_lines(
    lines: list[str],
    *,
    code_ref: str,
) -> tuple[list[PropagationRow], list[str]]:
    """Coerce legacy ``propagation_residue`` lines into structured rows."""
    rows: list[PropagationRow] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        match = _SYNC_RESTART_SLUG_RE.match(text)
        if match:
            slug = match.group(1).lower()
            if slug in seen:
                continue
            seen.add(slug)
            pc = default_proof_class(slug)
            rows.append(
                PropagationRow(
                    service=slug,
                    code_ref=code_ref,
                    safe_window=default_safe_window(slug),
                    proof=compose_proof(slug, pc),
                    proof_class=pc,
                    reason=_PATH_DERIVED_OBLIGATION_REASON,
                )
            )
            continue
        if text.startswith(("install_plugin:", "libs_touched:", "unresolved:")):
            skipped.append(text)
    return rows, skipped


def is_lib_test_module(path: str) -> bool:
    """True when *path* is a pytest module under ``libs/``."""
    if not path.startswith("libs/") or not path.endswith(".py"):
        return False
    return path.rsplit("/", 1)[-1].startswith("test_")


def consumers_for_lib_path(path: str) -> tuple[str, ...] | None:
    """Import ``CONSUMERS`` from a libs module when declared."""
    if is_lib_test_module(path):
        return None
    if not path.startswith("libs/") or not path.endswith(".py"):
        return None
    rel = path[len("libs/") : -3]
    parts = rel.replace("/", ".").split(".")
    for end in range(len(parts), 0, -1):
        module_path = ".".join(parts[:end])
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            continue
        consumers = getattr(module, "CONSUMERS", None)
        if consumers is None:
            continue
        if isinstance(consumers, (list, tuple)):
            return tuple(str(item) for item in consumers)
    return None


def land_paths_for_propagation(
    *,
    created: Sequence[str] = (),
    modified: Sequence[str] = (),
    untracked: Sequence[str] = (),
) -> list[str]:
    """Land-shaped paths that may feed lib-consumer and sync_restart propagation."""
    return [
        *created,
        *modified,
        *untracked,
    ]


def rows_from_lib_consumers(
    paths: list[str],
    *,
    code_ref: str,
) -> list[PropagationRow]:
    """Mint one row per declared consumer for shared-lib lands."""
    rows: list[PropagationRow] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        if is_lib_test_module(path):
            continue
        consumers = consumers_for_lib_path(path)
        if not consumers:
            continue
        for slug in consumers:
            key = (slug, code_ref)
            if key in seen:
                continue
            seen.add(key)
            pc = default_proof_class(slug)
            rows.append(
                PropagationRow(
                    service=slug,
                    code_ref=code_ref,
                    safe_window=default_safe_window(slug),
                    proof=compose_proof(slug, pc),
                    proof_class=pc,
                    reason=(
                        f"shared lib land: {path}; {_PATH_DERIVED_OBLIGATION_REASON}"
                    ),
                )
            )
    return rows


def rows_from_service_paths(
    paths: list[str],
    *,
    code_ref: str,
) -> list[PropagationRow]:
    """Derive sync_restart obligation rows from touched service Python paths."""
    rows: list[PropagationRow] = []
    seen: set[str] = set()
    for path in paths:
        slug = slug_for_path(path)
        if slug is None or slug in seen:
            continue
        seen.add(slug)
        pc = default_proof_class(slug)
        rows.append(
            PropagationRow(
                service=slug,
                code_ref=code_ref,
                safe_window=default_safe_window(slug),
                proof=compose_proof(slug, pc),
                proof_class=pc,
                reason=_PATH_DERIVED_OBLIGATION_REASON,
            )
        )
    return rows


def resolve_code_ref(payload: dict[str, Any]) -> str:
    """Pick land SHA from closeout evidence or fall back to unknown."""
    evidence = payload.get("evidence_uris") or {}
    if isinstance(evidence, dict):
        git_refs = evidence.get("git_refs") or []
        if isinstance(git_refs, list):
            for ref in git_refs:
                if isinstance(ref, str) and ref.strip():
                    return normalize_code_ref(ref.strip())
    for key in ("code_ref", "land_sha", "merge_sha"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_code_ref(value.strip())
    closeout_head = payload.get("closeout_head")
    if isinstance(closeout_head, str) and closeout_head.strip():
        return normalize_code_ref(closeout_head.strip())
    return "unknown"


def rows_from_closeout_payload(
    payload: dict[str, Any],
) -> tuple[list[PropagationRow], list[str], bool]:
    """Resolve propagation rows from a closeout dict.

    Returns ``(rows, skipped_lines, prose_only_advisory)``. Structured
    ``propagation`` wins over legacy ``propagation_residue``.
    """
    raw_structured = payload.get("propagation")
    if isinstance(raw_structured, list) and raw_structured:
        rows = [
            row_from_mapping(item) for item in raw_structured if isinstance(item, dict)
        ]
        return rows, [], False

    code_ref = resolve_code_ref(payload)
    land_paths: list[str] = []
    for field in ("files_created", "files_modified"):
        raw = payload.get(field)
        if isinstance(raw, list):
            land_paths.extend(entry for entry in raw if isinstance(entry, str))

    raw_residue = payload.get("propagation_residue")
    lines: list[str] = []
    if isinstance(raw_residue, list):
        lines = [str(item) for item in raw_residue if isinstance(item, str) and item]

    rows, skipped = rows_from_residue_lines(lines, code_ref=code_ref)
    if rows:
        return rows, skipped, False

    consumer_rows = rows_from_lib_consumers(land_paths, code_ref=code_ref)
    if consumer_rows:
        return consumer_rows, skipped, False

    service_rows = rows_from_service_paths(land_paths, code_ref=code_ref)
    prose_only = bool(lines) and not service_rows
    runtime_only = any(
        path.startswith(("services/", "libs/")) and path.endswith(".py")
        for path in land_paths
    )
    if prose_only or (runtime_only and lines and not service_rows):
        logger.warning(
            "propagation_prose_only: runtime-code land with prose residue only"
        )
        return service_rows, skipped, True

    return service_rows, skipped, False


__all__ = [
    "MissingProofTemplateError",
    "PropagationRow",
    "coerce_allow_self_preempt_flag",
    "coerce_force_flag",
    "compose_proof",
    "default_proof",
    "default_proof_class",
    "default_safe_window",
    "is_lib_test_module",
    "land_paths_for_propagation",
    "proof_claims_performed_ancestry",
    "resolve_code_ref",
    "row_from_mapping",
    "row_from_mapping_strict",
    "rows_from_closeout_payload",
    "rows_from_lib_consumers",
    "rows_from_parsed_block",
    "rows_from_residue_lines",
    "rows_from_service_paths",
    "slug_for_path",
]
