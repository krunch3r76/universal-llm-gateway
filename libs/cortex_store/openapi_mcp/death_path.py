"""Death-path gate for POST /dispatch deletion (A3)."""

from __future__ import annotations

DEATH_PATH_GATE_DOC = """\
Death-path gate (A3 — severable from vanish-tier / schema channel):

Delete ``POST /dispatch`` ONLY when BOTH hold:

1. **served-parity** — every MCP-reachable cortex op maps to a served OpenAPI
   operationId (bijection; no reachable-but-unserved ops).
2. **zero non-adapter traffic** — no direct HTTP ``POST /dispatch`` callers
   remain after the declared ≥21–30d telemetry window (adapter relay passes
   ``via_adapter=True``).

Vanish-tier (S6) and out-of-band schema channel (A4) are parallel work and
MUST NOT block deletion once the two gates above pass.
"""

# Minimum honest telemetry window before any "zero traffic" retire claim (days).
TELEMETRY_HONEST_WINDOW_DAYS = 21


def death_path_gate_met(
    *,
    served_parity: bool,
    zero_non_adapter_traffic: bool,
) -> bool:
    """Return True only when both A3 gates are empirically met."""
    return served_parity and zero_non_adapter_traffic
