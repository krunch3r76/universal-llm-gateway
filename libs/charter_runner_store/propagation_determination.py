"""Classify what a liveness probe actually established about a propagation row.

Split from :mod:`propagation_terminal` so that *reading* an observation is a
separate concern from *deciding* on it.

A probe answers one of three ways, and the third is load-bearing. A payload
carrying no readable ``code_version`` has not reported a mismatch — it has
failed to report. Collapsing that non-answer into "no" lets a fetch failure, a
half-unreachable composite probe, or an unrecognised payload shape drive a row
to ``failed``, which is terminal: ``fail_row`` transitions only ``WHERE
status='open'``, so the row leaves the open set and no later, better-founded
probe can ever revisit it. A wrong negative here is therefore self-sealing.

The asymmetry between the two directions is real and is why they are not
treated alike:

* A **match** is self-validating. Observed ``code_version`` equals the target,
  so the target code is live regardless of which process generation answered.
* A **non-match** is not. It establishes only that *this* process, at *this*
  instant, is not at the target — which is also what a probe of the outgoing
  generation looks like mid-restart.
"""

from __future__ import annotations

from typing import Any, Literal

Determination = Literal["matched", "contradicted", "indeterminate"]

# Composite payload emitted by ``probe_for_row`` for client-visible mcp rows:
# ``{"mcp_health": {...}, "cortex_api": {...}}``. It carries no top-level
# ``code_version`` or ``uptime_s``, so predicates that read those keys flat are
# blind to it.
_COMPOSITE_KEYS = ("mcp_health", "cortex_api")


def observed_code_versions(payload: dict[str, Any] | None) -> list[str] | None:
    """Readable ``code_version`` strings in a flat or composite probe payload.

    Returns ``None`` when the probe did not answer the question — no sub-probe
    returned a well-formed version. That is distinct from returning a version
    which differs from the target, and the two must not share a representation.
    """
    if not isinstance(payload, dict):
        return None
    if any(key in payload for key in _COMPOSITE_KEYS):
        versions: list[str] = []
        for key in _COMPOSITE_KEYS:
            section = payload.get(key)
            if not isinstance(section, dict):
                return None
            version = section.get("code_version")
            if not isinstance(version, str) or not version:
                return None
            versions.append(version)
        return versions or None
    version = payload.get("code_version")
    if isinstance(version, str) and version:
        return [version]
    return None


def classify_probe(
    payload: dict[str, Any] | None,
    *,
    code_ref: str,
    matched: bool,
) -> Determination:
    """Three-valued reading of one probe against a row's ``code_ref``.

    ``matched`` comes from the caller's existing proof predicate so this module
    does not duplicate proof-class semantics; it decides only whether a
    non-match is a genuine contradiction or a non-answer.
    """
    if matched:
        return "matched"
    versions = observed_code_versions(payload)
    if versions is None:
        return "indeterminate"
    # Versions read as equal while the proof predicate declined: the predicate
    # knows something this reader does not, so do not upgrade that to a
    # contradiction.
    if all(version == code_ref for version in versions):
        return "indeterminate"
    return "contradicted"


def outgoing_generation_ruled_out(
    payload: dict[str, Any] | None,
    *,
    settle_not_before_monotonic: float | None,
    now_monotonic: float,
) -> bool:
    """True only when the probed process demonstrably started after the boundary.

    Requires *both* a restart boundary and an ``uptime_s`` in the payload.
    Absence of either means the reading cannot be attributed to the incoming
    generation, so any contradiction it reports must stay non-terminal.
    """
    if settle_not_before_monotonic is None:
        return False
    if not isinstance(payload, dict):
        return False
    uptime = payload.get("uptime_s")
    if isinstance(uptime, bool) or not isinstance(uptime, (int, float)):
        return False
    return (now_monotonic - float(uptime)) >= settle_not_before_monotonic


__all__ = [
    "Determination",
    "classify_probe",
    "observed_code_versions",
    "outgoing_generation_ruled_out",
]
