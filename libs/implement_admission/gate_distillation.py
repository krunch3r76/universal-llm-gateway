"""Gate-2 implement-admission distillation helpers (pure, offline-testable).

Dense-spec path contract (``decision:cortex-spec-gaps-bundle-v1`` Bind 1): identity
is the cited ``source_uri``; home is ``cortex://notes/system/specs/``; basename is
preserved; every distill payload discloses ``path_resolution``. ``{slug}.md`` is the
default when ``source_uri`` is empty or non-spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from implement_admission.dense_spec_schema import (
    DENSE_SPEC_RE,
    DenseSpecVerdict,
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.scheme_resolve import resolve_schemed_packet_file
from implement_admission.share_uri_emit import to_share_uri

_REJECTED_SPEC_PREFIXES = ("packet:", "agent-bus:")
_RETIRED_SPEC_LOCUS = "tasks/specs/"
_ULG_DIRNAME = "universal-llm-gateway"


@dataclass(frozen=True, slots=True)
class DenseSpecPathResolution:
    cited: str | None
    resolved: str
    action: str
    basename_nonstandard: bool


class GateDistillationFailure(NamedTuple):
    code: str
    reason: str
    path_resolution: DenseSpecPathResolution | None


def todo_slug(todo_id: str) -> str:
    """``todo:foo-bar`` → ``foo-bar``."""
    return todo_id.removeprefix("todo:")


def default_dense_spec_uri(todo_id: str) -> str:
    """Canonical dense-spec path for a todo slug."""
    return f"notes/system/specs/{todo_slug(todo_id)}.md"


def _rejected_spec_source(source_uri: str) -> bool:
    lower = source_uri.strip().lower()
    return lower.startswith(_REJECTED_SPEC_PREFIXES)


def resolve_dense_spec_path(
    source_uri: str | None, *, todo_id: str
) -> DenseSpecPathResolution:
    """Resolve cited dense-spec identity to canonical cortex Share URI."""
    canonical = default_dense_spec_uri(todo_id)
    default = to_share_uri("cortex", canonical)
    raw = (source_uri or "").strip()
    if not raw:
        return DenseSpecPathResolution(None, default, "defaulted_empty_source", False)

    uri = raw.removeprefix("files://")
    match = DENSE_SPEC_RE.search(uri)
    if not match:
        return DenseSpecPathResolution(raw, default, "defaulted_non_spec_source", False)

    cited_path = match.group(0)
    basename = PurePosixPath(cited_path).name
    resolved = to_share_uri("cortex", f"notes/system/specs/{basename}")
    nonstandard = basename != PurePosixPath(canonical).name
    if cited_path.lower().startswith(_RETIRED_SPEC_LOCUS):
        action = "relocated_retired_home"
    elif raw == resolved:
        action = "as_cited"
    else:
        action = "scheme_normalized"
    return DenseSpecPathResolution(raw, resolved, action, nonstandard)


def normalize_dense_spec_path(source_uri: str | None, *, todo_id: str) -> str:
    """Canonical cortex Share URI for the cited dense spec — basename preserved."""
    return resolve_dense_spec_path(source_uri, todo_id=todo_id).resolved


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
    path_resolution: DenseSpecPathResolution


def prepare_gate_distillation(
    *,
    todo_id: str,
    source_uri: str | None = None,
    workspaces_root_path: Path | None = None,
) -> GateDistillationInputs | GateDistillationFailure:
    """Load + validate dense spec; return inputs or failure on error."""
    if not todo_id.startswith("todo:"):
        return GateDistillationFailure(
            "invalid_todo_id",
            f"{todo_id!r} must be todo:{{slug}}",
            None,
        )

    resolution = resolve_dense_spec_path(source_uri, todo_id=todo_id)

    if source_uri and str(source_uri).strip():
        raw = str(source_uri).strip()
        if _rejected_spec_source(raw):
            return GateDistillationFailure(
                "implement_spec_source_rejected",
                (
                    f"{todo_id}: dense spec source_uri must cite "
                    f"cortex://notes/system/specs/*.md, not {raw!r}"
                ),
                resolution,
            )

    spec_path = resolution.resolved
    spec_text = read_dense_spec_text(
        spec_path, workspaces_root_path=workspaces_root_path
    )
    if spec_text is None:
        return GateDistillationFailure(
            "implement_spec_unreadable",
            f"{todo_id}: dense spec at {spec_path} could not be read",
            resolution,
        )

    schema = validate_dense_spec(spec_text)
    if not schema.passed:
        return GateDistillationFailure(
            schema.code or "implement_spec_not_dense",
            (
                f"{todo_id}: {spec_path} fails dense-spec schema "
                f"({schema.code}: {schema.reason})"
            ),
            resolution,
        )

    return GateDistillationInputs(
        todo_id=todo_id,
        spec_path=spec_path,
        spec_text=spec_text,
        evidence_uris=build_implement_ready_evidence_uris(spec_path, spec_text),
        schema=schema,
        path_resolution=resolution,
    )


__all__ = [
    "DenseSpecPathResolution",
    "GateDistillationFailure",
    "GateDistillationInputs",
    "build_implement_ready_evidence_uris",
    "default_dense_spec_uri",
    "normalize_dense_spec_path",
    "prepare_gate_distillation",
    "read_dense_spec_text",
    "resolve_dense_spec_path",
    "todo_slug",
]
