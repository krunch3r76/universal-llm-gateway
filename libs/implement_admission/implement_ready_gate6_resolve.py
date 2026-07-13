"""Gate-6 FILE_EVIDENCE ratification resolver for implement admission."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from implement_admission.implement_ready_gate_resolve import SkepticRatificationOutcome
from implement_admission.scheme_resolve import (
    parse_schemed_path,
    resolve_schemed_packet_file,
)

_AGENT_BUS_EVIDENCE = re.compile(
    r"^agent-bus:(?P<thread>\d+)(?:#turn-(?P<turn>\d+))?$",
    re.IGNORECASE,
)
_ELIGIBLE_GATE6_ROLES = frozenset({"reviewer", "skeptic"})
_AFFIRMATIVE_VERDICT = re.compile(
    r"(?:^##\s*Verdict:\s*\*\*RATIFY(?:-WITH-CONDITIONS)?\*\*"
    r"|^\*\*Verdict:\*\*\s*\*\*RATIFY(?:-WITH-CONDITIONS)?\*\*)",
    re.IGNORECASE | re.MULTILINE,
)
_NEGATIVE_VERDICT = re.compile(
    r"(?:^##\s*Verdict:\s*\*\*REJECT[^*]*\*\*"
    r"|^\*\*Verdict:\*\*\s*\*\*REJECT[^*]*\*\*)",
    re.IGNORECASE | re.MULTILINE,
)
_FILE_EVIDENCE_HEADER = re.compile(r"^FILE_EVIDENCE_PATHS:\s*$", re.IGNORECASE)
_LIST_MARKER_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)(?P<entry>.+)$")
_GROUNDING_MODE_LINE = re.compile(r"^grounding_mode:\s*(\S+)\s*$", re.IGNORECASE)
_NON_FILE_EVIDENCE_PREFIXES = (
    "agent-bus:",
    "spec_sha256:",
    "execution:",
    "assertion:",
    "todo:",
    "decision:",
)
_KNOWN_FILE_SCHEMES = frozenset({"workspaces", "cortex", "ws"})
_MAX_FILE_EVIDENCE_PATH_LEN = 200

FetchBusTurn = Callable[[str, int], dict[str, Any] | None]


def _strip_evidence_path_annotation(entry: str) -> str:
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


def _parse_file_evidence_paths(body: str) -> tuple[list[str], str | None, bool]:
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


def _ground_file_evidence_paths(
    paths: list[str],
    *,
    workspaces_root: Any | None,
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


def _gate6_affirmative_disposition(body: str) -> bool:
    if _NEGATIVE_VERDICT.search(body):
        return False
    return _AFFIRMATIVE_VERDICT.search(body) is not None


def _gate6_turn_role(turn: dict[str, Any]) -> str | None:
    raw = turn.get("from") or turn.get("from_role") or turn.get("role")
    if not isinstance(raw, str):
        return None
    return raw.strip().lower()


def resolve_gate6_ratification(
    *,
    todo_attrs: dict[str, Any],
    implement_ready_assertion: dict[str, Any],
    spec_hash_uri: str | None,
    fetch_bus_turn: FetchBusTurn | None = None,
    workspaces_root: Any | None = None,
) -> SkepticRatificationOutcome:
    """Resolve Gate-6 FILE_EVIDENCE as effective ratification when explicitly designated."""
    if not spec_hash_uri:
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                "dense-spec content hash is unavailable, so gate6 ratification "
                "cannot be checked against spec_sha256:<hex>"
            ),
        )

    raw_uri = todo_attrs.get("gate6_ratification_uri")
    if not isinstance(raw_uri, str) or not raw_uri.strip():
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                "no gate6_ratification_uri attribute — set "
                "gate6_ratification_uri=agent-bus:{tid}#turn-N on the todo"
            ),
        )

    match = _AGENT_BUS_EVIDENCE.match(raw_uri.strip())
    if match is None or match.group("turn") is None:
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                "gate6_ratification_uri must be agent-bus:{tid}#turn-N with an "
                "explicit turn number"
            ),
        )

    evidence = implement_ready_assertion.get("evidence_uris")
    evidence_list = evidence if isinstance(evidence, list) else None
    if not (isinstance(evidence_list, list) and spec_hash_uri in evidence_list):
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                "implement_ready evidence_uris must cite the current "
                f"spec_sha256 URI ({spec_hash_uri}) for gate6 ratification"
            ),
        )

    if fetch_bus_turn is None:
        return SkepticRatificationOutcome(
            ratified=False,
            reason="gate6 bus turn could not be fetched (no bus reader available)",
            evidence_grounded=False,
            evidence_mode="stamp_missing",
        )

    thread = match.group("thread")
    turn_number = int(match.group("turn"))
    turn = fetch_bus_turn(thread, turn_number)
    if turn is None:
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                f"gate6_ratification_uri {raw_uri.strip()!r} could not be read "
                "from agent-bus"
            ),
            evidence_grounded=False,
            evidence_mode="stamp_missing",
        )

    body = turn.get("body")
    if not isinstance(body, str) or not body.strip():
        return SkepticRatificationOutcome(
            ratified=False,
            reason=f"gate6 bus turn {raw_uri.strip()!r} has no readable body",
            evidence_grounded=False,
            evidence_mode="stamp_missing",
        )

    role = _gate6_turn_role(turn)
    if role not in _ELIGIBLE_GATE6_ROLES:
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                f"gate6 bus turn author role {role!r} is not an eligible check "
                "role (reviewer or skeptic)"
            ),
        )

    if not _gate6_affirmative_disposition(body):
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                "gate6 bus turn lacks an affirmative verdict line "
                "(## Verdict: **RATIFY** or **Verdict:** **RATIFY-WITH-CONDITIONS**)"
            ),
        )

    if spec_hash_uri not in body:
        return SkepticRatificationOutcome(
            ratified=False,
            reason=(
                f"gate6 bus turn body must contain the spec_sha256 token "
                f"{spec_hash_uri!r} matching the current dense spec"
            ),
        )

    paths, grounding_mode, malformed = _parse_file_evidence_paths(body)
    if malformed:
        return SkepticRatificationOutcome(
            ratified=True,
            evidence_grounded=False,
            evidence_mode="malformed",
        )
    if grounding_mode == "reasoning_only":
        return SkepticRatificationOutcome(
            ratified=True,
            evidence_grounded=False,
            evidence_mode="reasoning_only",
        )
    if not paths:
        return SkepticRatificationOutcome(
            ratified=True,
            evidence_grounded=False,
        )

    unresolved, resolved_count = _ground_file_evidence_paths(
        paths,
        workspaces_root=workspaces_root,
    )
    if unresolved:
        return SkepticRatificationOutcome(
            ratified=True,
            evidence_grounded=False,
            evidence_unresolved=unresolved,
        )
    if resolved_count == 0:
        return SkepticRatificationOutcome(
            ratified=True,
            evidence_grounded=False,
        )
    return SkepticRatificationOutcome(
        ratified=True,
        evidence_grounded=True,
    )


__all__ = ["FetchBusTurn", "resolve_gate6_ratification"]
