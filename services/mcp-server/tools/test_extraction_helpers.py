"""Tests for ``tools._sidecar_schema.validate_sidecar_frontmatter``.

Covers phase-b acceptance criterion (2) — schema validation unit tests pass —
for plan_phase:document-ingestion-redesign/phase-b. Verifies the helper's
contract against the pinned schema at
cortex://configs/schemas/extraction-sidecar-v1.yaml.

Hermetic by design: each test copies the schema fixture from
``services/mcp-server/testdata/extraction-sidecar-v1.yaml`` into ``tmp_path``
and monkeypatches ``_sidecar_schema.FILES_ROOT`` to point there, so tests
run without any /data/files mount. The fixture file mirrors the canonical
schema verbatim — if the canonical schema bumps versions, the fixture moves
with it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tools import _sidecar_schema
from tools._sidecar_schema import (
    ValidationResult,
    validate_sidecar_frontmatter,
)

_SCHEMA_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "testdata"
    / "extraction-sidecar-v1.yaml"
)

# A reference SHA-256 hex string (64 lowercase hex chars) and 6-char prefix.
_VALID_SHA = "9f3a12c8e0b41a" + "0" * 50
_VALID_PREFIX = "9f3a12"

# Minimal valid frontmatter — matches the spec's worked example in
# §"Sidecar naming, partial extractions, and variant artifacts > Frontmatter".
_VALID_FRONTMATTER: dict[str, Any] = {
    "naming_version": 1,
    "canonical": True,
    "partial": False,
    "page_spec": "all",
    "args_hash": None,
    "args_hash_prefix": None,
    "default_profile": "document-extraction-v1",
    "source_path": "dropbox/cortex_legal/2026-05-19/bill.pdf",
    "source_sha256": _VALID_SHA,
    "source_size": 184523,
    "extracted_at": "2026-05-19T09:47:14Z",
    "model": "openai/gpt-5.4",
    "dpi": 200,
    "pages": "all",
    "prompt_hash": _VALID_SHA,
    "extraction_type": "ocr_transcription",
    "tool_version": "extract_document/1.0",
}

# The 15 fields the schema marks `required`. Used to parametrize the
# missing-field test so a schema change shows up as a test failure rather
# than a silent loss of coverage.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "naming_version",
    "canonical",
    "partial",
    "page_spec",
    "default_profile",
    "source_path",
    "source_sha256",
    "source_size",
    "extracted_at",
    "model",
    "dpi",
    "pages",
    "prompt_hash",
    "extraction_type",
    "tool_version",
)


@pytest.fixture(autouse=True)
def _schema_in_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Install the schema fixture under ``tmp_path`` and reset the loader cache."""
    schema_dir = tmp_path / "configs" / "schemas"
    schema_dir.mkdir(parents=True)
    schema_path = schema_dir / "extraction-sidecar-v1.yaml"
    schema_path.write_text(_SCHEMA_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(_sidecar_schema, "FILES_ROOT", tmp_path)
    _sidecar_schema._load_schema.cache_clear()
    yield schema_path
    _sidecar_schema._load_schema.cache_clear()


def _frontmatter(**overrides: Any) -> dict[str, Any]:
    """Return a deep copy of the valid frontmatter with ``overrides`` applied."""
    base = copy.deepcopy(_VALID_FRONTMATTER)
    base.update(overrides)
    return base


def test_valid_frontmatter_passes() -> None:
    result = validate_sidecar_frontmatter(_frontmatter())
    assert result == ValidationResult(ok=True, errors=())


def test_valid_with_populated_args_hash_passes() -> None:
    result = validate_sidecar_frontmatter(
        _frontmatter(args_hash=_VALID_SHA, args_hash_prefix=_VALID_PREFIX)
    )
    assert result.ok is True
    assert result.errors == ()


def test_valid_with_page_array_passes() -> None:
    """``pages`` as a list of positive ints satisfies the oneOf branch."""
    result = validate_sidecar_frontmatter(_frontmatter(pages=[2, 3, 4]))
    assert result.ok is True


@pytest.mark.parametrize("field", _REQUIRED_FIELDS)
def test_missing_required_field_fails(field: str) -> None:
    """Each of the 15 required fields must independently fail validation."""
    incomplete = _frontmatter()
    incomplete.pop(field)
    result = validate_sidecar_frontmatter(incomplete)
    assert result.ok is False
    assert any(field in err for err in result.errors), (
        f"expected error message to name missing field {field!r}; "
        f"got {result.errors!r}"
    )


def test_naming_version_const_violation_fails() -> None:
    """``naming_version`` must equal 1; version bumps require a schema swap."""
    result = validate_sidecar_frontmatter(_frontmatter(naming_version=2))
    assert result.ok is False
    assert any("/naming_version" in err for err in result.errors)


def test_canonical_wrong_type_fails() -> None:
    """``canonical`` must be a boolean, not a truthy string."""
    result = validate_sidecar_frontmatter(_frontmatter(canonical="true"))
    assert result.ok is False
    assert any("/canonical" in err for err in result.errors)


def test_dpi_below_minimum_fails() -> None:
    """``dpi`` minimum is 72; lower values reject."""
    result = validate_sidecar_frontmatter(_frontmatter(dpi=50))
    assert result.ok is False
    assert any("/dpi" in err for err in result.errors)


def test_source_size_negative_fails() -> None:
    """``source_size`` minimum is 0; negative sizes reject."""
    result = validate_sidecar_frontmatter(_frontmatter(source_size=-1))
    assert result.ok is False
    assert any("/source_size" in err for err in result.errors)


def test_source_sha256_pattern_violation_fails() -> None:
    """``source_sha256`` must match the 64-hex pattern."""
    result = validate_sidecar_frontmatter(_frontmatter(source_sha256="not-a-hash"))
    assert result.ok is False
    assert any("/source_sha256" in err for err in result.errors)


def test_args_hash_prefix_wrong_length_fails() -> None:
    """``args_hash_prefix`` must be exactly 6 hex chars when non-null."""
    result = validate_sidecar_frontmatter(_frontmatter(args_hash_prefix="9f3a"))
    assert result.ok is False
    assert any("/args_hash_prefix" in err for err in result.errors)


def test_pages_array_with_zero_fails() -> None:
    """``pages`` array items must be >= 1; 0 fails the oneOf branch."""
    result = validate_sidecar_frontmatter(_frontmatter(pages=[0, 1, 2]))
    assert result.ok is False
    assert any("/pages" in err for err in result.errors)


def test_pages_invalid_string_fails() -> None:
    """``pages`` as a string other than ``"all"`` fails both oneOf branches."""
    result = validate_sidecar_frontmatter(_frontmatter(pages="2-4"))
    assert result.ok is False
    assert any("/pages" in err for err in result.errors)


def test_additional_properties_rejected() -> None:
    """``additionalProperties: false`` rejects unknown top-level fields."""
    result = validate_sidecar_frontmatter(_frontmatter(unexpected_field="x"))
    assert result.ok is False
    assert any("unexpected_field" in err for err in result.errors)


def test_non_dict_input_returns_structured_error() -> None:
    """Non-mapping inputs short-circuit before schema validation."""
    result = validate_sidecar_frontmatter([])  # type: ignore[arg-type]
    assert result.ok is False
    assert result.errors == ("/: frontmatter must be a mapping, got list",)


def test_multiple_violations_all_surface() -> None:
    """All schema violations report; validation isn't fail-on-first."""
    broken = _frontmatter(dpi=10, source_size=-5, canonical="nope")
    result = validate_sidecar_frontmatter(broken)
    assert result.ok is False
    pointers = [err.split(":", 1)[0] for err in result.errors]
    assert "/dpi" in pointers
    assert "/source_size" in pointers
    assert "/canonical" in pointers


def test_schema_load_is_cached(_schema_in_tmp: Path) -> None:
    """``_load_schema`` returns identical objects across calls (lru_cache)."""
    first = _sidecar_schema._load_schema()
    second = _sidecar_schema._load_schema()
    assert first is second


def test_schema_load_survives_file_deletion(_schema_in_tmp: Path) -> None:
    """Once cached, the schema persists even if the source file is removed.

    Reflects the spec's restart-bound migration contract: a running service
    holds its schema; on-disk swaps don't take effect until cache clear or
    process restart.
    """
    _sidecar_schema._load_schema()  # prime the cache
    _schema_in_tmp.unlink()
    # No FileNotFoundError because the schema is cached.
    result = validate_sidecar_frontmatter(_frontmatter())
    assert result.ok is True


def test_missing_schema_file_raises() -> None:
    """On a cold cache with no schema on disk, the loader raises."""
    _sidecar_schema._load_schema.cache_clear()
    schema_path = (
        _sidecar_schema.FILES_ROOT
        / "configs"
        / "schemas"
        / "extraction-sidecar-v1.yaml"
    )
    schema_path.unlink()

    with pytest.raises(FileNotFoundError, match="Sidecar schema not found"):
        validate_sidecar_frontmatter(_frontmatter())
