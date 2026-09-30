"""Primary sub-op index for ``tool_search`` — umbrella verbs as first-class hits.

Server-primary dispatchers (``cortex``, ``rag``, ``fs``, …) are advertised on
``tools/list`` but their verbs are not: ``tool_search(query="friction")`` used
to surface only overflow tools, so a seat that knew the verb still had to grep
the repo or read a full descriptor for the call shape (friction a:36916).

This module indexes named sub-ops of umbrella tools and returns the **direct
call shape** (``cortex(tool="friction", arguments='…')``), never a
``dispatch(tool=…)`` template — dispatch rejects primaries. Entries stay out
of ``tool_search._MANIFEST`` (overflow only, by contract and by test); they are
merged into ``results`` at search time.

Call shapes and required args trace to the handlers they describe:
``cortex_store.dispatch_ops._doc_required_by_op`` (friction / assert /
entity_create), ``server.rag`` (search), ``tools/filesystem/_fs_dispatch``
(md_read). Add an entry only when the op is a server-primary verb; the
required-args list must match the handler's validation set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

PRIMARY_OP_NOTE = (
    "Server-primary op — call the primary tool directly by name with this "
    "shape. Do NOT route through dispatch(tool=...); it rejects primaries."
)


@dataclass(frozen=True)
class PrimaryOpEntry:
    primary_tool: str
    op: str
    purpose: str
    call_shape: str
    required_args: tuple[str, ...]
    keywords: frozenset[str] = field(default_factory=frozenset)
    optional_args_hint: str = ""

    @property
    def name(self) -> str:
        return f"{self.primary_tool}.{self.op}"


_CORTEX_FRICTION = PrimaryOpEntry(
    primary_tool="cortex",
    op="friction",
    purpose=(
        "File a friction observation on an owning entity (service:*, "
        "agent_skill:*, ai_agent:*); protocol category needs an anchor."
    ),
    call_shape=(
        'cortex(tool="friction", arguments=\'{"owner": "service:<slug>", '
        '"category": "<feature|tool_error|schema_gap|doc_drift|protocol|…>", '
        '"note": "<observation>", "suggestion": "<optional ask>", '
        '"evidence_uris": ["agent-bus:<thread>"], "actionable": true}\')'
    ),
    required_args=("owner", "note"),
    optional_args_hint=(
        "category ∈ {tool_mismatch, tool_absent, tool_error, schema_gap, "
        "boot_drift, lesson_gap, lesson_conflict, stale_context, doc_drift, "
        "protocol, regression, feature}; claim aliases note; service aliases "
        "owner; protocol category requires {charter_root, window_index} or "
        "{root_thread, cp_ordinal}."
    ),
    keywords=frozenset(
        {"friction", "file", "filing", "observation", "report", "gap", "owner"}
    ),
)

_CORTEX_ASSERT = PrimaryOpEntry(
    primary_tool="cortex",
    op="assert",
    purpose="Write a claim onto a Cortex entity with confidence and evidence.",
    call_shape=(
        'cortex(tool="assert", arguments=\'{"entity_id": "<type:slug>", '
        '"claim": "<one claim>", '
        '"confidence": "<confirmed|believed|suspected|hypothesized>", '
        '"evidence": "<where this was observed>", '
        '"evidence_uris": ["agent-bus:<thread>"]}\')'
    ),
    required_args=("entity_id", "claim", "confidence", "evidence"),
    optional_args_hint=(
        "seeded_by, derivation_type, supersedes_id, dry_run, attributes; "
        "confidence is an enum, not a float (confidence_score is the float)."
    ),
    keywords=frozenset({"assert", "assertion", "claim", "record", "pin", "graph"}),
)

_CORTEX_ENTITY_CREATE = PrimaryOpEntry(
    primary_tool="cortex",
    op="entity_create",
    purpose="Mint a new Cortex entity (todo, decision, service, spec, …).",
    call_shape=(
        'cortex(tool="entity_create", arguments=\'{"id": "<type:slug>", '
        '"type": "<todo|decision|service|spec|…>", "name": "<title>", '
        '"description": "<optional>"}\')'
    ),
    required_args=("id", "type", "name"),
    optional_args_hint=(
        "description, status, workflow_state, notes, aliases, attributes, "
        "source_uri; trait fields are rejected at create."
    ),
    keywords=frozenset(
        {"entity_create", "entity", "create", "mint", "new", "todo", "decision"}
    ),
)

_RAG_SEARCH = PrimaryOpEntry(
    primary_tool="rag",
    op="search",
    purpose="Semantic corpus retrieval — the only agent surface for RAG.",
    call_shape=(
        'rag(op="search", arguments=\'{"query": "<natural language>", '
        '"scope": "<scope>", "limit": 20}\')'
    ),
    required_args=("query",),
    optional_args_hint="scope XOR prefix; limit aliases top_k; mapped=true for durable packs.",
    keywords=frozenset(
        {"rag", "search", "corpus", "retrieval", "semantic", "research"}
    ),
)

_FS_MD_READ = PrimaryOpEntry(
    primary_tool="fs",
    op="md_read",
    purpose="Read one markdown section from a sandboxed file (cortex or workspaces).",
    call_shape=(
        'fs(sandbox="<cortex|workspaces>", op="md_read", '
        'path="<relative/path.md>", section="<## heading path>")'
    ),
    required_args=("sandbox", "path"),
    optional_args_hint="section selects one heading; omit for the whole file.",
    keywords=frozenset({"fs", "md_read", "read", "markdown", "section", "file"}),
)

PRIMARY_OP_INDEX: tuple[PrimaryOpEntry, ...] = (
    _CORTEX_FRICTION,
    _CORTEX_ASSERT,
    _CORTEX_ENTITY_CREATE,
    _RAG_SEARCH,
    _FS_MD_READ,
)


def _tokenize(query: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(query.lower()) if len(t) >= 2]


def search_primary_ops(query: str, limit: int = 5) -> list[PrimaryOpEntry]:
    """Score sub-op entries against the query; verb-exact matches rank first.

    Same weights as ``tool_search_matcher.search_manifest`` so a hit here is
    comparable to an overflow hit: op name in tokens +12, tool name +6,
    keyword +5, purpose substring +2.
    """
    tokens = _tokenize(query)
    if not tokens:
        return []
    scored: list[tuple[int, str, PrimaryOpEntry]] = []
    for entry in PRIMARY_OP_INDEX:
        score = 0
        if entry.op in tokens or entry.name in tokens:
            score += 12
        if entry.primary_tool in tokens:
            score += 6
        purpose_lower = entry.purpose.lower()
        for tok in tokens:
            if tok in entry.keywords:
                score += 5
            if tok in purpose_lower:
                score += 2
        if score > 0:
            scored.append((score, entry.name, entry))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, _, e in scored[:limit]]


def verb_named_in_query(query: str, hits: list[PrimaryOpEntry]) -> bool:
    """True when the query names a hit's op verbatim — those hits lead ``results``."""
    tokens = set(_tokenize(query))
    return any(h.op in tokens or h.name in tokens for h in hits)


def primary_op_to_response(entry: PrimaryOpEntry) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": entry.name,
        "kind": "primary_op",
        "primary_tool": entry.primary_tool,
        "op": entry.op,
        "purpose": entry.purpose,
        "call_shape": entry.call_shape,
        "required_args": list(entry.required_args),
        "note": PRIMARY_OP_NOTE,
    }
    if entry.optional_args_hint:
        out["optional_args_hint"] = entry.optional_args_hint
    return out
