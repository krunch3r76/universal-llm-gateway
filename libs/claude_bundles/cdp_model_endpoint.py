"""Shared CDP model-endpoint adapter (team_dispatch + pipeline fronts).

Thin wrapper over the native CDP contract (``cdp_ask.client.CdpAskClient`` /
Stargate ``POST /api/v1/providers/cdp/ask``). Terminal complete only after
harvest proof (``content_proof`` or ``archive_uri``) or failed+stall_stage.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from cdp_ask.client import CdpAskClient, CdpAskClientError, project_ask_base_url
from cdp_ask.models import SubmitProjectAskRequest

from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_cdp_prompt_with_skills,
    sweep_ephemeral,
)
from claude_bundles.cdp_progress_trace import ProgressTrace
from claude_bundles.cdp_progress_trace import fingerprint as progress_fingerprint
from claude_bundles.chat_model_match import normalize_picker_request
from claude_bundles.operator_proxy_mission import is_operator_proxy_mission_purpose

DEFAULT_MAX_WALL_S = 1800
DEFAULT_NO_PROGRESS_S = 600
DEFAULT_POLL_INTERVAL_S = 2.0
CDP_SUBSTRATE = "web-anthropic-cdp"
CDP_REPLY_FROM = "cdp"

# Operator-proxy / mission CSE must stay live across long Auto legs. Poller
# wall / no-progress must NOT Stop-click the page. Clean CSE break is only for
# continuity handoff (after a new CSE is confirmed) or rare human escalation —
# never for max_wall_s / no_progress_s alone.

# Phases after the page goes idle: the satellite is resolving harvest (Cowork
# Output download, archive write) and emits no per-sample progress, so the
# no-progress fingerprint necessarily freezes. Only ``max_wall_s`` bounds these.
POST_IDLE_PHASES = frozenset({"turn_idle", "content_proof", "archiving"})

RETRYABLE_OVERLOAD_STATUS = frozenset({529, 503})
SUBMIT_RETRY_BACKOFF_S = 5.0
MAX_OVERLOAD_SUBMIT_ATTEMPTS = 2
UPSTREAM_OVERLOADED = "upstream_overloaded"
_OVERLOAD_ONLY_BODY_RE = re.compile(r"API Error:\s*52[93]", re.IGNORECASE)
_OVERLOAD_ONLY_LINE_RE = re.compile(
    r"^(?:Claude responded: )?API Error:\s*52[93].*$",
    re.IGNORECASE,
)
_OVERLOAD_ONLY_MAX_LEN = 500

HarvestSource = Literal["chat", "output-file", "auto"]
ExpectedSize = Literal["small", "large", "auto"]


@dataclass(frozen=True, slots=True)
class CdpGenerateResult:
    """Outcome of one CDP generate run (adapter core)."""

    ok: bool
    body: str
    execution_id: str
    satellite_execution_id: str | None
    prompt_uri: str
    picker_model: str
    archive_uri: str | None = None
    content_proof_uri: str | None = None
    content_proof_sha256: str | None = None
    stall_stage: str | None = None
    error: str | None = None
    substrate: str = CDP_SUBSTRATE
    cost_source: str = "unavailable"
    poll_snapshots: int = 0
    extras: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "body": self.body,
            "execution_id": self.execution_id,
            "satellite_execution_id": self.satellite_execution_id,
            "prompt_uri": self.prompt_uri,
            "picker_model": self.picker_model,
            "archive_uri": self.archive_uri,
            "content_proof_uri": self.content_proof_uri,
            "content_proof_sha256": self.content_proof_sha256,
            "stall_stage": self.stall_stage,
            "error": self.error,
            "substrate": self.substrate,
            "cost_source": self.cost_source,
            "poll_snapshots": self.poll_snapshots,
            **self.extras,
        }


def project_ask_url() -> str:
    """Return configured satellite base URL from ``PROJECT_ASK_URL`` env."""
    return project_ask_base_url()


def picker_from_model_id(model_id: str) -> str:
    """Map ``cdp/<picker>`` dispatch ids to canonical picker wire for the UI."""
    if "/" not in model_id:
        return normalize_picker_request(model_id)
    provider, picker = model_id.split("/", 1)
    if provider != "cdp" or not picker:
        raise ValueError(f"expected cdp/<picker>, got {model_id!r}")
    return normalize_picker_request(f"cdp/{picker}")


def has_proof(snapshot: dict[str, Any]) -> bool:
    """True when snapshot carries harvest proof (archive_uri or content_proof).

    ``completion_phase=terminal`` alone is insufficient without archive/content_proof.
    """
    if snapshot.get("archive_uri"):
        return True
    phase = str(snapshot.get("completion_phase") or "")
    if phase == "failed":
        return False
    if snapshot.get("content_proof_uri") and phase in {"content_proof", "archiving"}:
        return True
    return False


_has_proof = has_proof


@dataclass
class _ProofCarry:
    """Last-known harvest proof fields from status-bearing poll snapshots."""

    archive_uri: str | None = None
    content_proof_uri: str | None = None
    content_proof_sha256: str | None = None

    def absorb_status_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "status" not in snapshot:
            return
        uri = snapshot.get("archive_uri")
        if uri is not None:
            self.archive_uri = uri
        proof_uri = snapshot.get("content_proof_uri")
        if proof_uri is not None:
            self.content_proof_uri = proof_uri
        proof_sha = snapshot.get("content_proof_sha256")
        if proof_sha is not None:
            self.content_proof_sha256 = proof_sha

    def as_result_fields(self) -> dict[str, str | None]:
        return {
            "archive_uri": self.archive_uri,
            "content_proof_uri": self.content_proof_uri,
            "content_proof_sha256": self.content_proof_sha256,
        }


_progress_fingerprint = progress_fingerprint


def _terminal_failure(snapshot: dict[str, Any]) -> bool:
    status = str(snapshot.get("status") or "")
    if status in {"failed", "aborted"}:
        return True
    # Transient transport errors while still pending/running keep polling.
    if snapshot.get("error") and status not in {"running", "pending"}:
        return True
    return False


terminal_failure = _terminal_failure


def _post_idle(snapshot: dict[str, Any]) -> bool:
    """Whether the snapshot sits in a phase whose progress signal is unobservable."""
    return str(snapshot.get("completion_phase") or "") in POST_IDLE_PHASES


def completed_without_proof(snapshot: dict[str, Any]) -> bool:
    """True when satellite status is completed but proof fields are absent."""
    return str(snapshot.get("status") or "") == "completed" and not has_proof(snapshot)


_completed_without_proof = completed_without_proof


def _abort_then_sweep(
    satellite_id: str | None,
    execution_id: str,
    *,
    ask_client: CdpAskClient | None = None,
    client: httpx.Client | None = None,
    retain_cse: bool = False,
) -> dict[str, Any]:
    """Abort satellite (Stop-click) then sweep staging — unless *retain_cse*.

    When ``retain_cse`` is true (operator-proxy / mission), skip ``abort`` so the
    Cowork page keeps streaming; only ephemeral prompt staging is swept.
    """
    abort_info: dict[str, Any] = {}
    if retain_cse:
        abort_info = {"abort_skipped": True, "reason": "operator_proxy_cse_retain"}
        sweep_ephemeral(execution_id)
        return abort_info
    if satellite_id:
        try:
            relay = ask_client or CdpAskClient()
            abort_info = relay.abort(satellite_id, client=client)
        except CdpAskClientError as exc:
            abort_info = _client_error_dict(exc)
    sweep_ephemeral(execution_id)
    return abort_info


def _client_error_dict(exc: CdpAskClientError) -> dict[str, Any]:
    out: dict[str, Any] = {"error": str(exc)}
    if exc.detail:
        out["detail"] = exc.detail
    if exc.status_code is not None:
        out["status_code"] = exc.status_code
    return out


def _is_retryable_overload_status(exc: CdpAskClientError) -> bool:
    """True when submit HTTP status is in the bounded overload retry set."""
    return exc.status_code in RETRYABLE_OVERLOAD_STATUS


def _is_overload_only_harvest(body: str) -> bool:
    """Detect Anthropic overload-only harvest bodies (529/503 API Error text).

    Requires every non-empty line to be overload-error shaped so a legitimate
    short harvest that merely quotes ``API Error: 529`` is not terminalized.
    """
    text = body.strip()
    if not text or len(text) > _OVERLOAD_ONLY_MAX_LEN:
        return False
    if not _OVERLOAD_ONLY_BODY_RE.search(text):
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not _OVERLOAD_ONLY_LINE_RE.match(stripped):
            return False
    return True


def _proof_rejects_overload(snapshot: dict[str, Any]) -> bool:
    """Fail closed when proof looks like upstream overload (incl. empty body + archive)."""
    body = str(snapshot.get("body") or "")
    if body:
        return _is_overload_only_harvest(body)
    return bool(snapshot.get("archive_uri"))


def _upstream_overloaded_extras(
    exc: CdpAskClientError | None = None,
    *,
    status_code: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build ``extras`` carrier for upstream overload terminal results."""
    out: dict[str, Any] = {"reason": UPSTREAM_OVERLOADED}
    if exc is not None:
        out.update(_client_error_dict(exc))
    elif status_code is not None:
        out["status_code"] = status_code
    out.update(extra)
    return out


