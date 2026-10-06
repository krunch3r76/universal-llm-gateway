"""Typed shapes for maestro-induct packet sections (documentation-only)."""

from __future__ import annotations

from typing import TypedDict


class MetaTruncated(TypedDict, total=False):
    lanes: int
    scores: int
    continuity: int
    checkpoint: int
    runbook: int
    journal: int
    house: int
    open_consults: str
    open_consults_ids: int
    open_consults_outside_house: bool


class MetaCounts(TypedDict, total=False):
    first_act_source: str


class MetaError(TypedDict, total=False):
    kind: str
    message: str
    section: str
    item: str
    threads: list[str]
    scanned_down_to: int
