"""Parse operator ``TYPE: DIRECTIVE`` bodies from agent_bus.request."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_auto.queue import AutoJob


@dataclass(frozen=True, slots=True)
class ParsedDirective:
    """Minimal fields extracted from an operator directive body."""

    turn_type: str
    density: str | None
    require_attended: bool
    evidence_required_uris: tuple[str, ...]
    raw_body: str


_TYPE_RE = re.compile(r"^TYPE:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
# Continuity hop: first nonblank line only (terra C1 — not anywhere-in-body).
_FIRST_LINE_TYPE_RE = re.compile(r"^TYPE:\s*(\S+)", re.IGNORECASE)
_CONTINUITY_HANDOFF = "CONTINUITY_HANDOFF"
_DIRECTIVE_TYPES = frozenset({"DIRECTIVE", _CONTINUITY_HANDOFF})
_DENSITY_RE = re.compile(r"^density:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_CONTRACT_RE = re.compile(r"^contract:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_DESIRED_MODEL_RE = re.compile(
    r"^desired_model:\s*(\S+)", re.MULTILINE | re.IGNORECASE
)
_ESCALATION_RE = re.compile(r"^escalation:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_EFFORT_RE = re.compile(r"^effort:\s*(\S+)", re.MULTILINE | re.IGNORECASE)
_REASONING_EFFORT_RE = re.compile(
    r"^reasoning_effort:\s*(\S+)", re.MULTILINE | re.IGNORECASE
)
_MODEL_KNOBS_EFFORT_RE = re.compile(
    r"""^[ \t]*(?:[-*][ \t]+)?model_knobs\s*=\s*\{[^}]*["']?effort["']?\s*:\s*["']?([^"'}\s,]+)""",
    re.IGNORECASE | re.MULTILINE,
)
_REQUIRE_ATTENDED_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?require_attended:\s*(true|yes|1)\b",
    re.MULTILINE | re.IGNORECASE,
)
_EXECUTOR_BIND_ATTENDED_RE = re.compile(
    r"^[ \t]*executor_bind:\s*attended\b",
    re.MULTILINE | re.IGNORECASE,
)
_EVIDENCE_REQUIRED_RE = re.compile(
    r"^[ \t]*evidence_required\s*:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_READ_CORPUS_RE = re.compile(
    r"^[ \t]*(?:read[- ]corpus|corpus|read)\s*:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_URI_TOKEN_RE = re.compile(r"(?:cortex|workspaces)://[^\s`>,]+")
_SHA_SUFFIX_RE = re.compile(
    r"\s*[·•]\s*sha256:.*$|\s+sha256:[0-9a-f]+.*$",
    re.IGNORECASE,
)


def _clean_uri_token(raw: str) -> str | None:
    """Strip backticks, trailing punctuation, and sha suffixes from a URI token."""
    token = raw.strip().strip("`\"'").rstrip(".,;:)>")
    token = _SHA_SUFFIX_RE.sub("", token).strip()
    if token.lower().startswith(("cortex://", "workspaces://")):
        return token
    return None


def _harvest_uri_tokens(line: str) -> list[str]:
    """Collect durable-share URIs from one directive line."""
    uris: list[str] = []
    seen: set[str] = set()
    for match in _URI_TOKEN_RE.finditer(line):
        cleaned = _clean_uri_token(match.group(0))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            uris.append(cleaned)
    return uris


def _extract_evidence_required_uris(text: str) -> tuple[str, ...]:
    """Parse ``evidence_required`` and labeled read-corpus lines for durable URIs."""
    ordered: list[str] = []
    seen: set[str] = set()
    for pattern in (_EVIDENCE_REQUIRED_RE, _READ_CORPUS_RE):
        for match in pattern.finditer(text):
            for uri in _harvest_uri_tokens(match.group(1)):
                if uri not in seen:
                    seen.add(uri)
                    ordered.append(uri)
    return tuple(ordered)


def _extract_guard_line_uris(text: str) -> tuple[str, ...]:
    """Parse only ``evidence_required:`` lines — confer fence guard source."""
    ordered: list[str] = []
    seen: set[str] = set()
    for match in _EVIDENCE_REQUIRED_RE.finditer(text):
        for uri in _harvest_uri_tokens(match.group(1)):
            if uri not in seen:
                seen.add(uri)
                ordered.append(uri)
    return tuple(ordered)


def corpus_guard_uris(directive: ParsedDirective | None) -> frozenset[str]:
    """Return cortex guard URIs for confer write-fence intersection (v1 cortex-only).

    Guard scope is limited to ``evidence_required:`` lines (not read-corpus harvest).
    ``workspaces://`` tokens on those lines are collected for prompt inject only.
    """
    if directive is None:
        return frozenset()
    guard_line_uris = _extract_guard_line_uris(directive.raw_body)
    return frozenset(
        uri for uri in guard_line_uris if uri.lower().startswith("cortex://")
    )


def _body_requires_attended(text: str) -> bool:
    """True when DIRECTIVE body declares attended executor bind."""
    return bool(
        _REQUIRE_ATTENDED_RE.search(text) or _EXECUTOR_BIND_ATTENDED_RE.search(text)
    )


def effective_require_attended(
    job: AutoJob,
    directive: ParsedDirective | None,
) -> bool:
    """OR wire + body attendance signals for handler short-circuit."""
    return bool(job.require_attended) or (
        directive is not None and directive.require_attended
    )


def attendance_surface(
    job: AutoJob,
    directive: ParsedDirective | None,
) -> str | None:
    """Which surface set attendance: ``wire``, ``body``, ``both``, or ``None``."""
    wire = bool(job.require_attended)
    body = directive is not None and directive.require_attended
    if wire and body:
        return "both"
    if wire:
        return "wire"
    if body:
        return "body"
    return None


_SCOPE_HEADING_RE = re.compile(r"(?im)^#{1,3}\s*scope\b")
_SCOPE_TAG_RE = re.compile(r"<scope>", re.IGNORECASE)
# cdp-operator-proxy-v0 §2 inline field — must match has_actionable_scope (a:26888).
_SCOPE_FIELD_RE = re.compile(r"(?im)^scope:\s*\S+")
_SOURCE_REF_LINE_RE = re.compile(r"(?im)^source_ref:\s*\S+")
_TODO_TOKEN_RE = re.compile(r"\btodo:[a-z0-9][a-z0-9._-]*", re.IGNORECASE)
_PACKET_TOKEN_RE = re.compile(r"\bpacket:", re.IGNORECASE)
_FILES_EXPECTED_RE = re.compile(r"(?im)\bfiles_expected\b")
# Tier-M tool asks have no file scope — these are their first-class scope tokens.
_TOOL_OP_FIELD_RE = re.compile(r"(?im)^tool_op:\s*\S+")
_EFFECTS_EXPECTED_RE = re.compile(r"(?im)^effects_expected:\s*\S+")
_PROPAGATION_SCOPE_RE = re.compile(r"(?im)^scope:\s*propagation\b")
_PROPAGATION_HEADING_RE = re.compile(r"(?im)^##\s+propagation(?:\s*\([^)]*\))?\s*$")
_VISION_FIELD_RE = re.compile(r"^vision\s*:", re.MULTILINE | re.IGNORECASE)
NESTED_SCOPE_CONTRACTS = frozenset({"implement", "investigate", "verify", "seed"})
VISION_REQUIRED_CONTRACTS = frozenset({"implement", "investigate", "seed"})


def has_actionable_scope(body: str) -> bool:
    """True when the body carries an actionable nest scope per friction-26765.

    Matches markdown ``## Scope`` headings, §2 lowercase ``scope:`` fields,
    ``<scope>`` tags, ``source_ref:`` lines, ``todo:`` / ``packet:`` tokens,
    ``files_expected`` labels, or the tier-M tool-ask tokens ``tool_op:`` /
    ``effects_expected:``. Prose may mention ``todo:`` / ``packet:``
    (accepted looseness). Body ``contract:`` is handled separately as an
    escape hatch in admit gates.
    """
    text = body or ""
    return bool(
        _SCOPE_HEADING_RE.search(text)
        or _SCOPE_FIELD_RE.search(text)
        or _SCOPE_TAG_RE.search(text)
        or _SOURCE_REF_LINE_RE.search(text)
        or _TODO_TOKEN_RE.search(text)
        or _PACKET_TOKEN_RE.search(text)
        or _FILES_EXPECTED_RE.search(text)
        or _TOOL_OP_FIELD_RE.search(text)
        or _EFFECTS_EXPECTED_RE.search(text)
        or _PROPAGATION_SCOPE_RE.search(text)
        or _PROPAGATION_HEADING_RE.search(text)
    )


def body_has_contract_override(body: str) -> bool:
    """True when the DIRECTIVE body declares an explicit ``contract:`` line."""
    return _CONTRACT_RE.search(body or "") is not None


def body_desired_model(body: str) -> str | None:
    """Return body-level ``desired_model:`` value when present (wire-only contract)."""
    match = _DESIRED_MODEL_RE.search(body or "")
    if match is None:
        return None
    return match.group(1).strip().lower()


def body_escalation(body: str) -> str | None:
    """Return body-level ``escalation:`` value when present (wire-only contract)."""
    match = _ESCALATION_RE.search(body or "")
    if match is None:
        return None
    return match.group(1).strip().lower()


def body_effort_pin(body: str) -> str | None:
    """Return body-level effort hint when present (wire-only contract).

    Matches line-start authoring pins only: ``effort:``, ``reasoning_effort:``, or
    ``model_knobs={\"effort\": ...}`` at a field/line boundary. Inline prose that
    merely mentions those literals (e.g. defect descriptions) does not match.
    """
    text = body or ""
    for pattern in (_EFFORT_RE, _REASONING_EFFORT_RE, _MODEL_KNOBS_EFFORT_RE):
        match = pattern.search(text)
        if match is not None:
            return match.group(1).strip().lower()
    return None


def has_vision_field(body: str) -> bool:
    """True when the DIRECTIVE body declares a ``vision:`` line (presence-only)."""
    return _VISION_FIELD_RE.search(body or "") is not None


def empty_directive_missed_tokens(body: str) -> tuple[str, ...]:
    """Return scope-predicate labels absent from *body* for observation payloads.

    Used by the empty-scope blocked event to list which actionable tokens were
    not found in the operator DIRECTIVE body.
    """
    text = body or ""
    checks: tuple[tuple[str, bool], ...] = (
        ("scope_heading", bool(_SCOPE_HEADING_RE.search(text))),
        ("scope_field", bool(_SCOPE_FIELD_RE.search(text))),
        ("scope_tag", bool(_SCOPE_TAG_RE.search(text))),
        ("source_ref", bool(_SOURCE_REF_LINE_RE.search(text))),
        ("todo", bool(_TODO_TOKEN_RE.search(text))),
        ("packet", bool(_PACKET_TOKEN_RE.search(text))),
        ("files_expected", bool(_FILES_EXPECTED_RE.search(text))),
        ("tool_op", bool(_TOOL_OP_FIELD_RE.search(text))),
        ("effects_expected", bool(_EFFECTS_EXPECTED_RE.search(text))),
        ("propagation_scope", bool(_PROPAGATION_SCOPE_RE.search(text))),
        ("propagation_heading", bool(_PROPAGATION_HEADING_RE.search(text))),
    )
    return tuple(name for name, present in checks if not present)


def effective_contract(wire: str | None, body: str) -> str:
    """Resolve wire contract with DIRECTIVE body upgrade and explicit body override."""
    wire_norm = (wire or "answer").strip().lower() or "answer"
    text = body or ""
    contract_match = _CONTRACT_RE.search(text)
    if contract_match is not None:
        return contract_match.group(1).strip().lower()
    if parse_request_body(body) is not None and wire_norm == "answer":
        return "implement"
    return wire_norm


def is_continuity_hop_request(
    body: str, *, wire_flag: bool = False
) -> tuple[bool, str]:
    """Classify a continuity hop by structural token — never prose sniff.

    Returns ``(is_hop, matched_token)`` where ``matched_token`` is one of
    ``TYPE:CONTINUITY_HANDOFF``, ``wire:continuity_hop``, or ``none``.

    Fail direction (row 21 G1): structural token present ⇒ hop (preserve
    in-flight); absent ⇒ not hop (supersede/backtrack). Wire OR first-line
    TYPE alone is enough; unlabeled second requests stay backtracks.
    """
    if wire_flag:
        text = (body or "").strip()
        if text:
            first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            match = _FIRST_LINE_TYPE_RE.match(first) if first else None
            if match and match.group(1).strip().upper() == _CONTINUITY_HANDOFF:
                return True, "TYPE:CONTINUITY_HANDOFF"
        return True, "wire:continuity_hop"
    text = (body or "").strip()
    if not text:
        return False, "none"
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first:
        return False, "none"
    match = _FIRST_LINE_TYPE_RE.match(first)
    if match is None:
        return False, "none"
    if match.group(1).strip().upper() == _CONTINUITY_HANDOFF:
        return True, "TYPE:CONTINUITY_HANDOFF"
    return False, "none"


def split_continuity_hop_legs(
    body: str, *, matched_token: str | None = None
) -> tuple[str, str | None]:
    """Split a hop-classified body into hop slice + deferred non-hop leg.

    A dying author may pack ``TYPE: CONTINUITY_HANDOFF`` and a later
    ``TYPE: DIRECTIVE`` into one body. Wire-flag hops whose body opens
    ``TYPE: DIRECTIVE`` carry no structural marker — the entire body is the
    lost leg. Hop short-circuit must not discard either envelope.

    Returns ``(hop_body, deferred_body)`` where ``deferred_body`` is ``None``
    when no fork applies. Scope/vision inside the hop slice alone is not a
    second leg (F5 hop-with-scope still single-envelope).

    Multi-``TYPE: DIRECTIVE`` chains: when a fork triggers, every trailing
    ``TYPE: DIRECTIVE`` block from the first split point through end-of-body
    is kept together in one deferred sibling (one queue job, not per-block).
    """
    text = body or ""
    if not text.strip():
        return text, None
    lines = text.splitlines(keepends=True)
    # Locate first nonblank TYPE line.
    first_type_idx: int | None = None
    first_type: str | None = None
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        match = _FIRST_LINE_TYPE_RE.match(stripped)
        if match is None:
            # Leading non-TYPE prose ⇒ not a structural dual-envelope hop body.
            return text, None
        first_type_idx = idx
        first_type = match.group(1).strip().upper()
        break
    if first_type_idx is None:
        return text, None
    # Wire-flag hop with no CONTINUITY_HANDOFF marker: entire DIRECTIVE body
    # is the deferred leg; CDP hop slice is empty (structural hop only).
    if matched_token == "wire:continuity_hop" and first_type == "DIRECTIVE":
        return "", text
    if first_type != _CONTINUITY_HANDOFF:
        return text, None
    deferred_idx: int | None = None
    for idx in range(first_type_idx + 1, len(lines)):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        match = _FIRST_LINE_TYPE_RE.match(stripped)
        if match is None:
            continue
        if match.group(1).strip().upper() == "DIRECTIVE":
            deferred_idx = idx
            break
    if deferred_idx is None:
        return text, None
    hop_body = "".join(lines[:deferred_idx])
    deferred_body = "".join(lines[deferred_idx:])
    if not deferred_body.strip():
        return text, None
    return hop_body, deferred_body


def parse_request_body(body: str) -> ParsedDirective | None:
    """Return parsed directive for ``TYPE: DIRECTIVE`` or ``CONTINUITY_HANDOFF``."""
    text = (body or "").strip()
    if not text:
        return None
    match = _TYPE_RE.search(text)
    if match is None:
        return None
    turn_type = match.group(1).strip().upper()
    if turn_type not in _DIRECTIVE_TYPES:
        return None
    density_match = _DENSITY_RE.search(text)
    density = density_match.group(1).strip().lower() if density_match else None
    require_attended = _body_requires_attended(text)
    evidence_required_uris = _extract_evidence_required_uris(text)
    return ParsedDirective(
        turn_type=turn_type,
        density=density,
        require_attended=require_attended,
        evidence_required_uris=evidence_required_uris,
        raw_body=text,
    )


def build_sdk_message(job_body: str, *, contract: str) -> str:
    """Prompt text handed to nested cursor-sdk dispatch."""
    from services.git_integration_worker.cursor_auto.reporting_contract import (
        reporting_contract_lines,
    )

    body = job_body.strip()
    lines = [
        "Nested cursor-sdk dispatch from cursor-auto (operator proxy).",
        f"contract={contract}",
        "",
        body,
        *reporting_contract_lines(),
    ]
    if contract in {"implement", "investigate", "verify"}:
        lines.extend(
            [
                "",
                "## Lane-A checkpoint (mandatory on closeout)",
                "In §2 emit `checkpoint_claim:` — your belief about authorship:",
                "`checkpoint_claim: committed <sha> paths=N` (optional `(+M pending)`),",
                "`checkpoint_claim: nothing_authored`, `checkpoint_claim: authored_cortex: …`,",
                "or `checkpoint_claim: deferred: <reason>`.",
                "Infrastructure injects the authoritative `checkpoint:` control line at relay",
                "assembly. Never `--all`. Commit clears lane authorship — not live/done gates.",
            ]
        )
    if contract == "confer":
        directive = parse_request_body(body)
        guard = sorted(corpus_guard_uris(directive))
        if guard:
            lines.extend(
                [
                    "",
                    "## Confer write fence (mandatory)",
                    "Forbidden durable write targets for this episode:",
                    *(f"- {uri}" for uri in guard),
                    "",
                    "Allowed alternatives:",
                    "- Write a distinct report sibling (e.g. …/confer-report.md) and "
                    "announce it in effects + deltas_to_spec, or",
                    "- Emit an explicit line: fence_exception: <uri> — <reason>",
                ]
            )
    return "\n".join(lines) + "\n"
