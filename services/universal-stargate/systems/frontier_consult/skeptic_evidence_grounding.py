"""Skeptic reply evidence extraction and path grounding for implement admission."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from implement_admission.scheme_resolve import (
    parse_schemed_path,
    resolve_schemed_packet_file,
)

SKEPTIC_EVIDENCE_GROUNDING_CUTOFF = "2026-06-29T00:00:00+00:00"
_NON_FILE_EVIDENCE_PREFIXES = (
    "agent-bus:",
    "spec_sha256:",
    "execution:",
    "assertion:",
    "todo:",
    "decision:",
)
_FILE_EVIDENCE_HEADER = re.compile(r"^FILE_EVIDENCE_PATHS:\s*$", re.IGNORECASE)
_LIST_MARKER_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)(?P<entry>.+)$")
_GROUNDING_MODE_LINE = re.compile(r"^grounding_mode:\s*(\S+)\s*$", re.IGNORECASE)
_AGENT_BUS_EVIDENCE = re.compile(
    r"^agent-bus:(?P<thread>\d+)(?:#turn-(?P<turn>\d+))?$",
    re.IGNORECASE,
)
_KNOWN_FILE_SCHEMES = frozenset({"workspaces", "cortex", "ws"})
_MAX_FILE_EVIDENCE_PATH_LEN = 200


class SkepticBusReader(Protocol):
    def bus_turn_get(self, thread: str, turn_number: int) -> dict[str, Any] | None: ...

    def bus_thread_last_turn(self, thread: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class SkepticEvidenceOutcome:
    grounded: bool | None
    unresolved: list[str] | None = None
    mode: str | None = None


def _strip_evidence_path_annotation(entry: str) -> str:
    """Keep the resolvable path prefix; drop optional :: / — annotation tails."""
    path = entry.strip()
    if " :: " in path:
        path = path.split(" :: ", 1)[0].strip()
    if " — " in path:
        path = path.split(" — ", 1)[0].strip()
    return path


def _normalize_file_evidence_entry(line: str) -> str:
    stripped = line.strip()
    match = _LIST_MARKER_RE.match(stripped)
    if match:
        stripped = match.group("entry").strip()
    return _strip_evidence_path_annotation(stripped)


def _is_non_file_evidence_token(entry: str) -> bool:
    lower = entry.strip().lower()
    return any(lower.startswith(prefix) for prefix in _NON_FILE_EVIDENCE_PREFIXES)


def _is_malformed_file_evidence(entry: str) -> bool:
    raw = entry.strip()
    if not raw or _is_non_file_evidence_token(raw):
        return False
    if len(raw) > _MAX_FILE_EVIDENCE_PATH_LEN or any(ch.isspace() for ch in raw):
        return True
    parsed = parse_schemed_path(raw)
    if parsed.scheme is not None:
        return parsed.scheme not in _KNOWN_FILE_SCHEMES
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw):
        prefix = raw.split(":", 1)[0].lower()
        return prefix not in _KNOWN_FILE_SCHEMES
    return False


def parse_skeptic_reply_evidence(body: str) -> tuple[list[str], str | None, bool]:
    """Return (file_paths, grounding_mode, malformed)."""
    lines = body.splitlines()
    grounding_mode: str | None = None
    for line in lines:
        match = _GROUNDING_MODE_LINE.match(line.strip())
        if match:
            grounding_mode = match.group(1).strip().lower()

    paths: list[str] = []
    malformed = False
    in_block = False
    for line in lines:
        stripped = line.strip()
        if _FILE_EVIDENCE_HEADER.match(stripped):
            in_block = True
            continue
        if not in_block:
            continue
        if not stripped:
            break
        if stripped.endswith(":") and "://" not in stripped and "/" not in stripped:
            break
        entry = _normalize_file_evidence_entry(stripped)
        if _is_non_file_evidence_token(entry):
            continue
        if _is_malformed_file_evidence(entry):
            malformed = True
            continue
        paths.append(entry)
    return paths, grounding_mode, malformed


def select_agent_bus_evidence(
    evidence_uris: list[str] | None,
) -> tuple[str | None, int | None]:
    if not evidence_uris:
        return None, None
    for uri in evidence_uris:
        if not isinstance(uri, str):
            continue
        match = _AGENT_BUS_EVIDENCE.match(uri.strip())
        if match:
            turn_raw = match.group("turn")
            return match.group("thread"), int(turn_raw) if turn_raw else None
    return None, None


def fetch_skeptic_turn_body(
    *,
    reader: SkepticBusReader,
    thread: str,
    turn_number: int | None,
) -> str | None:
    turn: dict[str, Any] | None
    if turn_number is not None:
        turn = reader.bus_turn_get(thread, turn_number)
    else:
        turn = reader.bus_thread_last_turn(thread)
    if turn is None:
        return None
    body = turn.get("body")
    return body if isinstance(body, str) else None


def ground_skeptic_file_paths(
    paths: list[str],
    *,
    workspaces_root: Path | None,
) -> tuple[list[str], int]:
    unresolved: list[str] = []
    resolved_count = 0
    for path in paths:
        try:
            resolved = resolve_schemed_packet_file(
                path,
                workspaces_root_override=workspaces_root,
            )
        except OSError:
            unresolved.append(path)
            continue
        if resolved is None:
            unresolved.append(path)
        else:
            resolved_count += 1
    return unresolved, resolved_count


def evaluate_skeptic_evidence_grounding(
    *,
    reader: SkepticBusReader,
    assertion: dict[str, Any],
    workspaces_root: Path | None,
) -> SkepticEvidenceOutcome:
    observed_at = str(assertion.get("observed_at") or "")
    if observed_at and observed_at < SKEPTIC_EVIDENCE_GROUNDING_CUTOFF:
        return SkepticEvidenceOutcome(grounded=True)

    evidence = assertion.get("evidence_uris")
    evidence_list = evidence if isinstance(evidence, list) else None
    thread, turn_number = select_agent_bus_evidence(evidence_list)
    if thread is None:
        return SkepticEvidenceOutcome(grounded=False, mode="stamp_missing")

    body = fetch_skeptic_turn_body(
        reader=reader,
        thread=thread,
        turn_number=turn_number,
    )
    if body is None:
        return SkepticEvidenceOutcome(grounded=False, mode="stamp_missing")

    paths, grounding_mode, malformed = parse_skeptic_reply_evidence(body)
    if malformed:
        return SkepticEvidenceOutcome(grounded=False, mode="malformed")
    if grounding_mode == "reasoning_only":
        return SkepticEvidenceOutcome(grounded=False, mode="reasoning_only")
    if not paths:
        return SkepticEvidenceOutcome(grounded=False)

    unresolved, resolved_count = ground_skeptic_file_paths(
        paths,
        workspaces_root=workspaces_root,
    )
    if unresolved:
        return SkepticEvidenceOutcome(grounded=False, unresolved=unresolved)
    if resolved_count == 0:
        return SkepticEvidenceOutcome(grounded=False)
    return SkepticEvidenceOutcome(grounded=True)


__all__ = [
    "SKEPTIC_EVIDENCE_GROUNDING_CUTOFF",
    "SkepticBusReader",
    "SkepticEvidenceOutcome",
    "evaluate_skeptic_evidence_grounding",
    "parse_skeptic_reply_evidence",
]
