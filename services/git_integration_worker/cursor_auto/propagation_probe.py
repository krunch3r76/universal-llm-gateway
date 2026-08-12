"""Queue/I2 probes and proof-of-live checks for cursor-auto propagation.

Callers are admit validation and ``PROOF_PROBE_REGISTRY``; ``PROCESS_LIVE_FETCHERS``
keys are the process_live satisfiability oracle (adding a fetcher unlocks a slug).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from typing import Any, Literal

import httpx
from deploy_identity.code_ref_relation import code_ref_satisfied
from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url
from implement_admission.propagation_row import PropagationRow
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CLOUD_PROXY_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_STARGATE_URL,
    EVENTS_QUERY_SOCK,
    make_sync_client,
    resolve_rag_base_url,
)

_GIW_QUEUE_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_QUEUE_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/queue",
)
_GIW_LIVENESS_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_LIVENESS_URL",
    "http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness",
)
_GATEWAY_CONTAINER = os.environ.get("GATEWAY_CONTAINER", "edge-localhost")
_GATEWAY_INTERNAL_HEALTH = (
    f"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '9998')}/health"
)

# Satisfiability oracle for process_live advertisement. Registry + legal_proof_classes
# derive from these keys — adding a fetcher unlocks the slug without a second hardcode.
ProcessLiveFetcher = Callable[[], dict[str, Any] | None]


def row_key(row: PropagationRow) -> str:
    """Stable identity key for a propagation row (service, code_ref, action)."""
    return f"{row.service}:{row.code_ref}:{row.action}"


def _fetch_json(url: str, *, timeout_s: float = 3.0) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, OSError):
        return None


def _fetch_health_at_base(
    base_url: str, *, timeout_s: float = 3.0
) -> dict[str, Any] | None:
    """GET ``/health`` from a UDS or TCP service base URL; None on failure."""
    if not base_url or not str(base_url).strip():
        return None
    try:
        with make_sync_client(str(base_url).strip(), timeout=timeout_s) as client:
            resp = client.get("/health")
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, OSError):
        return None


def _fetch_cortex_api_health(*, timeout_s: float = 3.0) -> dict[str, Any] | None:
    return _fetch_health_at_base(DEFAULT_CORTEX_URL, timeout_s=timeout_s)


def giw_i2_clear(*, queue_snapshot: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Return whether GIW restart is permitted under I2 (no in-flight closeout relay)."""
    snapshot = queue_snapshot if queue_snapshot is not None else _fetch_json(_GIW_QUEUE_URL)
    if snapshot is None:
        return False, "i2_queue_unreachable"
    claimed = int(snapshot.get("claimed") or 0)
    pending = int(snapshot.get("pending") or 0)
    if claimed > 0:
        return False, "i2_inflight_closeout"
    if pending > 0:
        return False, "i2_pending_closeout"
    return True, "ok"


def _fetch_giw_liveness() -> dict[str, Any] | None:
    return _fetch_json(_GIW_LIVENESS_URL)


def _fetch_mcp_health() -> dict[str, Any] | None:
    return _fetch_json(resolve_mcp_health_probe_url())


def _fetch_stargate_health() -> dict[str, Any] | None:
    return _fetch_health_at_base(DEFAULT_STARGATE_URL)


def _fetch_rag_health() -> dict[str, Any] | None:
    return _fetch_health_at_base(resolve_rag_base_url())


def _fetch_cloud_proxy_health() -> dict[str, Any] | None:
    return _fetch_health_at_base(DEFAULT_CLOUD_PROXY_URL)


def _fetch_event_service_health() -> dict[str, Any] | None:
    return _fetch_health_at_base(f"unix://{EVENTS_QUERY_SOCK}")


def _fetch_agent_bus_health() -> dict[str, Any] | None:
    return _fetch_health_at_base(DEFAULT_AGENT_BUS_URL)


