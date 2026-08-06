"""Descriptor drift gate — compare ``expected_x_mcp_count`` to live served counts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from services.git_integration_worker.cursor_auto.propagation_served_artifact import (
    SERVED_ARTIFACT_DESCRIPTORS,
    probe_served_artifact,
)


@dataclass(frozen=True, slots=True)
class DescriptorDriftResult:
    """Outcome of comparing descriptor expectations to live served OpenAPI."""

    fatal_messages: tuple[str, ...]
    warning_messages: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        return 1 if self.fatal_messages else 0


def check_descriptor_drift(
    *,
    probe_fn: Callable[..., dict[str, Any] | None] | None = None,
    code_ref: str = "HEAD",
) -> DescriptorDriftResult:
    """Compare each descriptor's expected count to the live served x-mcp count.

    Served **fewer** than expected → FATAL (healthy service cannot satisfy proof).
    Served **more** than expected → WARNING (service grew; expectation lags).
    Probe unreachable → WARNING (transient fleet/restart; must not refuse land alone).
    """
    probe = probe_fn or probe_served_artifact
    fatals: list[str] = []
    warnings: list[str] = []
    for service, descriptor in sorted(SERVED_ARTIFACT_DESCRIPTORS.items()):
        payload = probe(service, code_ref=code_ref)
        if payload is None:
            warnings.append(f"{service}: descriptor drift probe unreachable")
            continue
        served = payload.get("x_mcp_count")
        expected = descriptor.expected_x_mcp_count
        if not isinstance(served, int):
            fatals.append(f"{service}: descriptor drift unreadable x_mcp_count")
            continue
        if served < expected:
            fatals.append(
                f"{service}: served x-mcp count {served} < expected {expected}"
            )
        elif served > expected:
            warnings.append(
                f"{service}: served x-mcp count {served} > expected {expected}"
            )
    return DescriptorDriftResult(
        fatal_messages=tuple(fatals),
        warning_messages=tuple(warnings),
    )


__all__ = ["DescriptorDriftResult", "check_descriptor_drift"]
