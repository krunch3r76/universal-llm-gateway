"""Sidecar shape — schema validation, frontmatter parsing, suffix constant.

The sidecar grammar's read-time concerns live here. Used by both
``extract_document`` (write-time validation before the atomic sidecar
write) and ``promote_document_to_evidence`` (read-time validation before
moving the sidecar into evidence).

- ``validate_sidecar_frontmatter`` — JSON Schema 2020-12 validation against
  the pinned schema at ``cortex://configs/schemas/extraction-sidecar-v1.yaml``.
- ``parse_leading_frontmatter`` — extract the ``---``-delimited YAML block
  from a sidecar's text content.
- ``SIDECAR_SUFFIX`` — canonical suffix (``.extracted.md``).

Naming-grammar helpers (page-spec normalization, args-hash) live in
``_sidecar_naming``. Profile load and prompt hashing live in
``extraction_profile``.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Final

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from ._file_helpers import FILES_ROOT

# Canonical sidecar suffix per spec §"Naming grammar".
SIDECAR_SUFFIX: Final[str] = ".extracted.md"

# Sidecar schema path on the cortex files mount.
#
# The cortex:// URI ``cortex://configs/schemas/extraction-sidecar-v1.yaml``
# resolves to ``/data/files/configs/schemas/extraction-sidecar-v1.yaml`` —
# this constant is the latter form so the resolution matches FILES_ROOT.
_SCHEMA_RELATIVE_PATH = "configs/schemas/extraction-sidecar-v1.yaml"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of a single ``validate_sidecar_frontmatter`` call.

    The dataclass shape lets callers distinguish "valid" from "invalid with
    these specific reasons" without raising — both ``extract_document`` and
    ``promote_document_to_evidence`` are fail-closed at their respective
    boundaries but emit structured errors (per ``[quality]`` exception
    handling) rather than swallowing or re-wrapping low-level
    ``jsonschema`` exceptions.

    Attributes:
        ok: ``True`` iff the frontmatter satisfies the schema. When ``True``,
            ``errors`` is empty.
        errors: Human-readable error messages, one per schema violation, in
            JSON-pointer document order. Each entry is prefixed with the
            JSON pointer to the offending field (or ``/`` for root-level
            violations) for debuggability.
    """

    ok: bool
    errors: tuple[str, ...]


@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Load and cache the pinned sidecar schema.

    Cached for the process lifetime. Service restart is required after a
    schema bump (v1 -> v2 path change) — matching the spec's contract that
    "old sidecars retain their version; readers tolerate prior versions until
    full migration".
    """
    schema_path = FILES_ROOT / _SCHEMA_RELATIVE_PATH
    if not schema_path.is_file():
        raise FileNotFoundError(
            f"Sidecar schema not found at {schema_path!s}. "
            "Expected at cortex://configs/schemas/extraction-sidecar-v1.yaml."
        )
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = yaml.safe_load(handle)
    if not isinstance(schema, dict):
        raise ValueError(
            f"Sidecar schema root must be a mapping, got {type(schema).__name__}."
        )
    return schema


def validate_sidecar_frontmatter(
    frontmatter_dict: dict[str, Any],
) -> ValidationResult:
    """Validate parsed sidecar frontmatter against extraction-sidecar-v1.

    Used at two boundaries:

    - Write-time, in ``extract_document``: refuses to write a sidecar whose
      frontmatter does not validate (fail-closed; no silent fallback per
      ``[quality]`` exception handling).
    - Read-time, in ``promote_document_to_evidence``: refuses to promote a
      sidecar whose frontmatter does not validate.

    Both call sites should treat ``result.ok is False`` as terminal for the
    current operation and surface ``result.errors`` to the caller.

    Args:
        frontmatter_dict: The parsed YAML frontmatter from a sidecar markdown
            file (the block between the opening and closing ``---`` markers,
            parsed via ``yaml.safe_load``). Must be a mapping at the top
            level; the schema enforces shape and types beneath that.

    Returns:
        A ``ValidationResult`` with ``ok=True`` and an empty ``errors`` tuple
        when the dict conforms to the schema, or ``ok=False`` with one error
        string per violation otherwise.
    """
    if not isinstance(frontmatter_dict, dict):
        return ValidationResult(
            ok=False,
            errors=(
                f"/: frontmatter must be a mapping, got "
                f"{type(frontmatter_dict).__name__}",
            ),
        )

    schema = load_schema()
    validator = Draft202012Validator(schema)
    raw_errors: list[JsonSchemaValidationError] = sorted(
        validator.iter_errors(frontmatter_dict),
        key=lambda err: list(err.absolute_path),
    )
    if not raw_errors:
        return ValidationResult(ok=True, errors=())

    formatted = tuple(_format_error(err) for err in raw_errors)
    return ValidationResult(ok=False, errors=formatted)


def _format_error(err: JsonSchemaValidationError) -> str:
    """Render a jsonschema error with its JSON-pointer path."""
    if err.absolute_path:
        pointer = "/" + "/".join(str(segment) for segment in err.absolute_path)
    else:
        pointer = "/"
    return f"{pointer}: {err.message}"


def parse_leading_frontmatter(content: str) -> dict[str, Any] | None:
    """Extract and parse the leading ``---``-delimited YAML block.

    Returns ``None`` when the block is absent, malformed, or non-mapping —
    callers treat any of those as "no existing match" and write fresh.
    """
    if not content.startswith("---\n"):
        return None
    end_marker = content.find("\n---\n", 4)
    if end_marker < 0:
        return None
    fm_text = content[4:end_marker]
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    return fm
