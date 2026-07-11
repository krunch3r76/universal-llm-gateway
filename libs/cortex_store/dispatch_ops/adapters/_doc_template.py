"""Cortex dispatch op: doc_template — dense-spec authoring skeleton."""

from __future__ import annotations

import hashlib
from typing import Any

from implement_admission import dense_spec_schema as _schema
from implement_admission.dense_spec_schema import validate_dense_spec

from .._doc_type_resolve import DocTypeRecord, resolve_doc_type
from .._session_close_doc_type import (
    _SESSION_CLOSE_REQUIRED_FIELDS,
    _SESSION_CLOSE_SKILLS,
    _SESSION_CLOSE_VERSION,
    build_session_close_template,
    build_session_close_template_cursor,
    build_session_close_template_web,
    session_close_pedagogy_digest,
)
from .._session_close_doc_type import (
    skill_digest as _session_close_skill_digest,
)
from .._session_close_validate import validate_session_close_payload

# Drift policy (Q3): attestation pins semantic template_version; skill_digest is
# tracked independently; template_sha256 is exact-drift detection only — a
# non-breaking pedagogy/skill edit that bumps template_version must NOT
# false-block a previously-attested spec when validate_dense_spec still PASSes.
_IMPLEMENT_DENSE_SPEC_VERSION = "1.0.0"
_IMPLEMENT_TODO_SKILL = "implement-todo"


def _content_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()[:16]}"


def _template_sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _resolve_skill_body_bytes(slug: str) -> bytes | None:
    from implement_admission.closeout_helpers import workspaces_root
    from implement_admission.skill_source_table import resolve_canonical_source_uri

    uri = resolve_canonical_source_uri(slug)
    candidates: list[str] = []
    if uri.startswith("workspaces://"):
        candidates.append(uri.removeprefix("workspaces://"))
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


def _skill_digest(slug: str) -> str:
    body = _resolve_skill_body_bytes(slug)
    if body is not None:
        return _content_digest(body)
    return _content_digest(f".cursor/skills/{slug}/SKILL.md".encode())


def _pedagogy_digest(*parts: str) -> str:
    joined = "\n".join(parts).encode("utf-8")
    return _content_digest(joined)


_SECTION_HEADINGS: dict[str, str] = {
    "problem": "## Problem",
    "non_goals": "## Non-goals",
    "provenance": "## Provenance — source-of-truth",
    "touch_points": "## Touch-points",
    "forks": "## Bound design decisions",
    "implementation": "## Implementation guidance",
    "acceptance": "## Acceptance criteria",
    "verification": "## Verification (quality gates)",
}

_HYBRID_PEDAGOGY_INLINE = """\
## Authoring pedagogy (hybrid — inline rubric)

- Every required section must use a heading that matches the accepted pattern
  comment above it; the validator is keyword-anchored, not key-name anchored.
- Close all forks in the bound-design section; the reasoning_trace body must
  contain the attestation phrase "no fork remains open".
- Anti-pattern: leaving unresolved fork markers, placeholder-only sections, or a missing
  reasoning_trace block — each fails validate_dense_spec mechanically.
- After fill: run doc_validate(text=…) or doc_validate(path=…) and record a
  doc_validate PASS attestation (template_version + spec_sha256) on the todo
  before implement dispatch.
"""


def _hybrid_pedagogy_pointer(*, skill_slug: str, skill_digest: str) -> str:
    return (
        "## Canonical skill pointer\n\n"
        f"Use the `{skill_slug}` skill\n"
        f"Skill digest: `{skill_digest}`\n"
        "Dense-spec accepted-pattern SOT: "
        "implement_admission.dense_spec_schema.validate_dense_spec\n"
    )


def _section_block(key: str) -> str:
    heading = _SECTION_HEADINGS[key]
    hint = _schema._SECTION_ACCEPTED_PATTERNS[key]
    return f"{heading}\n\n<!-- accepted pattern: {hint} -->\n\n[Author content here.]\n"


def _build_implement_dense_spec_template() -> str:
    skill_digest = _skill_digest(_IMPLEMENT_TODO_SKILL)
    sections = "".join(_section_block(key) for key in _schema._REQUIRED_SECTIONS)
    return (
        "# Dense implement spec\n\n"
        f"{sections}\n"
        "<reasoning_trace>\n\n"
        "<!-- reasoning_trace tag block required; body must contain: no fork remains open -->\n\n"
        "Record bound forks resolved. No fork remains open.\n\n"
        "</reasoning_trace>\n\n"
        f"{_HYBRID_PEDAGOGY_INLINE}\n"
        f"{_hybrid_pedagogy_pointer(skill_slug=_IMPLEMENT_TODO_SKILL, skill_digest=skill_digest)}\n"
        "## Post-fill attestation checklist\n\n"
        "1. Compute `spec_sha256:<hex>` via `dense_spec_hash_uri(filled_spec_text)`.\n"
        "2. Run `doc_validate` and record PASS attestation tokens "
        "(`doc_validate:pass`, `template_version:…`, `skill_digest:…`) on the todo.\n"
        "3. Record or supersede a confirmed `implement_ready` assertion citing the "
        "dense-spec path and the exact `spec_sha256` token.\n"
        "4. Distill `files_expected` and `acceptance_criteria` onto the todo at "
        "Gate-2 close.\n"
    )


