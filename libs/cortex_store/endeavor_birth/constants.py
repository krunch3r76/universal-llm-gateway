"""Shared constants for endeavor birth gate (Step 2)."""

from __future__ import annotations

DISPOSITION_SET = frozenset({"express", "imply", "omit_with_reason"})
VISIBILITY_SET = frozenset({"public", "internal", "restricted"})
ROW_PREDICATE = "endeavor_strategy_row"
LEGACY_THREAD_KEYS = frozenset({"bus_thread", "arc_root_thread", "case_study_thread"})
BIRTH_POINTER_KEYS = ("endeavor_charter_uri", "ring_thread")
SCOREBOARD_KEY = "endeavor_scoreboard_uri"
ACK_ATTR = "endeavor_birth_ack"
# Advisory Cowork Project UUID — optional; never a birth-gate / lock predicate (T3 only).
COWORK_PROJECT_ATTR = "cowork_project"
STAGE_S2 = "S2"
