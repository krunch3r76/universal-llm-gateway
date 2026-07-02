"""Shared inject budget constants — breaks body_injection ↔ inject_registry cycle."""

from __future__ import annotations

import os

INJECTED_BODY_BUDGET_BYTES = int(os.getenv("INJECTED_BODY_BUDGET_BYTES", "50000"))
INJECTED_INDEX_TIMEOUT_MS = int(os.getenv("INJECTED_INDEX_TIMEOUT_MS", "300"))
INJECTED_BODY_TIMEOUT_MS = int(os.getenv("INJECTED_BODY_TIMEOUT_MS", "300"))
INJECTED_TOTAL_DEADLINE_MS = int(os.getenv("INJECTED_TOTAL_DEADLINE_MS", "1500"))