def _submit_with_overload_retry(
    relay: CdpAskClient,
    submit_req: SubmitProjectAskRequest,
    *,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Submit once with a single bounded retry on HTTP 529/503 overload."""
    last_exc: CdpAskClientError | None = None
    for attempt in range(MAX_OVERLOAD_SUBMIT_ATTEMPTS):
        try:
            return relay.submit(submit_req, client=client)
        except CdpAskClientError as exc:
            last_exc = exc
            if (
                attempt + 1 < MAX_OVERLOAD_SUBMIT_ATTEMPTS
                and _is_retryable_overload_status(exc)
            ):
                sleep(SUBMIT_RETRY_BACKOFF_S)
                continue
            raise
    assert last_exc is not None
    raise last_exc


def result_from_snapshot(
    *,
    snapshot: dict[str, Any],
    execution_id: str,
    satellite_execution_id: str,
    prompt_uri: str,
    picker_model: str,
) -> CdpGenerateResult | None:
    """Project one poll snapshot to a terminal result, or None if still running.

    Reconcile uses this — must not call ``run_cdp_generate``. Fail-closed: running
    legs and transient poll errors (no ``status`` field) return None.
    """
    if snapshot.get("error") and "status" not in snapshot:
        return None

    proof_carry = _ProofCarry()
    proof_carry.absorb_status_snapshot(snapshot)
    carry = proof_carry.as_result_fields()

    if has_proof(snapshot):
        body = str(snapshot.get("body") or "")
        if _proof_rejects_overload(snapshot):
            return CdpGenerateResult(
                ok=False,
                body=body,
                execution_id=execution_id,
                satellite_execution_id=satellite_execution_id,
                prompt_uri=prompt_uri,
                picker_model=picker_model,
                stall_stage=UPSTREAM_OVERLOADED,
                error="upstream overload-only harvest body",
                extras=_upstream_overloaded_extras(),
                archive_uri=carry["archive_uri"],
                content_proof_uri=carry["content_proof_uri"],
                content_proof_sha256=carry["content_proof_sha256"],
            )
        return CdpGenerateResult(
            ok=True,
            body=body,
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            prompt_uri=prompt_uri,
            picker_model=picker_model,
            archive_uri=carry["archive_uri"],
            content_proof_uri=carry["content_proof_uri"],
            content_proof_sha256=carry["content_proof_sha256"],
        )

    if completed_without_proof(snapshot):
        return CdpGenerateResult(
            ok=False,
            body=str(snapshot.get("body") or ""),
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            prompt_uri=prompt_uri,
            picker_model=picker_model,
            stall_stage="completed_without_proof",
            error="satellite completed without archive_uri or content_proof",
            archive_uri=carry["archive_uri"],
            content_proof_uri=carry["content_proof_uri"],
            content_proof_sha256=carry["content_proof_sha256"],
        )

    if _terminal_failure(snapshot):
        return CdpGenerateResult(
            ok=False,
            body=str(snapshot.get("body") or ""),
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            prompt_uri=prompt_uri,
            picker_model=picker_model,
            stall_stage=snapshot.get("stall_stage"),
            error=str(snapshot.get("error") or snapshot.get("status")),
            archive_uri=carry["archive_uri"],
            content_proof_uri=carry["content_proof_uri"],
            content_proof_sha256=carry["content_proof_sha256"],
        )

    return None


def abort_cdp_generate(
    satellite_execution_id: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Abort in-flight satellite execution (separate failed path)."""
    try:
        return CdpAskClient().abort(satellite_execution_id, client=client)
    except CdpAskClientError as exc:
        return _client_error_dict(exc)


def run_cdp_generate(
    *,
    execution_id: str,
    model_id: str,
    prompt_uri: str | None = None,
    prompt_text: str | None = None,
    packet_path: str | None = None,
    sidecar_ref: str | None = None,
    max_wall_s: float = DEFAULT_MAX_WALL_S,
    no_progress_s: float = DEFAULT_NO_PROGRESS_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    converse: bool = True,
    no_project_uuid: bool = True,
    purpose: str = "ask",
    holder: str = "cdp-model-endpoint",
    harvest_source: HarvestSource = "auto",
    expected_size: ExpectedSize = "auto",
    download_output: bool = False,
    skills: list[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    client: httpx.Client | None = None,
    now: Callable[[], float] | None = None,
    ask_client: CdpAskClient | None = None,
    on_submitted: Callable[[str], None] | None = None,
) -> CdpGenerateResult:
    """Stage → native CDP submit → poll-to-proof (or stall/fail).

    Harvest/output knobs (``harvest_source``, ``expected_size``,
    ``download_output``) are forwarded on the native submit body — same fields
    as Stargate ``POST /api/v1/providers/cdp/ask``.

    ``purpose`` (default ``ask``): CDP registry/mission tag. ``operator-proxy`` /
    ``mission`` trigger skill-chip + seat-map inject on the satellite
    (``operator_proxy_mission.purpose_implies_mission``). Also matched when the
    prompt body declares ``purpose: operator-proxy``.

    ``skills`` (optional): catalog slugs prepended via
    ``stage_cdp_prompt_with_skills`` — ``shared_sync`` as leading ``/<slug>\\n``
    manifest lines; satellite attaches via **+ → Skills → pick** (never typed).
    Staging always merges ``reasoning-posture`` + ``frontier-reasoning-discipline``
    even when ``skills`` is omitted (light-bounded included).

    ``on_submitted`` receives the satellite-minted execution id the moment the
    submit is accepted. The satellite id space is disjoint from the caller's
    ``execution_id``, and it is the only handle the poll plane accepts — so
    callers that want in-flight discoverability must publish it here rather than
    on return (friction a:26175).
    """
    clock = now or time.monotonic
    picker = picker_from_model_id(model_id)
    relay = ask_client or CdpAskClient()
    mission_retain = is_operator_proxy_mission_purpose(purpose)
    try:
        staged = stage_cdp_prompt_with_skills(
            execution_id=execution_id,
            prompt_text=prompt_text,
            prompt_uri=prompt_uri,
            packet_path=packet_path,
            sidecar_ref=sidecar_ref,
            skills=skills if isinstance(skills, list) else None,
        )
    except CdpStagingError as exc:
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=prompt_uri or "",
            picker_model=picker,
            error=exc.reason,
            stall_stage=None,
            extras={"code": exc.code},
        )

    submit_req = SubmitProjectAskRequest(
        prompt_uri=staged.prompt_uri,
        holder=holder,
        purpose=purpose,
        model=picker,
        converse=converse,
        no_project_uuid=no_project_uuid,
        harvest_source=harvest_source,
        expected_size=expected_size,
        download_output=download_output,
    )
    try:
        submitted = _submit_with_overload_retry(
            relay,
            submit_req,
            client=client,
            sleep=sleep,
        )
    except CdpAskClientError as exc:
        sweep_ephemeral(execution_id)
        if _is_retryable_overload_status(exc):
            return CdpGenerateResult(
                ok=False,
                body="",
                execution_id=execution_id,
                satellite_execution_id=None,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage=UPSTREAM_OVERLOADED,
                error=str(exc),
                extras=_upstream_overloaded_extras(exc),
            )
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=staged.prompt_uri,
            picker_model=picker,
            error=str(exc),
            extras=_client_error_dict(exc),
        )

    sat_id = str(submitted.get("execution_id") or "")
    if not sat_id:
        sweep_ephemeral(execution_id)
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=staged.prompt_uri,
            picker_model=picker,
            error="satellite submit returned no execution_id",
        )

    if on_submitted is not None:
        on_submitted(sat_id)

    started = clock()
    last_fp = _progress_fingerprint(submitted)
    last_progress_at = started
    trace = ProgressTrace()
    trace.record(last_fp, at_s=0.0)
    polls = 0
    proof_carry = _ProofCarry()
    proof_carry.absorb_status_snapshot(submitted)

    while True:
        elapsed = clock() - started
        if elapsed > max_wall_s:
            if mission_retain:
                # Operator-proxy CSE must outlive the Stargate poller wall.
                # Do not Stop-click; keep polling until harvest proof, satellite
                # terminal, or an explicit continuity-handoff / human abort.
                started = clock()
                last_progress_at = clock()
                continue
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
            return CdpGenerateResult(
                ok=False,
                body="",
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage="wall_clock_exceeded",
                error=f"CDP generate exceeded max_wall_s={max_wall_s}",
                poll_snapshots=polls,
                extras={
                    "abort": abort_info,
                    "progress_trace": trace.as_dict(
                        now_s=elapsed, no_progress_s=no_progress_s
                    ),
                },
                **proof_carry.as_result_fields(),
            )

        sleep(poll_interval_s)
        try:
            snapshot = relay.poll(sat_id, client=client)
        except CdpAskClientError as exc:
            snapshot = _client_error_dict(exc)
        polls += 1
        if snapshot.get("error") and "status" not in snapshot:
            if clock() - last_progress_at > no_progress_s:
                if mission_retain:
                    last_progress_at = clock()
                    continue
                abort_info = _abort_then_sweep(
                    sat_id, execution_id, ask_client=relay, client=client
                )
                return CdpGenerateResult(
                    ok=False,
                    body="",
                    execution_id=execution_id,
                    satellite_execution_id=sat_id,
                    prompt_uri=staged.prompt_uri,
                    picker_model=picker,
                    stall_stage="no_progress",
                    error=str(snapshot.get("error")),
                    poll_snapshots=polls,
                    extras={
                        "abort": abort_info,
                        "progress_trace": trace.as_dict(
                            now_s=clock() - started, no_progress_s=no_progress_s
                        ),
                    },
                    **proof_carry.as_result_fields(),
                )
            continue

        proof_carry.absorb_status_snapshot(snapshot)

        fp = _progress_fingerprint(snapshot)
        if trace.record(fp, at_s=clock() - started):
            last_fp = fp
            last_progress_at = clock()

        if _has_proof(snapshot):
            body = str(snapshot.get("body") or "")
            if _proof_rejects_overload(snapshot):
                abort_info = _abort_then_sweep(
                    sat_id, execution_id, ask_client=relay, client=client
                )
                return CdpGenerateResult(
                    ok=False,
                    body=body,
                    execution_id=execution_id,
                    satellite_execution_id=sat_id,
                    prompt_uri=staged.prompt_uri,
                    picker_model=picker,
                    archive_uri=snapshot.get("archive_uri"),
                    content_proof_uri=snapshot.get("content_proof_uri"),
                    content_proof_sha256=snapshot.get("content_proof_sha256"),
                    stall_stage=UPSTREAM_OVERLOADED,
                    error="upstream overload-only harvest body",
                    poll_snapshots=polls,
                    extras=_upstream_overloaded_extras(abort=abort_info),
                )
            sweep_ephemeral(execution_id)
            return CdpGenerateResult(
                ok=True,
                body=body,
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                archive_uri=snapshot.get("archive_uri"),
                content_proof_uri=snapshot.get("content_proof_uri"),
                content_proof_sha256=snapshot.get("content_proof_sha256"),
                poll_snapshots=polls,
            )

        if _completed_without_proof(snapshot):
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
            return CdpGenerateResult(
                ok=False,
                body=str(snapshot.get("body") or ""),
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage="completed_without_proof",
                error="satellite completed without archive_uri or content_proof",
                poll_snapshots=polls,
                extras={"abort": abort_info},
                **proof_carry.as_result_fields(),
            )

        if _terminal_failure(snapshot):
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
            return CdpGenerateResult(
                ok=False,
                body=str(snapshot.get("body") or ""),
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage=snapshot.get("stall_stage"),
                error=str(snapshot.get("error") or snapshot.get("status")),
                poll_snapshots=polls,
                extras={"abort": abort_info},
                **proof_carry.as_result_fields(),
            )

        if not _post_idle(snapshot) and clock() - last_progress_at > no_progress_s:
            if mission_retain:
                # Idle between DIRECTIVE legs is normal on operator-proxy;
                # no_progress must not Stop-click the retained CSE.
                last_progress_at = clock()
                continue
            abort_info = _abort_then_sweep(
                sat_id, execution_id, ask_client=relay, client=client
            )
            return CdpGenerateResult(
                ok=False,
                body="",
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage="no_progress",
                error=(
                    f"CDP generate no progress for no_progress_s={no_progress_s} "
                    f"(completion_phase={snapshot.get('completion_phase')!r})"
                ),
                poll_snapshots=polls,
                extras={
                    "abort": abort_info,
                    "progress_trace": trace.as_dict(
                        now_s=clock() - started, no_progress_s=no_progress_s
                    ),
                },
                **proof_carry.as_result_fields(),
            )
