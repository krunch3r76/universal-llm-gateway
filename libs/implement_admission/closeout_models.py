"""ImplementCloseout v1 pydantic models — no adapter imports."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from admission_common.qualified_scalar import AbsenceSemantics, AuthorityClass
from pydantic import BaseModel, Field

from implement_admission.propagation_row import PropagationRow
from implement_admission.spec import CloseoutStatus, WorkOutcome

CoverageStatus = Literal["complete", "partial", "unavailable"]

AmbientRepoCause = Literal[
    "ambient:concurrent_commit",
    "ambient:concurrent_edit",
    "ambient:vanished",
    "declared_unproved",
]

# Row 29 member 5 — exit_code claim register (mirrors claim_register.ClaimRegister
# + unknown degrade). Kept as Literal here so closeout_models stays free of a
# hard import cycle into claim_register from every closeout consumer.
ExitCodeRegister = Literal["observed", "derived", "unknown"]


class AmbientRepoMovement(BaseModel):
    path: str
    cause: AmbientRepoCause


class EffectEntry(BaseModel):
    op: str
    target: str | None = None
    detail: dict[str, Any] | None = None
    identity: str | None = None


class SurfaceSection(BaseModel):
    surface: str
    source: str
    entries: list[EffectEntry] = Field(default_factory=list)
    cross_check: str | None = None
    authority_class: AuthorityClass | None = None
    absence_semantics: AbsenceSemantics | None = None


class ObservedReconciliation(BaseModel):
    """Stream/conversation vs commit-ack divergence (item 9 / AC-9f)."""

    surface: str
    seat_claimed_unobserved: list[str] = Field(default_factory=list)
    observed_unclaimed: list[str] = Field(default_factory=list)


class EffectsManifest(BaseModel):
    schema_version: int = 1
    dispatch_id: str
    thread_id: str
    capture_sources: list[str] = Field(default_factory=list)
    surfaces: dict[str, SurfaceSection] = Field(default_factory=dict)
    coverage: dict[str, CoverageStatus] = Field(default_factory=dict)
    external_effects: Literal["scoped_out"] = "scoped_out"
    reconciliation: list[ObservedReconciliation] = Field(default_factory=list)


class Verification(BaseModel):
    """One verification row — process exit or derived gate boolean.

    Closed ``{command, exit_code}`` collapsed mid-run capture with closeout
    verdict (specimen ``auto-00a23d2a4f45``: prose ``All checks passed!`` vs
    structured ``ruff check 8 touched files`` / ``exit_code: 1``). Row 29
    member 5 opens the schema:

    - ``exit_code_register`` — ``observed`` (process returncode) vs ``derived``
      (Gate-D boolean packed as 0/1) vs ``unknown`` (legacy wire degrade).
    - ``invocation_id`` — binds the row to one process/gate evaluation so two
      runs of the same command are distinguishable. A register alone without
      identity does not fix the specimen class.

    **Gate-D ≠ pytest witness:** ``gate_d:*`` rows are structural deliverable
    gates (``basis=gate_d_boolean_pass``), not harvested test process exits.
    Optional pytest siblings are ``observed`` rows packed by
    ``cursor_sdk_test_observation.harvest_test_verifications``; absence of such
    a sibling does **not** earn "no tests ran" — semantics
    ``presence_legible_absence_not`` (7065#162).

    Default ``unknown`` keeps historical closeout JSON loadable
    (``ImplementCloseout.model_validate`` / pipeline apply). New packers MUST
    set register + invocation_id via the helpers below — bare two-field
    construction is the legacy degrade path, not the typed emit path.
    """

    command: str
    exit_code: int
    exit_code_register: ExitCodeRegister = "unknown"
    invocation_id: str | None = None
    basis: str | None = None


def observed_process_verification(
    *,
    command: str,
    exit_code: int,
    invocation_id: str | None = None,
    basis: str | None = None,
) -> Verification:
    """Pack a process-observed exit from ``subprocess`` / shell returncode.

    Mints ``invocation_id`` when omitted so each process run stays distinct.
    Used for closeout-time lint, harvested pytest shells, and quality_gate test
    siblings — never for Gate-D (use ``derived_gate_verification``).
    """
    return Verification(
        command=command,
        exit_code=exit_code,
        exit_code_register="observed",
        invocation_id=invocation_id or f"proc:{uuid4().hex}",
        basis=basis,
    )


def derived_gate_verification(
    *,
    command: str,
    exit_code: int,
    basis: str,
    invocation_id: str | None = None,
) -> Verification:
    """Pack a derived boolean-as-exit for Gate-D and similar non-process verdicts.

    ``exit_code`` is not a live process returncode — callers must pass *basis*.
    Readers must not treat ``gate_d:*`` rows as pytest witnesses; see
    ``TEST_OBSERVATION_SEMANTICS`` / ``presence_legible_absence_not``.
    """
    return Verification(
        command=command,
        exit_code=exit_code,
        exit_code_register="derived",
        invocation_id=invocation_id,
        basis=basis,
    )


class EvidenceUris(BaseModel):
    dispatch_ids: list[str] = Field(default_factory=list)
    bus_threads: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    cortex_assertions: list[str] | None = Field(default_factory=list)
    git_refs: list[str] = Field(default_factory=list)


class AdapterResult(BaseModel):
    adapter: str
    status: str
    mutation: str | None = None
    error: str | None = None


class ImplementCloseout(BaseModel):
    schema_version: Literal[1] = 1
    status: CloseoutStatus
    work_outcome: WorkOutcome | None = None
    summary: str
    deviations: list[str] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    files_deleted: list[str] = Field(default_factory=list)
    files_ambient_repo_movement: list[AmbientRepoMovement] = Field(default_factory=list)
    capture_status: Literal["complete", "partial", "unavailable"] | None = None
    effects_manifest: EffectsManifest | None = None
    public_api_changed: bool = False
    verification: list[Verification] = Field(default_factory=list)
    evidence_uris: EvidenceUris = Field(default_factory=EvidenceUris)
    source_ref: str
    packet_sha256: str | None = None
    adapter_results: list[AdapterResult] = Field(default_factory=list)
    # Landed≠live: manage sync_restart / plugin-install action lines (decision:closeout-propagation-residue).
    propagation_residue: list[str] = Field(default_factory=list)
    # Structured harvest rows — authoritative when non-empty over propagation_residue.
    propagation: list[PropagationRow] = Field(default_factory=list)
    usage: dict[str, Any] | None = None
    usage_capture_status: Literal["captured", "partial", "missing", "reconciled_delta"] | None = (
        None
    )
    # Row-16 lane label + isolation gauge — stamped at admit on all write lanes.
    lane: Literal["A", "B"] | None = None
    isolation_materialized: bool | None = None
    # Lane-B worktree closeout fields (S3) — optional; Lane-A/non-B may omit branch keys.
    branch: str | None = None
    branch_point: str | None = None
    head_sha: str | None = None
    commits_ahead: int | None = None
    commits_ahead_unfiltered: int | None = None
    landed: bool | None = None
