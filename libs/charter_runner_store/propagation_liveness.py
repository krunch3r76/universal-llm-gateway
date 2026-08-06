"""Observation-cited answers to whether a ``code_ref`` is live on a service.

Ledger PK rows (``service:code_ref:action``) are immutable *events* — one
obligation attempt with an outcome typed as
:data:`charter_runner_store.propagation_attempt_status.PropagationAttemptStatus`
(Packet D / member 2). Readers who treat ``status=failed`` or ``status=closed``
as current not-live / live inherit a state/event confusion that freezes the
wrong answer after the world catches up (F4 specimen:
``git_integration_worker:40f8eadd…:sync_restart``).

This module never opens the ledger. Every answer cites a fresh process probe
plus ``deploy_identity.code_ref_relation``; ``unknown`` is honest when the
probe is missing or unreadable. Current-state authority for the family.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from deploy_identity.code_ref_relation import (
    CodeRefRelation,
    code_ref_relation_from_observed,
    code_ref_satisfied,
)
from deploy_identity.code_version import normalize_code_ref

ProbeFn = Callable[[str], dict[str, Any] | None]
LivenessAnswer = Literal["yes", "no", "unknown"]


@dataclass(frozen=True)
class CodeRefLiveness:
    """One observation-cited answer to ``is code_ref live on service?``."""

    answer: LivenessAnswer
    service: str
    code_ref: str
    observed_code_version: str | None
    relation: CodeRefRelation | None
    observation: dict[str, Any]
    reason: str


def _default_probe(service: str) -> dict[str, Any] | None:
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        probe_process_live,
    )

    return probe_process_live(service)


def observe_code_ref_live(
    service: str,
    code_ref: str,
    *,
    probe: ProbeFn | None = None,
) -> CodeRefLiveness:
    """Answer whether ``code_ref`` is live on ``service`` from a fresh probe.

    Does not read ledger row status. ``yes`` means equal or ancestor relation
    (``code_ref_satisfied``); ``no`` means a readable version that does not
    satisfy; ``unknown`` means unreachable or unreadable probe. The
    ``observation`` dict always carries the probe payload (or an explicit
    unreachability record) so callers can cite evidence.
    """
    resolved = normalize_code_ref(code_ref)
    probe_fn = probe or _default_probe
    payload = probe_fn(service)
    if not isinstance(payload, dict):
        return CodeRefLiveness(
            answer="unknown",
            service=service,
            code_ref=resolved,
            observed_code_version=None,
            relation=None,
            observation={
                "probe_reachable": False,
                "service": service,
                "code_ref": resolved,
            },
            reason="probe unreachable or returned no payload",
        )
    raw_version = payload.get("code_version")
    observed = raw_version if isinstance(raw_version, str) and raw_version.strip() else None
    relation = code_ref_relation_from_observed(resolved, observed)
    citation = {
        **payload,
        "probe_reachable": True,
        "service": service,
        "code_ref": resolved,
        "observed_code_version": observed,
        "code_ref_relation": relation,
    }
    if observed is None:
        return CodeRefLiveness(
            answer="unknown",
            service=service,
            code_ref=resolved,
            observed_code_version=None,
            relation=relation,
            observation=citation,
            reason="probe carried no readable code_version",
        )
    if code_ref_satisfied(resolved, observed):
        return CodeRefLiveness(
            answer="yes",
            service=service,
            code_ref=resolved,
            observed_code_version=observed,
            relation=relation,
            observation=citation,
            reason=(
                f"observed code_version={observed} satisfies code_ref "
                f"via relation={relation}"
            ),
        )
    return CodeRefLiveness(
        answer="no",
        service=service,
        code_ref=resolved,
        observed_code_version=observed,
        relation=relation,
        observation=citation,
        reason=(
            f"observed code_version={observed} does not satisfy code_ref "
            f"(relation={relation})"
        ),
    )


__all__ = [
    "CodeRefLiveness",
    "LivenessAnswer",
    "ProbeFn",
    "observe_code_ref_live",
]
