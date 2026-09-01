"""CHECKPOINT body projection — derived zone from machine state at post time."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .checkpoint_citation_lint import CitationToken, lint_checkpoint_citations
from .checkpoint_projection_lanes import render_lane_derived_sections
from .turns_models import MAX_TURN_BODY_CHARS

CANONICAL_RESUME_FOOTER = (
    "— RESUME (any seat, no command): load checkpoint-discipline "
    "(tip resume + author workflow; done/close claims also load "
    "agent-bus-discipline § R12 completeness gate; cursor coding arc may add "
    "orchestrator-workflow) → read <roadmap path> [+ scoreboard gated lane if "
    "named] → this is the latest CHECKPOINT (wave/in-flight/next above). Do not "
    "read the thread linearly. empty Next-pickup ≠ arc complete."
)

# checkpoint-discipline mandates parameterizing the continuity-source URI per
# arc, so an authored footer almost never equals the canonical literal. Match on
# the prefix `checkpoint_schema.parse` already binds as canonical (schema
# §3.1.1 / Align-2) rather than on the constant.
RESUME_FOOTER_PREFIX = "— RESUME (any seat, no command):"

_DERIVED_HEADER = "## Derived (projected at post — do not hand-edit)"
_RESIDUE_HEADER = "## Residue (authored"
# CCL-4 fail-open: stamp only when a resolver raises (graph/fs unreachable).
# Missing/unresolvable referents omit the row — they do not degrade the tip.
_UNPROJECTED_BANNER = (
    "> **UNPROJECTED** — graph or fs unreachable at post; derived rows below "
    "are not verified."
)
_CLAIM_HEAD_MAX = 120
# Trailing markdown wrappers / sentence punctuation must not poison the URI
# (CP11: backtick-wrapped handoff → sha miss → false UNPROJECTED).
_URI_RE = re.compile(r"(?:cortex|workspaces)://[^\s)\]>`'\"]+")
_URI_TRAIL_JUNK = ".,;:!?'\"`"
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_CLOSED_THREAD_STATUSES = frozenset({"closed"})
# CCL-4 staleness adjudication — closed set that survives objection (i).
STALENESS_FIELDS = frozenset(
    {"superseded_by", "valid_until", "newer_assertion_on_entity"}
)


@dataclass(frozen=True, slots=True)
class ChildThreadRow:
    thread_id: str
    status: str
    last_turn: int
    lane_role: str | None = None
    parent_thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactAnchor:
    uri: str
    sha256: str


@dataclass(frozen=True, slots=True)
class EntityAssertionRow:
    row_id: str
    entity: str
    claim_head: str
    confidence: float | None = None
    superseded_by: str | None = None
    valid_until: str | None = None
    newer_on_entity: bool = False


class ChildRegistryResolver(Protocol):
    def __call__(
        self, *, root_thread: str, cited_thread_ids: tuple[str, ...]
    ) -> tuple[tuple[ChildThreadRow, ...], tuple[ChildThreadRow, ...]]: ...


class ArtifactShaResolver(Protocol):
    def __call__(self, uri: str) -> ArtifactAnchor | None: ...


class CitationRowResolver(Protocol):
    def __call__(self, token: CitationToken) -> EntityAssertionRow | None: ...


@dataclass(frozen=True, slots=True)
class ProjectionResolvers:
    child_registry: ChildRegistryResolver
    artifact_sha: ArtifactShaResolver
    citation_row: CitationRowResolver


class CheckpointBodyTooLargeError(Exception):
    """Projected CHECKPOINT exceeds soft spill limit after compression."""

    def __init__(self, *, body_chars: int, limit_chars: int) -> None:
        self.envelope: dict[str, object] = {
            "code": "checkpoint_body_too_large",
            "reason": "checkpoint_body_too_large",
            "body_chars": body_chars,
            "limit_chars": limit_chars,
            "message": (
                f"Projected CHECKPOINT body is {body_chars:,} chars after "
                f"compression; soft limit is {limit_chars:,}. Trim residue or "
                "reduce cited rows — load-bearing derived rows are never dropped."
            ),
            "retryable": True,
            "source": "agent_bus_store.checkpoint_projection",
        }
        super().__init__(self.envelope["message"])


def is_checkpoint_subject(subject: str) -> bool:
    return subject.strip().upper().startswith("CHECKPOINT")


def _strip_resume_footer(text: str) -> str:
    """Drop authored RESUME footer lines, keeping any trailing machine fence.

    Truncating from the footer to end-of-text would swallow a trailing
    ``charter-state`` fence, which harvest still validates on charter-runner
    CHECKPOINTs.
    """
    kept = [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith(RESUME_FOOTER_PREFIX)
    ]
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(kept))


def _residue_after_derived(body: str) -> str:
    """Return the body with any prior projected derived zone removed."""
    text = body.strip()
    derived_idx = text.find(_DERIVED_HEADER)
    if derived_idx >= 0:
        text = text[derived_idx + len(_DERIVED_HEADER) :]
        residue_idx = text.find(_RESIDUE_HEADER)
        if residue_idx >= 0:
            text = text[residue_idx:]
            newline = text.find("\n")
            text = text[newline + 1 :] if newline >= 0 else ""
    return text


def extract_authored_residue(body: str) -> str:
    """Return authored residue text, stripping any prior derived zone and footer."""
    return _strip_resume_footer(_residue_after_derived(body)).strip()


def extract_authored_resume_footer(body: str) -> str | None:
    """Return the author's RESUME footer line, parameterized or literal, if present.

    The projection re-emits this rather than the canonical constant so a
    per-arc continuity URI survives the round trip instead of being replaced by
    the constant's unresolved ``<roadmap path>`` placeholder.
    """
    for line in _residue_after_derived(body).splitlines():
        if line.lstrip().startswith(RESUME_FOOTER_PREFIX):
            return line.strip()
    return None


def authored_residue_char_count(body: str) -> int:
    return len(extract_authored_residue(body))


def _normalize_artifact_uri(raw: str) -> str:
    return raw.rstrip(_URI_TRAIL_JUNK)


def _extract_artifact_uris(body: str) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _URI_RE.finditer(body):
        uri = _normalize_artifact_uri(match.group(0))
        if uri and uri not in seen:
            seen.add(uri)
            ordered.append(uri)
    return tuple(ordered)


def _cited_thread_ids(tokens: tuple[CitationToken, ...]) -> tuple[str, ...]:
    return tuple(
        token.identifier
        for token in tokens
        if token.kind == "agent_bus"
    )


def _truncate_claim(text: str, limit: int = _CLAIM_HEAD_MAX) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _render_anchor(anchor: ArtifactAnchor) -> str:
    return f"- {anchor.uri} · sha256:{anchor.sha256}"


def _render_unresolved_anchor(uri: str) -> str:
    return f"- {uri} · unresolved"


def _render_entity_row(row: EntityAssertionRow) -> str:
    flags: list[str] = []
    if row.superseded_by:
        flags.append(f"superseded_by={row.superseded_by}")
    if row.valid_until:
        flags.append(f"valid_until={row.valid_until}")
    if row.newer_on_entity:
        flags.append("newer_assertion_on_entity")
    conf = "" if row.confidence is None else f" · conf={row.confidence:.2f}"
    flag_text = f" · {' · '.join(flags)}" if flags else ""
    return (
        f"- {row.row_id} · {row.entity} · "
        f"\"{_truncate_claim(row.claim_head)}\"{conf}{flag_text}"
    )


def _render_derived_zone(
    *,
    unprojected: bool,
    child_lanes: tuple[ChildThreadRow, ...],
    cited_lanes: tuple[ChildThreadRow, ...],
    anchors: tuple[ArtifactAnchor, ...],
    unresolved_uris: tuple[str, ...],
    rows: tuple[EntityAssertionRow, ...],
    compress_closed_children: bool,
) -> str:
    parts = [_DERIVED_HEADER]
    if unprojected:
        parts.append(_UNPROJECTED_BANNER)
    parts.append("")
    parts.extend(
        render_lane_derived_sections(
            child_lanes=child_lanes,
            cited_lanes=cited_lanes,
            compress_closed_children=compress_closed_children,
        )
    )
    parts.append("")
    parts.append("### Artifact anchors")
    if anchors or unresolved_uris:
        parts.extend(_render_anchor(a) for a in anchors)
        parts.extend(_render_unresolved_anchor(u) for u in unresolved_uris)
    else:
        parts.append("_none cited_")
    parts.append("")
    parts.append("### Entity / assertion rows")
    if rows:
        parts.extend(_render_entity_row(r) for r in rows)
    else:
        parts.append("_none cited_")
    return "\n".join(parts)


def project_checkpoint_body(
    *,
    root_thread: str,
    residue: str,
    resolvers: ProjectionResolvers,
) -> str:
    """Materialize derived zone + residue + exactly one RESUME footer."""
    clean_residue = extract_authored_residue(residue)
    resume_footer = extract_authored_resume_footer(residue) or CANONICAL_RESUME_FOOTER
    findings = lint_checkpoint_citations(clean_residue)
    cited_threads = _cited_thread_ids(findings.citation_tokens)
    uris = _extract_artifact_uris(clean_residue)

    unprojected = False
    try:
        child_lanes, cited_lanes = resolvers.child_registry(
            root_thread=root_thread, cited_thread_ids=cited_threads
        )
    except Exception:
        child_lanes, cited_lanes = (), ()
        unprojected = True

    anchors: list[ArtifactAnchor] = []
    unresolved_uris: list[str] = []
    for uri in uris:
        try:
            anchor = resolvers.artifact_sha(uri)
        except Exception:
            unprojected = True
            unresolved_uris.append(uri)
            continue
        if anchor is not None:
            anchors.append(anchor)
        else:
            unresolved_uris.append(uri)

    entity_rows: list[EntityAssertionRow] = []
    for token in findings.citation_tokens:
        if token.kind == "agent_bus":
            continue
        try:
            row = resolvers.citation_row(token)
        except Exception:
            unprojected = True
            continue
        if row is not None:
            entity_rows.append(row)

    return _assemble_body(
        unprojected=unprojected,
        child_lanes=child_lanes,
        cited_lanes=cited_lanes,
        anchors=tuple(anchors),
        unresolved_uris=tuple(unresolved_uris),
        rows=tuple(entity_rows),
        residue=clean_residue,
        resume_footer=resume_footer,
        compress_closed_children=False,
    )


def _assemble_body(
    *,
    unprojected: bool,
    child_lanes: tuple[ChildThreadRow, ...],
    cited_lanes: tuple[ChildThreadRow, ...],
    anchors: tuple[ArtifactAnchor, ...],
    unresolved_uris: tuple[str, ...],
    rows: tuple[EntityAssertionRow, ...],
    residue: str,
    resume_footer: str,
    compress_closed_children: bool,
) -> str:
    derived = _render_derived_zone(
        unprojected=unprojected,
        child_lanes=child_lanes,
        cited_lanes=cited_lanes,
        anchors=anchors,
        unresolved_uris=unresolved_uris,
        rows=rows,
        compress_closed_children=compress_closed_children,
    )
    body = "\n\n".join(
        [
            derived,
            f"{_RESIDUE_HEADER} — cap ~800 chars)\n{residue}",
            resume_footer,
        ]
    )
    if len(body) <= MAX_TURN_BODY_CHARS:
        return body
    all_lanes = child_lanes + cited_lanes
    if compress_closed_children or not all_lanes:
        raise CheckpointBodyTooLargeError(
            body_chars=len(body), limit_chars=MAX_TURN_BODY_CHARS
        )
    return _assemble_body(
        unprojected=unprojected,
        child_lanes=child_lanes,
        cited_lanes=cited_lanes,
        anchors=anchors,
        unresolved_uris=unresolved_uris,
        rows=rows,
        residue=residue,
        resume_footer=resume_footer,
        compress_closed_children=True,
    )


__all__ = [
    "CANONICAL_RESUME_FOOTER",
    "RESUME_FOOTER_PREFIX",
    "STALENESS_FIELDS",
    "ArtifactAnchor",
    "CheckpointBodyTooLargeError",
    "ChildThreadRow",
    "EntityAssertionRow",
    "ProjectionResolvers",
    "authored_residue_char_count",
    "extract_authored_residue",
    "extract_authored_resume_footer",
    "is_checkpoint_subject",
    "project_checkpoint_body",
]