def _implement_dense_spec_pedagogy_digest() -> str:
    skill_digest = _skill_digest(_IMPLEMENT_TODO_SKILL)
    return _pedagogy_digest(
        _HYBRID_PEDAGOGY_INLINE,
        _hybrid_pedagogy_pointer(
            skill_slug=_IMPLEMENT_TODO_SKILL,
            skill_digest=skill_digest,
        ),
    )


_DOC_TYPE_REGISTRY: dict[str, DocTypeRecord] = {
    "implement_dense_spec": DocTypeRecord(
        builder=_build_implement_dense_spec_template,
        schema=_schema,
        validator=validate_dense_spec,
        pedagogy_digest=_implement_dense_spec_pedagogy_digest(),
        template_version=_IMPLEMENT_DENSE_SPEC_VERSION,
        required_sections=list(_schema._REQUIRED_SECTIONS.keys()),
        skill_slugs=(_IMPLEMENT_TODO_SKILL,),
    ),
    "session_close": DocTypeRecord(
        builder=build_session_close_template,
        schema=None,
        validator=validate_session_close_payload,
        pedagogy_digest=session_close_pedagogy_digest(),
        template_version=_SESSION_CLOSE_VERSION,
        side_effect_binding="session_close",
        required_sections=list(_SESSION_CLOSE_REQUIRED_FIELDS),
        skill_slugs=_SESSION_CLOSE_SKILLS,
        variants={
            "web": {
                "builder": build_session_close_template_web,
                "pedagogy_digest": session_close_pedagogy_digest(
                    platform_note="web overlay"
                ),
                "metadata": {"platform": "web"},
            },
            "cursor": {
                "builder": build_session_close_template_cursor,
                "pedagogy_digest": session_close_pedagogy_digest(
                    platform_note="cursor overlay"
                ),
                "metadata": {"platform": "cursor"},
            },
        },
    ),
}

_SUPPORTED_DOC_TYPES: frozenset[str] = frozenset(_DOC_TYPE_REGISTRY)


def get_doc_type_record(doc_type: str) -> DocTypeRecord | None:
    resolved = resolve_doc_type(doc_type, _DOC_TYPE_REGISTRY)
    return resolved.record if resolved is not None else None


def current_template_version(doc_type: str) -> str | None:
    record = get_doc_type_record(doc_type)
    return record.template_version if record is not None else None


def current_skill_digest(doc_type: str) -> str | None:
    record = get_doc_type_record(doc_type)
    if record is None or not record.skill_slugs:
        return None
    if record.skill_slugs == (_IMPLEMENT_TODO_SKILL,):
        return _skill_digest(_IMPLEMENT_TODO_SKILL)
    digests = [_session_close_skill_digest(slug) for slug in record.skill_slugs]
    joined = "\n".join(digests).encode("utf-8")
    return _content_digest(joined)


def _op_doc_template(
    doc_type: str = "implement_dense_spec", **_: object
) -> dict[str, Any]:
    """Return a dense-spec skeleton that round-trips validate_dense_spec when filled."""
    resolved = resolve_doc_type(doc_type, _DOC_TYPE_REGISTRY)
    if resolved is None:
        supported = ", ".join(sorted(_DOC_TYPE_REGISTRY))
        return {
            "error": f"unknown doc_type {doc_type!r}; supported: {supported}",
            "status_code": 422,
        }
    record = resolved.record
    template = record.builder()
    normalized = resolved.base_key
    skill_digest = current_skill_digest(normalized)
    required_sections = record.required_sections or list(_schema._REQUIRED_SECTIONS.keys())
    return {
        "ok": True,
        "doc_type": resolved.requested,
        "template": template,
        "required_sections": required_sections,
        "template_version": record.template_version,
        "template_sha256": _template_sha256(template),
        "skill_digest": skill_digest,
        "pedagogy_digest": record.pedagogy_digest,
        **({"variant": resolved.variant} if resolved.variant else {}),
        **({"metadata": record.metadata} if record.metadata else {}),
    }


__all__ = [
    "DocTypeRecord",
    "_DOC_TYPE_REGISTRY",
    "_SUPPORTED_DOC_TYPES",
    "_op_doc_template",
    "current_skill_digest",
    "current_template_version",
    "get_doc_type_record",
]
