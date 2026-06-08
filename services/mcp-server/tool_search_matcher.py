"""Tool-search matcher primitives — parsers, scorers, dispatch-template renderers.

Pure functions extracted from ``tool_search.py`` to keep that module under
the 300-line SLOC budget. No FastMCP / no event-record dependencies — these
helpers operate on plain ``description``/``schema`` strings and produce
``ManifestEntry`` field values + match scores. ``tool_search_manifest`` wires
them into the build pipeline; ``tool_search`` registers the FastMCP tool.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tool_search_manifest import ManifestEntry

_OPS_LINE_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s+\(([^)]*)\)", re.IGNORECASE)
_EXAMPLE_RE = re.compile(r"(?:Examples?):\s*\n((?:[^\n]+\n?){1,3})", re.IGNORECASE)

# Hand-tuned keyword boosts for natural-language query→tool mapping. These
# survive description rewrites and are the load-bearing input for the
# golden-ranking test (test_tool_search_quality.py).
_KEYWORD_BOOSTS: dict[str, set[str]] = {
    "manage": {
        "restart",
        "service",
        "lifecycle",
        "health",
        "rebuild",
        "start",
        "stop",
        "sync_restart",
        "wait_healthy",
    },
    "observability": {
        "events",
        "query",
        "trace",
        "investigate",
        "logs",
        "recent",
        "failures",
    },
    "pipeline": {"poll", "result", "execution", "async", "wait", "execution_id"},
    "team_dispatch": {"role", "consult", "delegate", "team", "generate", "handoff"},
    "rag": {"corpus", "index", "research", "retrieve", "knowledge", "search"},
    "sql": {"sqlite", "raw", "database", "query"},
    "web_search": {"google", "internet", "web"},
    "web_fetch": {"url", "page", "fetch", "http", "html"},
    "model_status": {"models", "list", "status"},
    "boot_inspect": {"boot", "audit", "card", "briefing"},
    "cortex_boot": {"boot", "session", "warmup", "start"},
    "quality_gate": {"lint", "ruff", "test", "compile", "ci", "format"},
    "email": {
        "mailbox",
        "imap",
        "ingest",
        "message",
        "correspondence",
        "review_extract",
        "bridge",
        "sent",
        "create_folder",
        "folder",
    },
}

# Manifest overrides for overflow tools whose live schema/docstring under-specify
# the dispatch wire shape (op + nested JSON ``arguments`` string).
_MANIFEST_OVERRIDES: dict[str, dict[str, Any]] = {
    "email": {
        "dispatch_template": (
            'dispatch(tool="email", arguments=\'{"op": "review_extract", '
            '"arguments": "{\\"message_id\\": \\"<msg-id>\\"}"}\')'
        ),
        "example": (
            'dispatch(tool="email", arguments=\'{"op": "recent", '
            '"arguments": "{\\"mailbox\\": \\"Sent\\", \\"limit\\": 20}"}\')'
        ),
        "ops": [
            "list",
            "status",
            "get",
            "recent",
            "search",
            "archive",
            "pull",
            "pending",
            "ingest_one",
            "review_extract",
            "review_dismiss",
            "move",
            "create_folder",
            "retry",
        ],
        "required_args_by_op": {
            "get": ["message_id"],
            "ingest_one": ["message_id"],
            "review_extract": ["message_id"],
            "review_dismiss": ["message_id"],
            "move": ["message_ids", "folder"],
            "create_folder": ["folder"],
            "retry": ["message_ids"],
            "pull": ["mode"],
            "send": ["draft_id"],
            "draft_new": ["to", "subject", "body"],
            "draft_reply": ["source_message_id"],
            "draft_forward": ["source_message_id"],
            "draft_update": ["draft_id"],
            "get_draft": ["draft_id"],
            "draft_delete": ["draft_id"],
        },
    },
}


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    m = re.match(r"^(.+?[.!?])(?:\s|$)", line)
    return (m.group(1) if m else line).strip()


def _extract_ops_and_required_args(
    description: str, schema: dict[str, Any]
) -> tuple[list[str], dict[str, list[str]]]:
    """Parse the ``Operations:`` section of a docstring for op→required args.

    Tools follow a tabular convention: ``op_name (arg1, arg2?, arg3) — purpose``.
    Args ending with ``?`` are optional. Falls back to ``inputSchema.properties.<selector>.enum``
    if no Operations section is present.
    """
    ops: list[str] = []
    req: dict[str, list[str]] = {}
    in_ops = False
    for raw in (description or "").splitlines():
        low = raw.strip().lower()
        if low.startswith(("operations:", "standard ops:", "ops:")):
            in_ops = True
            continue
        if not in_ops:
            continue
        if not raw.strip():
            if ops:
                break
            continue
        if raw and not raw[0].isspace() and re.match(r"^[A-Z][a-zA-Z ]+:", raw):
            break
        m = _OPS_LINE_RE.match(raw)
        if m:
            op_name = m.group(1)
            args_blob = m.group(2)
            required = [
                a.strip()
                for a in args_blob.split(",")
                if a.strip() and not a.strip().endswith("?") and a.strip() != "..."
            ]
            ops.append(op_name)
            req[op_name] = required
    if not ops:
        props = (schema or {}).get("properties", {}) or {}
        for selector in ("op", "tool", "action", "operation"):
            enum = (props.get(selector) or {}).get("enum")
            if enum:
                ops = list(enum)
                break
    return ops, req


def _extract_example(description: str) -> str:
    m = _EXAMPLE_RE.search(description or "")
    if not m:
        return ""
    block = m.group(1).strip()
    if not block:
        return ""
    for line in block.splitlines():
        s = line.strip()
        if s.startswith(("dispatch(", "tool_search(")) or "(" in s:
            return s
    return block.splitlines()[0].strip()


def _render_dispatch_template(name: str, schema: dict[str, Any], ops: list[str]) -> str:
    override = _MANIFEST_OVERRIDES.get(name, {}).get("dispatch_template")
    if override:
        return str(override)
    props = (schema or {}).get("properties", {}) or {}
    selectors = [s for s in ("op", "tool", "action", "operation") if s in props]
    sample_op = ops[0] if ops else "list"
    op_token = f'"{sample_op}"'
    if "arguments" in props and selectors:
        sel = selectors[0]
        inner = (
            '{"message_id": "<msg-id>"}'
            if sample_op in {"get", "ingest_one", "review_extract", "review_dismiss"}
            else '{"mailbox": "Sent", "limit": 20}'
            if sample_op == "recent"
            else "{}"
        )
        inner_escaped = inner.replace('"', '\\"')
        return (
            f'dispatch(tool="{name}", arguments=\'{{"{sel}": {op_token}, '
            f'"arguments": "{inner_escaped}"}}\')'
        )
    if selectors:
        sel = selectors[0]
        return f'dispatch(tool="{name}", arguments=\'{{"{sel}": {op_token}, ...}}\')'
    return f"dispatch(tool=\"{name}\", arguments='{{...}}')"


def _derive_keywords(name: str, description: str) -> tuple[str, ...]:
    kws: set[str] = {name.lower()}
    kws.update(name.lower().split("_"))
    head = " ".join((description or "").split()[:40]).lower()
    for tok in re.findall(r"[a-z][a-z0-9_]{3,}", head):
        kws.add(tok)
    if name in _KEYWORD_BOOSTS:
        kws |= _KEYWORD_BOOSTS[name]
    return tuple(sorted(kws))


def _tokenize(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", query.lower()) if len(t) >= 2]


def search_manifest(
    manifest: dict[str, ManifestEntry], query: str, limit: int = 5
) -> list[ManifestEntry]:
    tokens = _tokenize(query)
    if not tokens:
        return []
    scored: list[tuple[int, str, ManifestEntry]] = []
    for entry in manifest.values():
        score = 0
        name_lower = entry.name.lower()
        if name_lower in tokens:
            score += 12
        for tok in tokens:
            if len(tok) >= 4 and tok in name_lower:
                score += 6
            if tok in entry.keywords:
                score += 5
            if tok in entry.purpose.lower():
                score += 2
        if score > 0:
            scored.append((score, entry.name, entry))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, _, e in scored[:limit]]


def _entry_to_response(e: ManifestEntry) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": e.name,
        "purpose": e.purpose,
        "dispatch_template": e.dispatch_template,
    }
    if e.ops:
        out["ops"] = e.ops
    if e.required_args_by_op:
        out["required_args_by_op"] = e.required_args_by_op
    if e.example:
        out["example"] = e.example
    return out


def _all_manifest_summary(
    manifest: dict[str, ManifestEntry],
) -> list[dict[str, str]]:
    return [{"name": e.name, "purpose": e.purpose} for e in manifest.values()]
