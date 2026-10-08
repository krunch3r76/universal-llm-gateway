"""Shared CDP model-endpoint adapter (team_dispatch + pipeline fronts).

Thin wrapper over the native CDP contract (``cdp_ask.client.CdpAskClient`` /
Stargate ``POST /api/v1/providers/cdp/ask``). Terminal complete only after
harvest proof (``content_proof`` or ``archive_uri``) or failed+stall_stage.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from cdp_ask.client import CdpAskClient, CdpAskClientError, project_ask_base_url
from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.unverifiable import (
    _CSE_URL_MARKER,
    WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED,
    failed_snapshot_fields,
    transport_miss_fields,
    wall_abort_unconfirmed,
)
from chat_harvest.chrome import is_chrome_only, is_prompt_echo

from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    stage_cdp_prompt_with_skills,
    sweep_ephemeral,
)
from claude_bundles.cdp_progress_trace import ProgressTrace
from claude_bundles.cdp_progress_trace import fingerprint as progress_fingerprint
from claude_bundles.chat_model_match import normalize_picker_request
from claude_bundles.overload_only_harvest import (
    _ERROR_BANNER_BODY_RE,
    _ERROR_BANNER_ONLY_MAX_LEN,
    is_error_banner_only_harvest,
)

DEFAULT_MAX_WALL_S = 1800
DEFAULT_NO_PROGRESS_S = 600
DEFAULT_POLL_INTERVAL_S = 2.0
CDP_SUBSTRATE = "web-anthropic-cdp"
# Bus endpoint address (identity doctrine) — ¬ mint a separate "cdp" seat.
# CDP is substrate/session association (CDP_SUBSTRATE, execution_id, registration).
CDP_REPLY_FROM = "web-anthropic"

# Operator-proxy / mission CSE must stay live across long Auto legs. Poller
# wall / no-progress must NOT Stop-click the page. Clean CSE break is only for
# continuity handoff (after a new CSE is confirmed) or rare human escalation —
# never for max_wall_s / no_progress_s alone.

# Phases after the page goes idle: the satellite is resolving harvest (Cowork
# Output download, archive write) and emits no per-sample progress, so the
# no-progress fingerprint necessarily freezes. ``max_wall_s`` bounds these as
# seconds since the last observed fingerprint progress (not cumulative elapsed).
POST_IDLE_PHASES = frozenset(
    {"turn_idle", "content_proof", "archiving", "awaiting_wake"}
)

RETRYABLE_OVERLOAD_STATUS = frozenset({529, 503})
SUBMIT_RETRY_BACKOFF_S = 5.0
MAX_OVERLOAD_SUBMIT_ATTEMPTS = 2
UPSTREAM_OVERLOADED = "upstream_overloaded"
WEEKLY_LIMIT = "weekly_limit"
_is_overload_only_harvest = is_error_banner_only_harvest
_WEEKLY_LIMIT_RE = re.compile(
    r"weekly\s+limit|hit\s+your\s+.+\s*limit|you've\s+hit\s+your",
    re.IGNORECASE,
)
_WEEKLY_LIMIT_STOP_RE = re.compile(
    r"hit\s+your\s+.+\s*limit|you'?ve\s+hit\s+your|limit\s+reached|reached\s+your\s+.+\s*limit",
    re.IGNORECASE,
)
_WEEKLY_LIMIT_APPROACHING_RE = re.compile(
    r"approaching|nearing|close\s+to",
    re.IGNORECASE,
)
_WEEKLY_LIMIT_WARNING_MAX_LEN = 200

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


def _is_user_prompt_echo_body(body: str) -> bool:
    """True when harvested text is Cowork user-turn chrome (a:27801 / 083e6e4a)."""
    return is_prompt_echo(body)


def _is_chrome_only_body(body: str) -> bool:
    """True when body is skill-chip / manifest chrome without assistant prose."""
    return is_chrome_only(body)


def _review_lacks_verdict(purpose: str, body: str) -> bool:
    """True when job=delivery-review and body has no parseable verdict (a:37156)."""
    if (purpose or "").strip().lower() != "review":
        return False
    from review_verdict.grammar import has_parseable_verdict

    return not has_parseable_verdict(body)


def _has_unresolved_artifact_card(snapshot: dict[str, Any]) -> bool:
    """True when harvest observed in-chat artifact cards without resolved body."""
    return bool(snapshot.get("artifact_cards_unresolved"))


def has_proof(snapshot: dict[str, Any]) -> bool:
    """True when snapshot carries terminal harvest proof (shared poll + reconcile gate).

    Success requires **either**:
    - **Chat path:** non-echo assistant body with populated ``attested_model``, or
    - **Outputs path:** ``harvest_provenance`` in ``output-file`` | ``cortex-uri``
      with ``content_proof_uri`` (F6 semantics).

    ``archive_uri`` alone, ``completion_phase=terminal`` alone, and prompt-echo
    archives (``You said:`` with ``attested_model: None``) are insufficient.
    Unresolved in-chat artifact cards (detected but body not extracted) also
    fail proof — distinct from ``_is_chrome_only_body``.
    """
    phase = str(snapshot.get("completion_phase") or "")
    if phase == "failed":
        return False

    if _has_unresolved_artifact_card(snapshot):
        return False

    provenance = snapshot.get("harvest_provenance")
    if provenance in {"output-file", "cortex-uri"}:
        return bool(snapshot.get("content_proof_uri"))

    if snapshot.get("content_proof_uri") and phase in {"content_proof", "archiving"}:
        return True

    body = str(snapshot.get("body") or "")
    if _is_user_prompt_echo_body(body) or _is_chrome_only_body(body):
        return False

    attested = snapshot.get("attested_model")
    if provenance in {"chat", "chat-large", "artifact-card"}:
        return bool(attested and body.strip())

    if attested and body.strip():
        return True

    return False


_has_proof = has_proof


@dataclass
class _ProofCarry:
    """Last-known harvest proof fields from status-bearing poll snapshots."""

    archive_uri: str | None = None
    content_proof_uri: str | None = None
    content_proof_sha256: str | None = None
    url: str | None = None
    registration_id: str | None = None

    def absorb_status_snapshot(self, snapshot: dict[str, Any]) -> None:
        if "status" not in snapshot:
            return
        snap_url = str(snapshot.get("url") or "")
        if _CSE_URL_MARKER in snap_url:
            self.url = snap_url
        reg = snapshot.get("registration_id")
        if reg is not None:
            self.registration_id = str(reg)
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

    def carry_extras(self) -> dict[str, str]:
        """Surface latched CSE URL + registration for terminal proof relay."""
        extras: dict[str, str] = {}
        if self.url:
            extras["chat_url"] = self.url
        if self.registration_id:
            extras["registration_id"] = self.registration_id
        return extras


_progress_fingerprint = progress_fingerprint


def _maybe_invoke_url_bound(
    *,
    proof_carry: _ProofCarry,
    snapshot: dict[str, Any],
    execution_id: str,
    on_url_bound: Callable[[str, str | None, int], None] | None,
    url_bound_emitted: set[tuple[str, str]],
    distinct_cse_urls: list[str],
) -> None:
    """Fire ``on_url_bound`` once per ``(execution_id, chat_url)`` observation."""
    if on_url_bound is None:
        return
    url = proof_carry.url
    if not url or _CSE_URL_MARKER not in url:
        return
    key = (execution_id, url)
    if key in url_bound_emitted:
        return
    url_bound_emitted.add(key)
    if url not in distinct_cse_urls:
        distinct_cse_urls.append(url)
    reg = proof_carry.registration_id or snapshot.get("registration_id")
    reg_str = str(reg) if reg is not None else None
    on_url_bound(url, reg_str, len(distinct_cse_urls))


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


def _missing_proof_error(snapshot: dict[str, Any]) -> str:
    """Name the proof leg actually missing — not archive absence on chat provenance."""
    provenance = snapshot.get("harvest_provenance")
    if provenance in {"output-file", "cortex-uri"}:
        return (
            "outputs harvest lacks content_proof_uri "
            "(harvest_provenance=output-file|cortex-uri)"
        )
    return (
        "chat harvest lacks attested_model (archive_uri alone insufficient — AC-S1-b)"
    )


def _deliverable_unproven_extras(carry: dict[str, str | None]) -> dict[str, Any]:
    """When proof-carry holds a deliverable URI, name recovery for harvest-not-redispatch."""
    if carry.get("archive_uri") or carry.get("content_proof_uri"):
        return {
            "deliverable_present_unproven": True,
            "recovery": (
                "poll satellite / read archive_uri; verify body manually; "
                "do not blind re-dispatch"
            ),
        }
    return {}


_completed_without_proof = completed_without_proof


def _abort_then_sweep(
    satellite_id: str | None,
    execution_id: str,
    *,
    ask_client: CdpAskClient | None = None,
    client: httpx.Client | None = None,
    retain_cse: bool = False,
    retain_reason: str | None = None,
    wall_expiry: bool = False,
) -> dict[str, Any]:
    """Abort satellite (Stop-click) then sweep staging — unless *retain_cse*.

    When ``retain_cse`` is true (operator-proxy / mission, or unverifiable-class
    stall after compose-attest), skip ``abort`` so the Cowork page keeps
    streaming; only ephemeral prompt staging is swept.

    ``wall_expiry`` still attempts Stop-click. An unconfirmed abort (error,
    non-2xx, or empty body while *satellite_id* is set) stamps ``retain_cse``
    and ``abort_unconfirmed`` so the caller keeps the CSE and does not grade
    the wall as death.
    """
    abort_info: dict[str, Any] = {}
    if retain_cse:
        abort_info = {
            "abort_skipped": True,
            "reason": retain_reason or "operator_proxy_cse_retain",
        }
        sweep_ephemeral(execution_id)
        return abort_info
    if satellite_id:
        try:
            relay = ask_client or CdpAskClient()
            abort_info = relay.abort(satellite_id, client=client)
        except CdpAskClientError as exc:
            abort_info = _client_error_dict(exc)
    if wall_expiry and wall_abort_unconfirmed(abort_info, sat_id=satellite_id):
        abort_info = {
            **abort_info,
            "retain_cse": True,
            "abort_unconfirmed": True,
            "reason": abort_info.get("reason") or "abort_unconfirmed",
        }
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


def _proof_rejects_overload(snapshot: dict[str, Any]) -> bool:
    """Fail closed when proof looks like upstream overload (incl. empty body + archive)."""
    body = str(snapshot.get("body") or "")
    if body:
        return _is_overload_only_harvest(body)
    return bool(snapshot.get("archive_uri"))


def _joined_error_banner(snapshot: dict[str, Any]) -> str:
    return " ".join(
        str(snapshot.get(k) or "")
        for k in ("error_banner_text", "error_banner_match", "error_banner")
    )


def _classify_weekly_limit_banner(
    banner: str,
) -> Literal["stop", "warning", "other"] | None:
    """Classify joined harvest banner text when it mentions weekly limit."""
    if not _WEEKLY_LIMIT_RE.search(banner):
        return None
    if _WEEKLY_LIMIT_STOP_RE.search(banner):
        return "stop"
    if _WEEKLY_LIMIT_APPROACHING_RE.search(banner) and re.search(
        r"weekly\s+limit", banner, re.IGNORECASE
    ):
        return "warning"
    return "other"


def _weekly_limit_warning_extra(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Surface approaching-limit notice on successful grades (operator paging)."""
    banner = _joined_error_banner(snapshot)
    if _classify_weekly_limit_banner(banner) != "warning":
        return {}
    text = banner.strip()
    if len(text) > _WEEKLY_LIMIT_WARNING_MAX_LEN:
        text = text[:_WEEKLY_LIMIT_WARNING_MAX_LEN]
    return {"weekly_limit_warning": text}


