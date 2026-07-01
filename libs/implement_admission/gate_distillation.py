"""Gate-2 implement-admission distillation helpers (pure, offline-testable)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from implement_admission.dense_spec_schema import (
    DENSE_SPEC_RE,
    DenseSpecVerdict,
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.scheme_resolve import resolve_schemed_packet_file
from implement_admission.share_uri_emit import to_share_uri

_REJECTED_SPEC_PREFIXES = ("packet:", "agent-bus:")
_ULG_DIRNAME = "universal-llm-gateway"


def todo_slug(todo_id: str) -> str:
    """``todo:foo-bar`` → ``foo-bar``."""
    return todo_id.removeprefix("todo:")


def default_dense_spec_uri(todo_id: str) -> str:
    """Canonical dense-spec path for a todo slug."""
    return f"tasks/specs/{todo_slug(todo_id)}.md"


def _rejected_spec_source(source_uri: str) -> bool:
    lower = source_uri.strip().lower()
    return lower.startswith(_REJECTED_SPEC_PREFIXES)


def normalize_dense_spec_path(source_uri: str | None, *, todo_id: str) -> str:
    """Resolve canonical Share URI for ``tasks/specs/{slug}.md``."""
    canonical = default_dense_spec_uri(todo_id)
    if not source_uri or not str(source_uri).strip():
        return to_share_uri("workspaces", canonical)

    uri = str(source_uri).strip().removeprefix("files://")
    match = DENSE_SPEC_RE.search(uri)
    if not match:
        return to_share_uri("workspaces", canonical)

    cited = match.group(0)
    if PurePosixPath(cited).name == PurePosixPath(canonical).name:
        if "://" in uri:
            return uri if uri.startswith("workspaces://") else to_share_uri("workspaces", cited)
        return to_share_uri("workspaces", cited)
    return to_share_uri("workspaces", canonical)


def _repo_candidates(root: Path) -> tuple[Path, ...]:
    root = root.resolve()
    nested = root / _ULG_DIRNAME
    if nested.is_dir() and nested != root:
        return (root, nested)
    return (root,)


def read_dense_spec_text(
    spec_path: str,
    *,
    workspaces_root_path: Path | None = None,
) -> str | None:
    """Read dense-spec prose via shared scheme resolver."""
    candidate = resolve_schemed_packet_file(
        spec_path, workspaces_root_override=workspaces_root_path
    )
    if candidate is None:
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return None


def build_implement_ready_evidence_uris(spec_path: str, spec_text: str) -> list[str]:
    """Evidence tokens ``evaluate_implement_ready`` requires (path + content hash)."""
    return [spec_path, dense_spec_hash_uri(spec_text)]


@dataclass(frozen=True, slots=True)
class GateDistillationInputs:
    todo_id: str
    spec_path: str
    spec_text: str
    evidence_uris: list[str]
    schema: DenseSpecVerdict


def prepare_gate_distillation(
    *,
    todo_id: str,
    source_uri: str | None = None,
    workspaces_root_path: Path | None = None,
) -> GateDistillationInputs | tuple[str, str]:
    """Load + validate dense spec; return inputs or ``(code, reason)`` on failure."""
    if not todo_id.startswith("todo:"):
        return ("invalid_todo_id", f"{todo_id!r} must be todo:{{slug}}")

    if source_uri and str(source_uri).strip():
        raw = str(source_uri).strip()
        if _rejected_spec_source(raw):
            return (
                "implement_spec_source_rejected",
                f"{todo_id}: dense spec source_uri must be tasks/specs/{{slug}}.md, "
                f"not {raw!r}",
            )

    spec_path = normalize_dense_spec_path(source_uri, todo_id=todo_id)
    spec_text = read_dense_spec_text(
        spec_path, workspaces_root_path=workspaces_root_path
    )
    if spec_text is None:
        return (
            "implement_spec_unreadable",
            f"{todo_id}: dense spec at {spec_path} could not be read",
        )

    schema = validate_dense_spec(spec_text)
    if not schema.passed:
        return (
            schema.code or "implement_spec_not_dense",
            f"{todo_id}: {spec_path} fails dense-spec schema "
            f"({schema.code}: {schema.reason})",
        )

    return GateDistillationInputs(
        todo_id=todo_id,
        spec_path=spec_path,
        spec_text=spec_text,
        evidence_uris=build_implement_ready_evidence_uris(spec_path, spec_text),
        schema=schema,
    )


__all__ = [
    "GateDistillationInputs",
    "build_implement_ready_evidence_uris",
    "default_dense_spec_uri",
    "normalize_dense_spec_path",
    "prepare_gate_distillation",
    "read_dense_spec_text",
    "todo_slug",
]