def resolve_cdp_ask_probe_base_url() -> str | None:
    """Resolve cdp-ask ``/health`` base for propagation settle probes.

    Matches manage lifecycle authority: ``cdp_ask_url_config()`` / mcp
    ``project_ask_url``. Explicit ``PROJECT_ASK_URL`` env is used when config
    is absent or agrees; when both are present and disagree, config wins
    (stale env export must not blind the manage-equivalent probe).
    """
    from scripts.model_manager.ui.controller.service_config import cdp_ask_url_config

    env = os.environ.get("PROJECT_ASK_URL", "").strip().rstrip("/")
    cfg = cdp_ask_url_config()
    config_base = cfg[2] if cfg else None
    if not env:
        return config_base
    if config_base is None:
        return env or None
    if env == config_base:
        return env
    return config_base


def _fetch_cdp_ask_health() -> dict[str, Any] | None:
    """Probe cdp-ask satellite ``/health`` via manage-equivalent URL resolution."""
    base = resolve_cdp_ask_probe_base_url()
    if not base:
        return None
    return _fetch_health_at_base(base)


def _fetch_gateway_health() -> dict[str, Any] | None:
    """Probe gateway ``/health``.

    Prefer ``GATEWAY_HEALTH_URL`` (full ``…/health`` URL or service base).
    Otherwise docker-exec into the edge container (gateway is container-local
    :9998; not published on the host).
    """
    explicit = os.environ.get("GATEWAY_HEALTH_URL", "").strip()
    if explicit:
        if explicit.startswith(("http://", "https://")) and explicit.rstrip(
            "/"
        ).endswith("/health"):
            return _fetch_json(explicit)
        return _fetch_health_at_base(explicit)
    try:
        proc = subprocess.run(
            [
                "docker",
                "exec",
                _GATEWAY_CONTAINER,
                "curl",
                "-sS",
                "-m",
                "2",
                _GATEWAY_INTERNAL_HEALTH,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


PROCESS_LIVE_FETCHERS: dict[str, ProcessLiveFetcher] = {
    "git_integration_worker": _fetch_giw_liveness,
    "mcp": _fetch_mcp_health,
    "cortex_api": _fetch_cortex_api_health,
    "gateway": _fetch_gateway_health,
    "stargate": _fetch_stargate_health,
    "rag": _fetch_rag_health,
    "cloud_proxy": _fetch_cloud_proxy_health,
    "event_service": _fetch_event_service_health,
    "cdp_ask": _fetch_cdp_ask_health,
    "agent_bus": _fetch_agent_bus_health,
}


def process_live_probeable_services() -> frozenset[str]:
    """Return slugs whose process_live probe can produce a payload (fetcher map keys)."""
    return frozenset(PROCESS_LIVE_FETCHERS)


def probe_process_live(service: str) -> dict[str, Any] | None:
    """Fetch health/liveness JSON for proof-of-live closure.

    Returns None when the slug has no fetcher or the probe request fails.
    """
    fetcher = PROCESS_LIVE_FETCHERS.get(service)
    if fetcher is None:
        return None
    return fetcher()


def probe_for_row(row: PropagationRow) -> dict[str, Any] | None:
    """Probe closure surface for one propagation row via the B1 registry."""
    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        dispatch_proof_probe,
    )

    result = dispatch_proof_probe(row)
    if result.error is not None:
        return None
    return result.payload


def _code_version(payload: dict[str, Any]) -> str | None:
    observed = payload.get("code_version")
    return observed if isinstance(observed, str) else None


IdentityAttestation = Literal["changed", "unchanged", "indeterminate"]
IdentityMeasurement = Literal["absent", "measured"]

_IDENTITY_AWARE_PROOF_CLASSES = frozenset(
    {"process_live", "served_artifact", "client_visible"}
)


class IdentityMeasurementError(ValueError):
    """Close chokepoint cannot stamp identity_measurement — row must not close."""

IDENTIFIER_FIELDS: tuple[str, ...] = ("pid", "process_start_time", "source_synced_at")
AGE_FIELDS: tuple[str, ...] = ("process_age_s", "uptime_s")


def _normalize_identifier_value(field: str, value: Any) -> Any | None:
    """Return a comparable identifier value, or None when the field is absent."""
    if value is None:
        return None
    if field == "pid":
        return value
    if field in ("process_start_time", "source_synced_at"):
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
    return None


def _attesting_identifier_fields(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    service: str | None = None,
) -> list[str]:
    """Identifier-class fields present on both probes and usable for attestation."""
    shared: list[str] = []
    for field in IDENTIFIER_FIELDS:
        if (
            _normalize_identifier_value(field, before.get(field)) is not None
            and _normalize_identifier_value(field, after.get(field)) is not None
        ):
            shared.append(field)
    if service == "mcp" and "pid" in shared:
        if before.get("pid") == 1 and after.get("pid") == 1:
            shared = [field for field in shared if field != "pid"]
    return shared


def attest_identity_delta(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    service: str | None = None,
) -> IdentityAttestation:
    """Compare all shared identifier-class fields; age counters never attest identity."""
    fields = _attesting_identifier_fields(before, after, service=service)
    if not fields:
        return "indeterminate"
    for field in fields:
        before_val = _normalize_identifier_value(field, before.get(field))
        after_val = _normalize_identifier_value(field, after.get(field))
        if before_val != after_val:
            return "changed"
    return "unchanged"


AuthorityAttestationResult = IdentityAttestation | Literal["fall_through"]


def _authority_identity_binds_row(
    authority: dict[str, Any],
    *,
    service: str,
    intent_id: str | None = None,
) -> bool:
    """True when *authority* describes the row under attestation (AC14 / L5)."""
    auth_service = authority.get("service")
    if auth_service is None or auth_service != service:
        return False
    auth_intent = authority.get("intent_id")
    if intent_id is not None and auth_intent is not None and auth_intent != intent_id:
        return False
    return True


def attest_authority_identity(
    authority: dict[str, Any] | None,
) -> AuthorityAttestationResult:
    """Attest identity from manage authority observation when readiness is proven.

    Returns ``fall_through`` when authority cannot bind (missing readiness, partial
    old/new after normalize, or cross-source mismatch) so the self-report arm may
    still attest. AC9 unchanged is terminal — callers must not fall through on that
    verdict. AC12 requires ``readiness_proven is True``; AC13 normalizes before
    comparing old/new.
    """
    from scripts.model_manager.ui.controller.service_ctl.authority_identity import (
        normalize_authority_value,
    )

    if not authority:
        return "fall_through"
    if authority.get("readiness_proven") is not True:
        return "fall_through"

    old_src = authority.get("old_identity_source") or authority.get("identity_source")
    new_src = authority.get("new_identity_source") or authority.get("identity_source")
    if old_src != new_src:
        return "fall_through"

    try:
        norm_old = normalize_authority_value(authority.get("old"))
        norm_new = normalize_authority_value(authority.get("new"))
    except ValueError:
        return "fall_through"
    if norm_old is None or norm_new is None:
        return "fall_through"
    if norm_old == norm_new:
        return "unchanged"
    return "changed"


def resolve_identity_attestation(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    service: str,
    authority_identity: dict[str, Any] | None = None,
    surface: str = "default",
    intent_id: str | None = None,
) -> IdentityAttestation:
    """Combine authority-primary and self-report identity attestation (Option C)."""
    if before is None:
        return "indeterminate"
    bound_authority = authority_identity
    if (
        authority_identity is not None
        and not _authority_identity_binds_row(
            authority_identity, service=service, intent_id=intent_id
        )
    ):
        bound_authority = None
    authority_result = attest_authority_identity(bound_authority)
    if authority_result == "changed":
        return "changed"
    if authority_result == "unchanged":
        return "unchanged"
    if surface == "mcp_health":
        before_section = _mcp_health_section(before)
        after_section = _mcp_health_section(after)
        if before_section is None or after_section is None:
            return "indeterminate"
        return attest_identity_delta(
            before_section, after_section, service=service
        )
    if surface == "liveness":
        before_section = _served_artifact_identity_section(before)
        after_section = _served_artifact_identity_section(after)
        if before_section is None or after_section is None:
            return "indeterminate"
        return attest_identity_delta(
            before_section, after_section, service=service
        )
    return attest_identity_delta(before, after, service=service)


def process_identity(payload: dict[str, Any]) -> str | None:
    """Return a comparable identifier-class key from health/liveness JSON, excluding age."""
    pid = payload.get("pid")
    if pid is not None:
        return f"pid:{pid}"
    start = payload.get("process_start_time")
    if isinstance(start, str) and start.strip():
        return f"start:{start.strip()}"
    synced = payload.get("source_synced_at")
    if isinstance(synced, str) and synced.strip():
        return f"synced:{synced.strip()}"
    return None


def strong_process_identity(
    payload: dict[str, Any],
    *,
    service: str | None = None,
) -> bool:
    """True when payload carries an attesting identifier-class field (not age alone).

    ``mcp`` ``pid == 1`` is container-invariant and does not bind process identity.
    """
    for field in IDENTIFIER_FIELDS:
        value = _normalize_identifier_value(field, payload.get(field))
        if value is None:
            continue
        if service == "mcp" and field == "pid" and value == 1:
            continue
        return True
    return False


def _mcp_health_section(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    section = payload.get("mcp_health")
    if isinstance(section, dict):
        return section
    if any(key in payload for key in (*IDENTIFIER_FIELDS, "code_version")):
        return payload
    return None


def _served_artifact_identity_section(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Prefer nested ``liveness``; else flat identifier fields on the probe payload."""
    if not isinstance(payload, dict):
        return None
    section = payload.get("liveness")
    if isinstance(section, dict):
        return section
    if any(key in payload for key in IDENTIFIER_FIELDS):
        return payload
    return None


def proof_identity_attestation(
    before: dict[str, Any] | None,
    after: dict[str, Any],
    *,
    service: str,
    surface: str = "default",
    authority_identity: dict[str, Any] | None = None,
    intent_id: str | None = None,
) -> IdentityAttestation:
    """Attest identity movement for one proof surface using identifier-class fields only."""
    return resolve_identity_attestation(
        before,
        after,
        service=service,
        authority_identity=authority_identity,
        surface=surface,
        intent_id=intent_id,
    )


def _proof_before_for_measurement(
    proof_payload: dict[str, Any],
    *,
    code_ref: str,
    open_row_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Return coerced before dict, or (None, malformed) when persisted before is invalid."""
    if "proof_before" in proof_payload:
        candidate = proof_payload["proof_before"]
        if candidate is None:
            return None, False
        if not isinstance(candidate, dict):
            raise IdentityMeasurementError(
                "proof_before in close payload was not a dict"
            )
        return candidate, False

    if open_row_payload is None:
        return None, False

    stored_ref = open_row_payload.get("code_ref_at_submit")
    if isinstance(stored_ref, str) and stored_ref != code_ref:
        return None, False

    if "proof_before" not in open_row_payload:
        return None, False

    candidate = open_row_payload["proof_before"]
    if candidate is None:
        return None, False
    if not isinstance(candidate, dict):
        return None, True
    return candidate, False


def resolve_identity_measurement(
    proof_payload: dict[str, Any],
    *,
    service: str,
    proof_class: str,
    code_ref: str,
    open_row_payload: dict[str, Any] | None = None,
) -> IdentityMeasurement:
    """Record whether before/after identity was measured for this close attempt.

    Parallel to ``IdentityAttestation`` (evaluative result). ``absent`` means no
    measurement was made; ``measured`` means a before/after or attestation arm ran.
    """
    _ = service  # reserved for surface-specific measurement rules
    if proof_payload.get("version_satisfaction_case") == "ancestry_satisfied":
        return "absent"
    if "identity_attestation" in proof_payload or "identity_attested" in proof_payload:
        return "measured"
    if proof_payload.get("proof_predicate_satisfied") is True:
        return "measured"

    before, malformed = _proof_before_for_measurement(
        proof_payload,
        code_ref=code_ref,
        open_row_payload=open_row_payload,
    )
    if malformed:
        raise IdentityMeasurementError(
            "persisted proof_before was not a dict — cannot stamp identity_measurement"
        )
    if isinstance(before, dict):
        return "measured"

    if (
        proof_class in _IDENTITY_AWARE_PROOF_CLASSES
        and proof_payload.get("proof_class_executed") is not None
        and proof_payload.get("version_satisfaction_case") is None
    ):
        return "measured"
    return "absent"


def _probe_is_outgoing_generation(
    payload: dict[str, Any],
    *,
    settle_not_before_monotonic: float,
) -> bool:
    """True when uptime_s implies the probed process predates restart completion."""
    uptime = payload.get("uptime_s")
    if not isinstance(uptime, (int, float)):
        return False
    process_started_monotonic = time.monotonic() - float(uptime)
    return process_started_monotonic < settle_not_before_monotonic


def _section_code_ref_satisfied(section: dict[str, Any], code_ref: str) -> bool:
    version = section.get("code_version")
    return isinstance(version, str) and code_ref_satisfied(code_ref, version)


def proof_observed(
    row: PropagationRow,
    payload: dict[str, Any] | None,
    *,
    before: dict[str, Any] | None = None,
    settle_not_before_monotonic: float | None = None,
    probed_surface: str | None = None,
    authority_identity: dict[str, Any] | None = None,
    intent_id: str | None = None,
) -> bool:
    """Return whether *payload* closes the row's proof_class obligation.

    Emits a deployment-identity boundary event; process_live requires a version
    match plus identity delta (or post-restart strong identity).
    """
    from services.git_integration_worker.cursor_sdk_boundary_deployment_identity import (
        DeploymentIdentityEmit,
        emit_deployment_identity_boundary,
    )

    surface = probed_surface or row.service
    emit_deployment_identity_boundary(
        DeploymentIdentityEmit(
            expected_executor=row.service,
            probed_surface=surface,
            payload=payload,
            code_ref=row.code_ref,
            before_payload=before,
            landed_at_monotonic=settle_not_before_monotonic,
        )
    )
    if payload is None:
        return False
    if row.proof_class == "served_artifact":
        from services.git_integration_worker.cursor_auto.propagation_served_artifact import (
            served_artifact_descriptor,
            served_artifact_observed,
        )

        descriptor = served_artifact_descriptor(row.service)
        if descriptor is None:
            return False
        expected = row.expected_x_mcp_count or descriptor.expected_x_mcp_count
        if not served_artifact_observed(
            payload,
            code_ref=row.code_ref,
            expected_x_mcp_count=expected,
        ):
            return False
        # Byte-identical OpenAPI only proves surfaces agree with each other —
        # compatible with all being stale together. Identity movement is owed
        # the same way as client_visible (harvest before/after).
        attestation = proof_identity_attestation(
            before,
            payload,
            service=row.service,
            surface="liveness",
            authority_identity=authority_identity,
            intent_id=intent_id,
        )
        return attestation == "changed"
    if row.proof_class == "client_visible" and row.service == "mcp":
        mcp_health = payload.get("mcp_health")
        cortex_health = payload.get("cortex_api")
        if not isinstance(mcp_health, dict) or not isinstance(cortex_health, dict):
            return False
        if not (
            _section_code_ref_satisfied(mcp_health, row.code_ref)
            and _section_code_ref_satisfied(cortex_health, row.code_ref)
        ):
            return False
        attestation = proof_identity_attestation(
            before,
            payload,
            service=row.service,
            surface="mcp_health",
            authority_identity=authority_identity,
            intent_id=intent_id,
        )
        return attestation == "changed"
    observed = _code_version(payload)
    if not isinstance(observed, str) or not code_ref_satisfied(row.code_ref, observed):
        return False
    if before is not None:
        attestation = resolve_identity_attestation(
            before,
            payload,
            service=row.service,
            authority_identity=authority_identity,
            intent_id=intent_id,
        )
        return attestation == "changed"
    if settle_not_before_monotonic is not None:
        if _probe_is_outgoing_generation(
            payload, settle_not_before_monotonic=settle_not_before_monotonic
        ):
            return False
        return strong_process_identity(payload, service=row.service)
    return False


__all__ = [
    "AGE_FIELDS",
    "IDENTIFIER_FIELDS",
    "IdentityAttestation",
    "IdentityMeasurement",
    "IdentityMeasurementError",
    "PROCESS_LIVE_FETCHERS",
    "resolve_cdp_ask_probe_base_url",
    "attest_authority_identity",
    "attest_identity_delta",
    "giw_i2_clear",
    "probe_for_row",
    "probe_process_live",
    "process_identity",
    "process_live_probeable_services",
    "proof_identity_attestation",
    "proof_observed",
    "resolve_identity_attestation",
    "resolve_identity_measurement",
    "row_key",
    "strong_process_identity",
]