_GRADE_TRACE_RULES = frozenset(
    {
        "weekly_limit_stop",
        "weekly_limit_short_body",
        "weekly_limit_warning",
        "overload_only",
        "completed_without_proof",
        "terminal_failure",
        "wall_clock",
        "no_progress",
        "transport_miss",
        "staging_error",
        "submit_overload",
        "submit_error",
        "submit_no_id",
    }
)
_GRADE_TRACE_BANNER_CAP = 500
_GRADE_TRACE_MATCH_CAP = 120
_GRADE_TRACE_SHORT_CAP = 64
_GRADE_TRACE_ERROR_CAP = 300
_GRADE_TRACE_MAX_BYTES = 1500
_GRADE_TRACE_DROP_ORDER = (
    "error",
    "matched_span",
    "banner_match",
    "harvest_provenance",
    "completion_phase",
    "status",
    "stall_stage",
)


def _collapse_trace_text(value: str) -> str:
    """Collapse whitespace and replace backticks so a banner stays one scalar."""
    return re.sub(r"\s+", " ", value).strip().replace("`", "'")


def _trace_utf8_len(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def _fit_grade_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop low-priority scalars until the trace is under 1.5KB."""
    out = dict(payload)
    if _trace_utf8_len(out) <= _GRADE_TRACE_MAX_BYTES:
        return out
    for key in _GRADE_TRACE_DROP_ORDER:
        out.pop(key, None)
        if _trace_utf8_len(out) <= _GRADE_TRACE_MAX_BYTES:
            return out
    text = out.get("banner_text")
    if isinstance(text, str) and text:
        lo = 0
        hi = len(text)
        best = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            trial = dict(out)
            trial["banner_text"] = text[:mid]
            trial["banner_text_truncated"] = True
            if _trace_utf8_len(trial) <= _GRADE_TRACE_MAX_BYTES:
                best = text[:mid]
                lo = mid + 1
            else:
                hi = mid - 1
        out["banner_text"] = best
        out["banner_text_truncated"] = True
    if _trace_utf8_len(out) > _GRADE_TRACE_MAX_BYTES:
        rule = out.get("rule")
        return {"rule": rule if isinstance(rule, str) else "trace_error"}
    return out


def _matched_span_for_rule(snapshot: dict[str, Any], rule: str) -> str:
    """Return the regex match text for *rule*, never the pattern source."""
    if rule in {"weekly_limit_stop", "weekly_limit_short_body", "weekly_limit_warning"}:
        pattern = (
            _WEEKLY_LIMIT_STOP_RE if rule == "weekly_limit_stop" else _WEEKLY_LIMIT_RE
        )
        if rule == "weekly_limit_warning":
            pattern = _WEEKLY_LIMIT_APPROACHING_RE
    elif rule == "overload_only":
        pattern = _ERROR_BANNER_BODY_RE
    else:
        return ""
    haystacks: list[str] = []
    for key in ("error_banner_text", "error_banner_match", "error_banner", "body"):
        raw = snapshot.get(key)
        if isinstance(raw, str) and raw:
            haystacks.append(raw)
    for hay in haystacks:
        found = pattern.search(hay)
        if found:
            return _collapse_trace_text(found.group(0))[:_GRADE_TRACE_MATCH_CAP]
        if rule == "weekly_limit_stop":
            fallback = _WEEKLY_LIMIT_RE.search(hay)
            if fallback:
                return _collapse_trace_text(fallback.group(0))[:_GRADE_TRACE_MATCH_CAP]
    return ""


def _grade_trace_fields(
    snapshot: dict[str, Any],
    *,
    rule: str,
    stall_stage: str | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rule": rule if rule in _GRADE_TRACE_RULES else rule[:_GRADE_TRACE_SHORT_CAP],
    }
    raw_banner = snapshot.get("error_banner_text")
    if isinstance(raw_banner, str):
        collapsed = _collapse_trace_text(raw_banner)
        out["banner_text_len"] = len(raw_banner)
        out["banner_text_truncated"] = len(collapsed) > _GRADE_TRACE_BANNER_CAP
        out["banner_text"] = collapsed[:_GRADE_TRACE_BANNER_CAP]
    else:
        out["banner_text"] = None
        out["banner_text_len"] = 0
        out["banner_text_truncated"] = False
    raw_match = snapshot.get("error_banner_match")
    if isinstance(raw_match, str) and raw_match.strip():
        out["banner_match"] = _collapse_trace_text(raw_match)[:_GRADE_TRACE_MATCH_CAP]
    span = _matched_span_for_rule(snapshot, rule)
    if span:
        out["matched_span"] = span
    stage = stall_stage if stall_stage is not None else snapshot.get("stall_stage")
    if isinstance(stage, str) and stage:
        out["stall_stage"] = stage[:_GRADE_TRACE_SHORT_CAP]
    for key in ("status", "completion_phase", "harvest_provenance"):
        raw = snapshot.get(key)
        if isinstance(raw, str) and raw:
            out[key] = raw[:_GRADE_TRACE_SHORT_CAP]
    raw_error = snapshot.get("error")
    if isinstance(raw_error, str) and raw_error:
        out["error"] = raw_error[:_GRADE_TRACE_ERROR_CAP]
    return out


def _grade_trace(
    snapshot: Any,
    *,
    rule: str,
    stall_stage: str | None = None,
) -> dict[str, Any]:
    """Bounded recording of the snapshot that a grade already decided.

    Reads only *snapshot*. No store and no I/O. Grading predicates must not
    read the returned dict. On any internal failure returns ``rule`` plus
    ``trace_error`` and does not raise.
    """
    try:
        if not isinstance(rule, str):
            raise TypeError("rule")
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot")
        return _fit_grade_trace(
            _grade_trace_fields(snapshot, rule=rule, stall_stage=stall_stage)
        )
    except Exception as exc:
        safe_rule = (
            rule[:_GRADE_TRACE_SHORT_CAP] if isinstance(rule, str) else "trace_error"
        )
        return {"rule": safe_rule, "trace_error": type(exc).__name__}


def _bound_borne_grade_trace(
    borne: dict[str, Any], *, rule: str | None
) -> dict[str, Any]:
    """Copy a snapshot-borne trace without promoting it to grader inputs."""
    clean: dict[str, Any] = {}
    for key, value in borne.items():
        if isinstance(value, (list, dict, tuple, set)):
            continue
        name = str(key)[:_GRADE_TRACE_SHORT_CAP]
        if isinstance(value, str):
            text = value.replace("`", "'")
            if name == "banner_text":
                text = text[:_GRADE_TRACE_BANNER_CAP]
            elif name in {"banner_match", "matched_span", "error"}:
                cap = (
                    _GRADE_TRACE_ERROR_CAP
                    if name == "error"
                    else _GRADE_TRACE_MATCH_CAP
                )
                text = text[:cap]
            elif name in {
                "rule",
                "stall_stage",
                "status",
                "completion_phase",
                "harvest_provenance",
                "trace_error",
            }:
                text = text[:_GRADE_TRACE_SHORT_CAP]
            clean[name] = text
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[name] = value
    if "rule" not in clean and rule:
        clean["rule"] = rule[:_GRADE_TRACE_SHORT_CAP]
    return _fit_grade_trace(clean)


def _attach_grade_trace(
    extras: dict[str, Any],
    snapshot: Any,
    *,
    rule: str | None,
    stall_stage: str | None,
) -> dict[str, Any]:
    """Merge ``grade_trace`` into *extras*. A borne trace is copied, not graded."""
    try:
        out = dict(extras)
    except Exception:
        out = {}
    try:
        borne = snapshot.get("grade_trace") if isinstance(snapshot, dict) else None
        if isinstance(borne, dict) and borne:
            out["grade_trace"] = _bound_borne_grade_trace(borne, rule=rule)
            return out
        if not rule:
            return out
        snap = snapshot if isinstance(snapshot, dict) else {}
        out["grade_trace"] = _grade_trace(snap, rule=rule, stall_stage=stall_stage)
        return out
    except Exception as exc:
        safe_rule = (
            rule[:_GRADE_TRACE_SHORT_CAP] if isinstance(rule, str) else "trace_error"
        )
        out["grade_trace"] = {"rule": safe_rule, "trace_error": type(exc).__name__}
        return out


def _weekly_limit_grade_rule(snapshot: dict[str, Any]) -> str:
    """Name the weekly-limit branch already chosen. Does not decide the grade."""
    banner = _joined_error_banner(snapshot)
    kind = _classify_weekly_limit_banner(banner)
    if kind == "warning":
        body = str(snapshot.get("body") or "").strip()
        if (
            body
            and len(body) <= _ERROR_BANNER_ONLY_MAX_LEN
            and _WEEKLY_LIMIT_RE.search(body)
        ):
            return "weekly_limit_short_body"
        return "weekly_limit_warning"
    if kind in {"stop", "other"}:
        return "weekly_limit_stop"
    return "weekly_limit_short_body"


def _proof_rejects_weekly_limit(snapshot: dict[str, Any]) -> bool:
    """True when harvest is a product quota banner, not a seat reply."""
    banner = _joined_error_banner(snapshot)
    if _WEEKLY_LIMIT_RE.search(banner):
        cls = _classify_weekly_limit_banner(banner)
        if cls != "warning":
            return True
        # Sticky approaching banner: still run short-body check (review B1).
    body = str(snapshot.get("body") or "")
    stripped = body.strip()
    if stripped and len(stripped) <= _ERROR_BANNER_ONLY_MAX_LEN:
        return bool(_WEEKLY_LIMIT_RE.search(body))
    return False


def _weekly_limit_result(
    *,
    body: str,
    execution_id: str,
    satellite_execution_id: str | None,
    prompt_uri: str,
    picker_model: str,
    carry: dict[str, str | None] | None = None,
    poll_snapshots: int = 0,
    abort: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> CdpGenerateResult:
    """Grade weekly-limit banner as ``cdp FAILED`` (mark: banner_not_a_seat)."""
    carry = carry or {}
    snap = snapshot if isinstance(snapshot, dict) else {}
    extras: dict[str, Any] = {"mark": "banner_not_a_seat", "reason": WEEKLY_LIMIT}
    if abort is not None:
        extras["abort"] = abort
    extras = _attach_grade_trace(
        extras,
        snap,
        rule=_weekly_limit_grade_rule(snap),
        stall_stage=WEEKLY_LIMIT,
    )
    return CdpGenerateResult(
        ok=False,
        body=body,
        execution_id=execution_id,
        satellite_execution_id=satellite_execution_id,
        prompt_uri=prompt_uri,
        picker_model=picker_model,
        stall_stage=WEEKLY_LIMIT,
        error="product weekly-limit banner (not a seat reply)",
        extras=extras,
        archive_uri=carry.get("archive_uri"),
        content_proof_uri=carry.get("content_proof_uri"),
        content_proof_sha256=carry.get("content_proof_sha256"),
        poll_snapshots=poll_snapshots,
    )


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
    purpose: str | None = None,
) -> CdpGenerateResult | None:
    """Project one poll snapshot to a terminal result, or None if still running.

    Reconcile uses this — must not call ``run_cdp_generate``. Fail-closed: running
    legs and transient poll errors (no ``status`` field) return None.

    ``job=delivery-review`` without a parseable verdict returns None so reconcile
    cannot race the worker keep-poll (a:37156 F1 / dogfood WITHHOLD).

    When the snapshot already carries ``grade_trace``, that dict is copied into
    extras and is not a grader input. Fresh traces record the snapshot in hand
    under the same key after the grade is chosen.
    """
    if snapshot.get("error") and "status" not in snapshot:
        return None

    proof_carry = _ProofCarry()
    proof_carry.absorb_status_snapshot(snapshot)
    carry = proof_carry.as_result_fields()

    if has_proof(snapshot):
        body = str(snapshot.get("body") or "")
        if _review_lacks_verdict(purpose or "", body):
            return None
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
                extras=_attach_grade_trace(
                    _upstream_overloaded_extras(),
                    snapshot,
                    rule="overload_only",
                    stall_stage=UPSTREAM_OVERLOADED,
                ),
                archive_uri=carry["archive_uri"],
                content_proof_uri=carry["content_proof_uri"],
                content_proof_sha256=carry["content_proof_sha256"],
            )
        if _proof_rejects_weekly_limit(snapshot):
            return _weekly_limit_result(
                body=body,
                execution_id=execution_id,
                satellite_execution_id=satellite_execution_id,
                prompt_uri=prompt_uri,
                picker_model=picker_model,
                carry=carry,
                snapshot=snapshot,
            )
        warning_extras = _weekly_limit_warning_extra(snapshot)
        ok_extras = _attach_grade_trace(
            warning_extras,
            snapshot,
            rule="weekly_limit_warning" if warning_extras else None,
            stall_stage=None,
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
            extras=ok_extras,
        )

    if completed_without_proof(snapshot):
        body = str(snapshot.get("body") or "")
        if _is_chrome_only_body(body) or _review_lacks_verdict(purpose or "", body):
            return None
        return CdpGenerateResult(
            ok=False,
            body=body,
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            prompt_uri=prompt_uri,
            picker_model=picker_model,
            stall_stage="completed_without_proof",
            error=_missing_proof_error(snapshot),
            archive_uri=carry["archive_uri"],
            content_proof_uri=carry["content_proof_uri"],
            content_proof_sha256=carry["content_proof_sha256"],
            extras=_attach_grade_trace(
                _deliverable_unproven_extras(carry),
                snapshot,
                rule="completed_without_proof",
                stall_stage="completed_without_proof",
            ),
        )

    if _terminal_failure(snapshot):
        fields = failed_snapshot_fields(snapshot)
        return CdpGenerateResult(
            ok=False,
            body=str(snapshot.get("body") or ""),
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            prompt_uri=prompt_uri,
            picker_model=picker_model,
            stall_stage=fields["stall_stage"],
            error=fields["error"],
            archive_uri=carry["archive_uri"],
            content_proof_uri=carry["content_proof_uri"],
            content_proof_sha256=carry["content_proof_sha256"],
            extras=_attach_grade_trace(
                dict(fields["extras"]),
                snapshot,
                rule="terminal_failure",
                stall_stage=fields["stall_stage"],
            ),
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
    project_uuid: str | None = None,
    purpose: str | None = None,
    mission_kind: str | None = None,
    parent_thread: str | None = None,
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
    on_url_bound: Callable[[str, str | None, int], None] | None = None,
) -> CdpGenerateResult:
    """Stage → native CDP submit → poll-to-proof (or stall/fail).

    Harvest/output knobs (``harvest_source``, ``expected_size``,
    ``download_output``) are forwarded on the native submit body — same fields
    as Stargate ``POST /api/v1/providers/cdp/ask``.

    ``purpose`` (default ``ask``): CDP registry/mission tag. ``operator-proxy`` /
    ``mission`` trigger skill-chip + seat-map inject on the satellite
    (``operator_proxy_mission.purpose_implies_mission``). A prompt line that
    names ``purpose`` as ``operator-proxy`` does not. Retain uses ``StagedPrompt.mission``
    captured at staging.

    ``mission_kind`` / ``parent_thread``: Chrome-host lineage claims on the
    registry row (``root|hop|side|parallel`` plus bus parent lane id). These
    are observations only — bus ``lane_role`` / ``association_id`` proof is
    written later by hub-side ``cse_provenance_enrich`` when a lane thread is
    known. Hop succession sets ``mission_kind=hop`` and
    ``parent_thread=<private lane>``.

    ``skills`` (optional): catalog slugs sealed via
    ``stage_cdp_prompt_with_skills``. The marker plus ``send_prompt`` induction
    delivers ``shared_sync`` slugs; the inline skills block carries everything
    else. Slash lines appear only for legacy prompts without the marker.
    Staging always merges ``reasoning-posture`` even when ``skills`` is omitted
    (none included).

    ``on_submitted`` receives the satellite-minted execution id the moment the
    submit is accepted. The satellite id space is disjoint from the caller's
    ``execution_id``, and it is the only handle the poll plane accepts — so
    callers that want in-flight discoverability must publish it here rather than
    on return (friction a:26175).

    ``on_url_bound`` receives ``(chat_url, registration_id, seating_ordinal)``
    on first observation of each distinct CSE URL during this run. Default ``None``
    preserves pre-change behavior (no seated event).

    ``max_wall_s`` measures seconds since the last observed fingerprint progress
    (a ``trace.record`` delta resets the wall origin beside ``last_progress_at``).
    ``no_progress_s`` still bounds frozen fingerprints outside post-idle phases.
    Progress-trace diagnostics use a run-relative ``trace_started`` origin so
    ``history[*].at_s`` stays monotonic across wall resets.
    """
    clock = now or time.monotonic
    picker = picker_from_model_id(model_id)
    relay = ask_client or CdpAskClient()
    try:
        staged = stage_cdp_prompt_with_skills(
            execution_id=execution_id,
            prompt_text=prompt_text,
            prompt_uri=prompt_uri,
            packet_path=packet_path,
            sidecar_ref=sidecar_ref,
            skills=skills if isinstance(skills, list) else None,
            purpose=purpose,
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
            extras=_attach_grade_trace(
                {"code": exc.code},
                {"error": exc.reason},
                rule="staging_error",
                stall_stage=None,
            ),
        )
    mission_retain = staged.mission

    submit_purpose = purpose if purpose is not None else "ask"
    submit_req = SubmitProjectAskRequest(
        prompt_uri=staged.prompt_uri,
        holder=holder,
        purpose=submit_purpose,
        mission_kind=mission_kind,
        parent_thread=parent_thread,
        model=picker,
        converse=converse,
        no_project_uuid=no_project_uuid
        if project_uuid is None
        else not bool(project_uuid),
        project_uuid=project_uuid or "",
        harvest_source=harvest_source,
        expected_size=expected_size,
        download_output=download_output,
        stargate_execution_id=execution_id,
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
                extras=_attach_grade_trace(
                    _upstream_overloaded_extras(exc),
                    {"error": str(exc)},
                    rule="submit_overload",
                    stall_stage=UPSTREAM_OVERLOADED,
                ),
            )
        return CdpGenerateResult(
            ok=False,
            body="",
            execution_id=execution_id,
            satellite_execution_id=None,
            prompt_uri=staged.prompt_uri,
            picker_model=picker,
            error=str(exc),
            extras=_attach_grade_trace(
                _client_error_dict(exc),
                {"error": str(exc)},
                rule="submit_error",
                stall_stage=None,
            ),
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
            extras=_attach_grade_trace(
                {},
                {"error": "satellite submit returned no execution_id"},
                rule="submit_no_id",
                stall_stage=None,
            ),
        )

    if on_submitted is not None:
        on_submitted(sat_id)

    started = clock()
    trace_started = started
    last_fp = _progress_fingerprint(submitted)
    last_progress_at = started
    trace = ProgressTrace()
    trace.record(last_fp, at_s=0.0)
    polls = 0
    transport_missed = False
    proof_carry = _ProofCarry()
    proof_carry.absorb_status_snapshot(submitted)
    url_bound_emitted: set[tuple[str, str]] = set()
    distinct_cse_urls: list[str] = []
    last_snapshot: dict[str, Any] = submitted
    _maybe_invoke_url_bound(
        proof_carry=proof_carry,
        snapshot=submitted,
        execution_id=execution_id,
        on_url_bound=on_url_bound,
        url_bound_emitted=url_bound_emitted,
        distinct_cse_urls=distinct_cse_urls,
    )

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
                sat_id,
                execution_id,
                ask_client=relay,
                client=client,
                wall_expiry=True,
            )
            since_last_progress_s = clock() - last_progress_at
            stall_stage = (
                WALL_CLOCK_EXCEEDED_ABORT_UNCONFIRMED
                if abort_info.get("abort_unconfirmed")
                else "wall_clock_exceeded"
            )
            return CdpGenerateResult(
                ok=False,
                body="",
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage=stall_stage,
                error=(
                    f"CDP generate no progress for max_wall_s={max_wall_s} "
                    f"(since_last_progress_s={since_last_progress_s:.1f})"
                ),
                poll_snapshots=polls,
                extras=_attach_grade_trace(
                    {
                        "abort": abort_info,
                        "since_last_progress_s": since_last_progress_s,
                        "progress_trace": trace.as_dict(
                            now_s=clock() - trace_started, no_progress_s=no_progress_s
                        ),
                        **proof_carry.carry_extras(),
                    },
                    last_snapshot,
                    rule="wall_clock",
                    stall_stage=stall_stage,
                ),
                **proof_carry.as_result_fields(),
            )

        sleep(poll_interval_s)
        poll_kwargs: dict[str, Any] = {"client": client}
        if transport_missed and proof_carry.url:
            # A satellite store miss can also lose registry/provenance; this
            # poller may be the only holder of the URL its recovery harvest needs.
            poll_kwargs["chat_url"] = proof_carry.url
        try:
            snapshot = relay.poll(sat_id, **poll_kwargs)
        except CdpAskClientError as exc:
            snapshot = _client_error_dict(exc)
        last_snapshot = snapshot
        polls += 1
        transport_missed = bool(snapshot.get("error")) and "status" not in snapshot
        if transport_missed:
            if clock() - last_progress_at > no_progress_s:
                if mission_retain:
                    last_progress_at = clock()
                    continue
                since_last_progress_s = clock() - last_progress_at
                fields = transport_miss_fields(
                    str(snapshot.get("error")),
                    proof_carry.url,
                    satellite_execution_id=sat_id,
                )
                abort_info = _abort_then_sweep(
                    sat_id,
                    execution_id,
                    ask_client=relay,
                    client=client,
                    retain_cse=bool(fields["unverifiable"]) or mission_retain,
                    retain_reason="operator_proxy_cse_retain"
                    if mission_retain
                    else fields["retain_reason"],
                )
                return CdpGenerateResult(
                    ok=False,
                    body="",
                    execution_id=execution_id,
                    satellite_execution_id=sat_id,
                    prompt_uri=staged.prompt_uri,
                    picker_model=picker,
                    stall_stage=fields["stall_stage"],
                    error=fields["error"],
                    poll_snapshots=polls,
                    extras=_attach_grade_trace(
                        {
                            "abort": abort_info,
                            "since_last_progress_s": since_last_progress_s,
                            "progress_trace": trace.as_dict(
                                now_s=clock() - trace_started,
                                no_progress_s=no_progress_s,
                            ),
                            **fields["extras"],
                            **proof_carry.carry_extras(),
                        },
                        snapshot,
                        rule="transport_miss",
                        stall_stage=fields["stall_stage"],
                    ),
                    **proof_carry.as_result_fields(),
                )
            continue

        proof_carry.absorb_status_snapshot(snapshot)
        _maybe_invoke_url_bound(
            proof_carry=proof_carry,
            snapshot=snapshot,
            execution_id=execution_id,
            on_url_bound=on_url_bound,
            url_bound_emitted=url_bound_emitted,
            distinct_cse_urls=distinct_cse_urls,
        )

        fp = _progress_fingerprint(snapshot)
        if trace.record(fp, at_s=clock() - trace_started):
            last_fp = fp
            last_progress_at = clock()
            started = clock()

        if _has_proof(snapshot):
            body = str(snapshot.get("body") or "")
            # job=delivery-review: skill-induction / mid-tool prose is not proof
            # (a:37156 / a:37034). Keep polling like chrome-only completed.
            if _review_lacks_verdict(purpose, body):
                sleep(poll_interval_s)
                continue
            if _proof_rejects_overload(snapshot):
                abort_info = _abort_then_sweep(
                    sat_id,
                    execution_id,
                    ask_client=relay,
                    client=client,
                    retain_cse=mission_retain,
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
                    extras=_attach_grade_trace(
                        _upstream_overloaded_extras(abort=abort_info),
                        snapshot,
                        rule="overload_only",
                        stall_stage=UPSTREAM_OVERLOADED,
                    ),
                )
            if _proof_rejects_weekly_limit(snapshot):
                abort_info = _abort_then_sweep(
                    sat_id,
                    execution_id,
                    ask_client=relay,
                    client=client,
                    retain_cse=mission_retain,
                )
                return _weekly_limit_result(
                    body=body,
                    execution_id=execution_id,
                    satellite_execution_id=sat_id,
                    prompt_uri=staged.prompt_uri,
                    picker_model=picker,
                    carry={
                        "archive_uri": snapshot.get("archive_uri"),
                        "content_proof_uri": snapshot.get("content_proof_uri"),
                        "content_proof_sha256": snapshot.get("content_proof_sha256"),
                    },
                    poll_snapshots=polls,
                    abort=abort_info,
                    snapshot=snapshot,
                )
            sweep_ephemeral(execution_id)
            warning_extras = _weekly_limit_warning_extra(snapshot)
            ok_extras = proof_carry.carry_extras()
            ok_extras.update(warning_extras)
            ok_extras = _attach_grade_trace(
                ok_extras,
                snapshot,
                rule="weekly_limit_warning" if warning_extras else None,
                stall_stage=None,
            )
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
                extras=ok_extras,
            )

        if _completed_without_proof(snapshot):
            body = str(snapshot.get("body") or "")
            if _is_chrome_only_body(body) or _review_lacks_verdict(purpose, body):
                sleep(poll_interval_s)
                continue
            abort_info = _abort_then_sweep(
                sat_id,
                execution_id,
                ask_client=relay,
                client=client,
                retain_cse=mission_retain,
            )
            carry_fields = proof_carry.as_result_fields()
            extras: dict[str, Any] = {"abort": abort_info, **proof_carry.carry_extras()}
            extras.update(_deliverable_unproven_extras(carry_fields))
            extras = _attach_grade_trace(
                extras,
                snapshot,
                rule="completed_without_proof",
                stall_stage="completed_without_proof",
            )
            return CdpGenerateResult(
                ok=False,
                body=str(snapshot.get("body") or ""),
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage="completed_without_proof",
                error=_missing_proof_error(snapshot),
                poll_snapshots=polls,
                extras=extras,
                **carry_fields,
            )

        if _terminal_failure(snapshot):
            fields = failed_snapshot_fields(snapshot)
            retain = mission_retain or bool(fields["unverifiable"])
            reason = (
                "operator_proxy_cse_retain"
                if mission_retain
                else fields["retain_reason"]
            )
            abort_info = _abort_then_sweep(
                sat_id,
                execution_id,
                ask_client=relay,
                client=client,
                retain_cse=retain,
                retain_reason=reason,
            )
            extras = _attach_grade_trace(
                {
                    "abort": abort_info,
                    **fields["extras"],
                    **proof_carry.carry_extras(),
                },
                snapshot,
                rule="terminal_failure",
                stall_stage=fields["stall_stage"],
            )
            return CdpGenerateResult(
                ok=False,
                body=str(snapshot.get("body") or ""),
                execution_id=execution_id,
                satellite_execution_id=sat_id,
                prompt_uri=staged.prompt_uri,
                picker_model=picker,
                stall_stage=fields["stall_stage"],
                error=fields["error"],
                poll_snapshots=polls,
                extras=extras,
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
            since_last_progress_s = clock() - last_progress_at
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
                extras=_attach_grade_trace(
                    {
                        "abort": abort_info,
                        "since_last_progress_s": since_last_progress_s,
                        "progress_trace": trace.as_dict(
                            now_s=clock() - trace_started,
                            no_progress_s=no_progress_s,
                        ),
                        **proof_carry.carry_extras(),
                    },
                    snapshot,
                    rule="no_progress",
                    stall_stage="no_progress",
                ),
                **proof_carry.as_result_fields(),
            )
