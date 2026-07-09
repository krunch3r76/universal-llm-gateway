"""session_close doc_type — template builder, attestation tokens, gate check."""

from __future__ import annotations

import hashlib
from typing import Any

from .ops_review_gate import _PRE_CLOSE_GATE_KINDS

_SESSION_CLOSE_VALIDATE_PASS = "session_close_validate:pass"
_SESSION_ID_PREFIX = "session_id:"
_SESSION_CLOSE_VERSION = "1.0.0"

_SESSION_CLOSE_SKILLS: tuple[str, ...] = (
    "session-close-kernel",
    "session-close-audit",
    "web-transcript-preprocessing",
)

_SESSION_CLOSE_REQUIRED_FIELDS: tuple[str, ...] = (
    "session_id",
    "agent",
    "session_summary_md",
    "summary",
)

_SESSION_CLOSE_OPTIONAL_FIELDS: tuple[str, ...] = (
    "transcript_depth",
    "transcript_jsonl_path",
    "transcript_md",
    "session_summary_md_path",
    "entity_ids",
    "defer_gaps",
    "handoff_prompt",
    "handoff_source_path",
    "assistant_label",
    "domains",
    "decisions",
    "open_items",
    "prior_session_id",
    "validate_attestation",
)

_TRANSCRIPT_DEPTH_RULES = """\
## Transcript depth rules

| Depth | Source required | Artifact | Handoff |
|---|---|---|---|
| `verbatim` (default) | `transcript_jsonl_path` (cursor) **or** `transcript_md` (web) | dual-layer file + entity | allowed (≥ light) |
| `light` | `session_summary_md` only | structural file + entity | allowed |
| `none` | none — journal + continues edge only | no transcript file/entity | **forbidden** (422 `handoff.requires_transcript_entity`) |

- `write_handoff ⟹ depth ≠ none` — handoff lives on the transcript entity.
- Web seats: compose `transcript_md` from the context window when no file exists.
- Cursor seats: prefer `transcript_jsonl_path` (see session-close.mdc Step 0).
"""


def _content_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def _resolve_skill_body_bytes(slug: str) -> bytes | None:
    from implement_admission.closeout_helpers import workspaces_root
    from implement_admission.skill_source_table import resolve_canonical_source_uri

    uri = resolve_canonical_source_uri(slug)
    candidates: list[str] = []
    if uri.startswith("workspaces://"):
        candidates.append(uri.removeprefix("workspaces://"))
    elif uri.startswith("agent-skills/"):
        candidates.extend([uri, f"universal-llm-gateway/{uri}"])
    else:
        candidates.append(uri)
    root = workspaces_root()
    for rel in candidates:
        path = (root / rel.lstrip("/")).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError:
                return None
    return None


def skill_digest(slug: str) -> str:
    body = _resolve_skill_body_bytes(slug)
    if body is not None:
        return _content_digest(body)
    return _content_digest(f"agent-skills/{slug}.md".encode())


def _skill_pointer_block() -> str:
    lines = ["## Canonical skill pointers\n"]
    for slug in _SESSION_CLOSE_SKILLS:
        lines.append(f"- Load skill: `{slug}`")
        lines.append(f"  Skill digest: `{skill_digest(slug)}`")
    return "\n".join(lines) + "\n"


def _audit_codes_block() -> str:
    codes = "\n".join(f"- `{code}`" for code in _PRE_CLOSE_GATE_KINDS)
    return (
        "## Pre-close audit gate codes (13)\n\n"
        "Imported from `_PRE_CLOSE_GATE_KINDS` — do not duplicate inline.\n\n"
        f"{codes}\n"
    )


def _fields_block() -> str:
    required = "\n".join(f"- `{field}`" for field in _SESSION_CLOSE_REQUIRED_FIELDS)
    optional = "\n".join(f"- `{field}`" for field in _SESSION_CLOSE_OPTIONAL_FIELDS)
    return (
        "## Required fields\n\n"
        f"{required}\n\n"
        "## Optional fields\n\n"
        f"{optional}\n"
    )


def build_session_close_template(*, platform_note: str = "") -> str:
    platform_section = f"\n## Platform overlay\n\n{platform_note}\n" if platform_note else ""
    return (
        "# Session close payload contract\n\n"
        f"{_fields_block()}\n"
        f"{_TRANSCRIPT_DEPTH_RULES}\n"
        f"{_audit_codes_block()}\n"
        f"{_skill_pointer_block()}\n"
        f"{platform_section}"
        "## Post-fill attestation checklist\n\n"
        "1. Run `doc_validate(doc_type=\"session_close\", …)` with the close payload.\n"
        "2. On PASS, copy `attestation_tokens` to the `validate_attestation` arg.\n"
        "3. Run `session_close(…, validate_attestation=[…])` — atomic write is refused without PASS.\n"
    )


def build_session_close_template_web() -> str:
    return build_session_close_template(
        platform_note=(
            "**Web seat:** no JSONL on disk — compose `transcript_md` from the context "
            "window at `light` or `verbatim`. Load `web-transcript-preprocessing` before "
            "composing. Reserve `depth=none` only for trivial sessions (no decisions, "
            "entities, or handoff)."
        ),
    )


def build_session_close_template_cursor() -> str:
    return build_session_close_template(
        platform_note=(
            "**Cursor seat:** supply `transcript_jsonl_path` for verbatim closes "
            "(session-close.mdc Step 0). Server assembles verbatim from JSONL."
        ),
    )


def session_close_pedagogy_digest(*, platform_note: str = "") -> str:
    joined = build_session_close_template(platform_note=platform_note)
    return _content_digest(joined.encode("utf-8"))


def session_close_attestation_tokens(*, session_id: str) -> list[str]:
    return [
        _SESSION_CLOSE_VALIDATE_PASS,
        f"{_SESSION_ID_PREFIX}{session_id}",
    ]


def check_session_close_validate_attestation(
    *,
    session_id: str,
    validate_attestation: list[str] | None,
) -> dict[str, Any] | None:
    """Return an error dict when attestation is missing or mismatched."""
    if not validate_attestation:
        return {
            "error": (
                "session_close requires doc_validate PASS attestation — run "
                "doc_validate(doc_type=\"session_close\", …) first, then pass "
                "attestation_tokens as validate_attestation"
            ),
            "reason": "session_close_validate_attestation_missing",
            "status_code": 422,
        }
    tokens = [t for t in validate_attestation if isinstance(t, str)]
    if _SESSION_CLOSE_VALIDATE_PASS not in tokens:
        return {
            "error": (
                "validate_attestation must include session_close_validate:pass "
                "from doc_validate PASS"
            ),
            "reason": "session_close_validate_attestation_missing",
            "status_code": 422,
        }
    expected_session = f"{_SESSION_ID_PREFIX}{session_id}"
    if expected_session not in tokens:
        return {
            "error": (
                f"validate_attestation session_id mismatch — expected {expected_session!r}"
            ),
            "reason": "session_close_validate_session_mismatch",
            "status_code": 422,
        }
    return None


__all__ = [
    "_SESSION_CLOSE_OPTIONAL_FIELDS",
    "_SESSION_CLOSE_REQUIRED_FIELDS",
    "_SESSION_CLOSE_SKILLS",
    "_SESSION_CLOSE_VERSION",
    "build_session_close_template",
    "build_session_close_template_cursor",
    "build_session_close_template_web",
    "check_session_close_validate_attestation",
    "session_close_attestation_tokens",
    "session_close_pedagogy_digest",
    "skill_digest",
]
