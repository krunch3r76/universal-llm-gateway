"""Tests for ``tools._extract_document_helpers`` (phase-c.2).

Covers the path-composition, frontmatter-build, atomic-write, idempotency,
and profile-load helpers backing the ``extract_document`` handler. Handler
integration tests (which require mocking the OCR + PDF parser backends) are
deferred to the c.3 deploy smoke test.

Hermetic — the only helper that touches the filesystem at module load is
``load_default_profile``; its tests install a fake profile under
``tmp_path`` and monkeypatch ``FILES_ROOT``. All other helpers operate on
paths or dicts passed directly.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools import _extraction_profile as extraction_profile
from tools._extract_document_helpers import (
    TOOL_VERSION,
    atomic_write,
    build_frontmatter,
    build_sidecar_path,
    check_idempotent,
    compute_source_sha256,
    format_sidecar,
)
from tools._extraction_profile import DefaultProfile, hash_prompt, load_default_profile
from tools._sidecar_naming import ArgsHash, PageSpec
from tools._sidecar_schema import SIDECAR_SUFFIX

# ─── Fixture data ────────────────────────────────────────────────────────────

_VALID_SHA = "9f3a12c8e0b41a" + "0" * 50  # 64 hex chars
_OTHER_SHA = "abcdef0123456789" + "0" * 48
_VALID_ARGS_HASH = "deadbeef" + "0" * 56  # 64 hex chars
_VALID_ARGS_PREFIX = _VALID_ARGS_HASH[:6]


_DEFAULT_PROFILE = DefaultProfile(
    profile="document-extraction-v1",
    model="openai/gpt-5.4",
    dpi=200,
    prompt="Extract all text faithfully.",
    extraction_type="ocr_transcription",
)


def _full_page_spec() -> PageSpec:
    return PageSpec(filename_infix="", is_full=True, pages_for_frontmatter="all")


def _partial_page_spec(infix: str = "2-4", pages: list[int] | None = None) -> PageSpec:
    return PageSpec(
        filename_infix=infix,
        is_full=False,
        pages_for_frontmatter=pages if pages is not None else [2, 3, 4],
    )


def _no_args_hash() -> ArgsHash:
    return ArgsHash(full_hash=None, prefix=None, normalized_args={})


def _variant_args_hash() -> ArgsHash:
    return ArgsHash(
        full_hash=_VALID_ARGS_HASH,
        prefix=_VALID_ARGS_PREFIX,
        normalized_args={"dpi": 300},
    )


# ─── build_sidecar_path ──────────────────────────────────────────────────────


def test_canonical_sidecar_path_omits_both_infixes(tmp_path: Path) -> None:
    """Spec example: canonical full extraction lands at ``bill.pdf.extracted.md``."""
    source = tmp_path / "dropbox" / "bill.pdf"
    result = build_sidecar_path(source, _full_page_spec(), _no_args_hash())
    assert result == source.parent / "bill.pdf.extracted.md"


def test_partial_sidecar_path_adds_pages_infix(tmp_path: Path) -> None:
    """Spec example: ``bill.pdf.pages-2-4.extracted.md``."""
    source = tmp_path / "bill.pdf"
    result = build_sidecar_path(source, _partial_page_spec("2-4"), _no_args_hash())
    assert result.name == "bill.pdf.pages-2-4.extracted.md"


def test_variant_sidecar_path_adds_args_infix(tmp_path: Path) -> None:
    """Spec example: ``bill.pdf.args-a1b2c3.extracted.md``."""
    source = tmp_path / "bill.pdf"
    result = build_sidecar_path(source, _full_page_spec(), _variant_args_hash())
    assert result.name == f"bill.pdf.args-{_VALID_ARGS_PREFIX}.extracted.md"


def test_combined_infixes_appear_in_fixed_order(tmp_path: Path) -> None:
    """Spec example: ``.pages-`` before ``.args-`` always."""
    source = tmp_path / "bill.pdf"
    result = build_sidecar_path(
        source,
        _partial_page_spec("2-4"),
        _variant_args_hash(),
    )
    expected = f"bill.pdf.pages-2-4.args-{_VALID_ARGS_PREFIX}.extracted.md"
    assert result.name == expected


def test_image_basename_includes_suffix(tmp_path: Path) -> None:
    """Spec: ``screenshot.png.extracted.md`` — full basename prevents collision
    with ``screenshot.pdf.extracted.md``."""
    source = tmp_path / "screenshot.png"
    result = build_sidecar_path(source, _full_page_spec(), _no_args_hash())
    assert result.name == "screenshot.png.extracted.md"


def test_long_page_spec_hash_renders_with_h_prefix(tmp_path: Path) -> None:
    """Long-spec fallback infix renders as ``.pages-h<sha12>.``."""
    source = tmp_path / "contract.pdf"
    long_spec = PageSpec(
        filename_infix="h91c2e88ab731",
        is_full=False,
        pages_for_frontmatter=[1, 4, 7, 10, 13, 16, 19, 22, 25],
    )
    result = build_sidecar_path(source, long_spec, _no_args_hash())
    assert result.name == "contract.pdf.pages-h91c2e88ab731.extracted.md"


def test_sidecar_suffix_constant() -> None:
    """The sidecar suffix matches the spec: ``.extracted.md``."""
    assert SIDECAR_SUFFIX == ".extracted.md"


# ─── build_frontmatter ───────────────────────────────────────────────────────


_FROZEN_NOW = datetime(2026, 5, 23, 21, 0, 0, tzinfo=UTC)


def _frontmatter(
    page_spec: PageSpec | None = None,
    args_hash: ArgsHash | None = None,
) -> dict[str, Any]:
    return build_frontmatter(
        source_path_rel="dropbox/cortex_legal/2026-05-19/bill.pdf",
        source_sha256=_VALID_SHA,
        source_size=184523,
        page_spec=page_spec or _full_page_spec(),
        args_hash=args_hash or _no_args_hash(),
        profile=_DEFAULT_PROFILE,
        effective_model="openai/gpt-5.4",
        effective_dpi=200,
        effective_prompt_hash=_VALID_SHA,
        now=_FROZEN_NOW,
    )


def test_canonical_frontmatter_flags() -> None:
    """Full extraction + default args → ``canonical: true, partial: false``."""
    fm = _frontmatter()
    assert fm["canonical"] is True
    assert fm["partial"] is False
    assert fm["page_spec"] == "all"
    assert fm["pages"] == "all"
    assert fm["args_hash"] is None
    assert fm["args_hash_prefix"] is None


def test_partial_only_frontmatter_flags() -> None:
    """Partial extraction with default args → ``canonical: false, partial: true``."""
    fm = _frontmatter(page_spec=_partial_page_spec("2-4"))
    assert fm["canonical"] is False
    assert fm["partial"] is True
    assert fm["page_spec"] == "2-4"
    assert fm["pages"] == [2, 3, 4]


def test_variant_only_frontmatter_flags() -> None:
    """Full extraction with variant args → ``canonical: false, partial: false``.

    Both flags false marks "this is a variant of a full extraction" — exactly
    one extraction per source can be canonical.
    """
    fm = _frontmatter(args_hash=_variant_args_hash())
    assert fm["canonical"] is False
    assert fm["partial"] is False
    assert fm["args_hash"] == _VALID_ARGS_HASH
    assert fm["args_hash_prefix"] == _VALID_ARGS_PREFIX


def test_partial_plus_variant_frontmatter_flags() -> None:
    """Both partial AND variant: ``canonical: false, partial: true``."""
    fm = _frontmatter(
        page_spec=_partial_page_spec("2-4"),
        args_hash=_variant_args_hash(),
    )
    assert fm["canonical"] is False
    assert fm["partial"] is True


def test_frontmatter_extracted_at_uses_z_suffix() -> None:
    """ISO 8601 with literal ``Z`` for UTC, not ``+00:00``."""
    fm = _frontmatter()
    assert fm["extracted_at"] == "2026-05-23T21:00:00Z"


def test_frontmatter_carries_tool_version_constant() -> None:
    """``tool_version`` field tracks the module constant."""
    fm = _frontmatter()
    assert fm["tool_version"] == TOOL_VERSION
    assert TOOL_VERSION == "extract_document/1.0"


def test_frontmatter_default_profile_field() -> None:
    """``default_profile`` carries the profile identifier from the input."""
    fm = _frontmatter()
    assert fm["default_profile"] == "document-extraction-v1"


def test_frontmatter_has_all_15_required_fields() -> None:
    """All schema-required fields present (schema v1 — extraction-sidecar-v1.yaml)."""
    fm = _frontmatter()
    required = {
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
    }
    assert required.issubset(fm.keys())
    assert fm["naming_version"] == 1


# ─── format_sidecar ──────────────────────────────────────────────────────────


def test_format_sidecar_yaml_roundtrips() -> None:
    """The YAML block between the ``---`` markers must parse back to the input dict."""
    fm = _frontmatter()
    body = "# Extracted content\n\nSome text here.\n"
    output = format_sidecar(fm, body)
    assert output.startswith("---\n")
    end_idx = output.find("\n---\n")
    assert end_idx > 0
    yaml_block = output[4:end_idx]
    parsed = yaml.safe_load(yaml_block)
    assert parsed == fm
    # Body follows the closing marker + blank line.
    assert output.endswith(body)


# ─── atomic_write ────────────────────────────────────────────────────────────


def test_atomic_write_creates_file_with_content(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "sidecar.md"
    atomic_write(target, "hello world\n")
    assert target.read_text(encoding="utf-8") == "hello world\n"


def test_atomic_write_creates_parent_directories(tmp_path: Path) -> None:
    """Parent dirs created on demand — extract paths can include fresh dropbox dirs."""
    target = tmp_path / "a" / "b" / "c" / "sidecar.md"
    atomic_write(target, "x")
    assert target.is_file()


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    """The ``.tmp`` file is consumed by the atomic rename."""
    target = tmp_path / "sidecar.md"
    atomic_write(target, "x")
    assert target.is_file()
    assert not (tmp_path / "sidecar.md.tmp").exists()


def test_atomic_write_overwrites_existing_file(tmp_path: Path) -> None:
    """Re-writing an existing target replaces its contents."""
    target = tmp_path / "sidecar.md"
    target.write_text("old", encoding="utf-8")
    atomic_write(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


# ─── check_idempotent ────────────────────────────────────────────────────────


def _write_sidecar(
    path: Path,
    *,
    source_sha256: str = _VALID_SHA,
    page_spec: str = "all",
    args_hash: str | None = None,
) -> None:
    """Write a minimally-valid sidecar for idempotency tests."""
    fm = {
        "source_sha256": source_sha256,
        "page_spec": page_spec,
        "args_hash": args_hash,
    }
    yaml_block = yaml.safe_dump(fm, sort_keys=False)
    path.write_text(f"---\n{yaml_block}---\n\nbody", encoding="utf-8")


def test_idempotent_returns_false_when_missing(tmp_path: Path) -> None:
    """No sidecar → caller writes fresh."""
    assert (
        check_idempotent(
            tmp_path / "missing.extracted.md",
            source_sha256=_VALID_SHA,
            page_spec_str="all",
            args_hash_full=None,
        )
        is False
    )


def test_idempotent_returns_true_on_full_match(tmp_path: Path) -> None:
    """All three identity fields match → no rewrite."""
    sidecar = tmp_path / "bill.pdf.extracted.md"
    _write_sidecar(sidecar)
    assert (
        check_idempotent(
            sidecar,
            source_sha256=_VALID_SHA,
            page_spec_str="all",
            args_hash_full=None,
        )
        is True
    )


def test_idempotent_returns_false_on_sha_mismatch(tmp_path: Path) -> None:
    """Source changed under us — caller auto-replaces, no collision raise."""
    sidecar = tmp_path / "bill.pdf.extracted.md"
    _write_sidecar(sidecar, source_sha256=_VALID_SHA)
    result = check_idempotent(
        sidecar,
        source_sha256=_OTHER_SHA,
        page_spec_str="all",
        args_hash_full=None,
    )
    assert result is False


def test_idempotent_raises_on_page_spec_collision(tmp_path: Path) -> None:
    """Matching filename + matching SHA but differing page_spec → schema collision."""
    sidecar = tmp_path / "bill.pdf.extracted.md"
    _write_sidecar(sidecar, page_spec="all")
    with pytest.raises(ValueError, match="page_spec"):
        check_idempotent(
            sidecar,
            source_sha256=_VALID_SHA,
            page_spec_str="2-4",
            args_hash_full=None,
        )


def test_idempotent_raises_on_args_hash_collision(tmp_path: Path) -> None:
    """Matching filename + matching SHA + matching page_spec but differing args_hash."""
    sidecar = tmp_path / "bill.pdf.extracted.md"
    _write_sidecar(sidecar, args_hash=None)
    with pytest.raises(ValueError, match="args_hash"):
        check_idempotent(
            sidecar,
            source_sha256=_VALID_SHA,
            page_spec_str="all",
            args_hash_full=_VALID_ARGS_HASH,
        )


def test_idempotent_returns_false_for_malformed_sidecar(tmp_path: Path) -> None:
    """No ``---`` opening → treat as no-match, caller overwrites."""
    sidecar = tmp_path / "garbage.md"
    sidecar.write_text("not a sidecar at all", encoding="utf-8")
    assert (
        check_idempotent(
            sidecar,
            source_sha256=_VALID_SHA,
            page_spec_str="all",
            args_hash_full=None,
        )
        is False
    )


def test_idempotent_returns_false_for_broken_yaml(tmp_path: Path) -> None:
    """Opening ``---`` but invalid YAML inside → no-match."""
    sidecar = tmp_path / "broken.md"
    sidecar.write_text("---\n  :: not valid yaml ::\n---\n\nbody", encoding="utf-8")
    assert (
        check_idempotent(
            sidecar,
            source_sha256=_VALID_SHA,
            page_spec_str="all",
            args_hash_full=None,
        )
        is False
    )


# ─── hash_prompt + compute_source_sha256 ─────────────────────────────────────


def test_hash_prompt_is_deterministic() -> None:
    assert hash_prompt("hello") == hash_prompt("hello")


def test_hash_prompt_returns_64_hex_chars() -> None:
    result = hash_prompt("anything")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_prompt_matches_sha256_of_utf8() -> None:
    """Spec: SHA-256 of the prompt text encoded as UTF-8."""
    expected = hashlib.sha256(b"test prompt").hexdigest()
    assert hash_prompt("test prompt") == expected


def test_compute_source_sha256_against_known_bytes(tmp_path: Path) -> None:
    """SHA-256 of ``b"hello world"`` is known."""
    source = tmp_path / "f.txt"
    source.write_bytes(b"hello world")
    sha, size = compute_source_sha256(source)
    expected_sha = hashlib.sha256(b"hello world").hexdigest()
    assert sha == expected_sha
    assert size == 11


def test_compute_source_sha256_streams_large_files(tmp_path: Path) -> None:
    """Verifies streaming works for files larger than the 64KB chunk size."""
    source = tmp_path / "big.bin"
    payload = b"a" * (65536 * 3 + 17)  # >3 chunks
    source.write_bytes(payload)
    sha, size = compute_source_sha256(source)
    assert sha == hashlib.sha256(payload).hexdigest()
    assert size == len(payload)


# ─── load_default_profile ────────────────────────────────────────────────────


_PROFILE_YAML = """\
profile: document-extraction-v1
model: openai/gpt-5.4
dpi: 200
prompt: |
  Extract all text faithfully.
extraction_type: ocr_transcription
"""


@pytest.fixture
def _profile_in_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Install a hermetic profile at ``tmp_path/configs/...`` and reset the cache."""
    profile_dir = tmp_path / "configs"
    profile_dir.mkdir(parents=True)
    profile_path = profile_dir / "document-extraction-v1.yaml"
    profile_path.write_text(_PROFILE_YAML, encoding="utf-8")
    monkeypatch.setattr(extraction_profile, "FILES_ROOT", tmp_path)
    extraction_profile.load_default_profile.cache_clear()
    yield profile_path
    extraction_profile.load_default_profile.cache_clear()


def test_load_default_profile_returns_dataclass(_profile_in_tmp: Path) -> None:
    profile = load_default_profile()
    assert profile.profile == "document-extraction-v1"
    assert profile.model == "openai/gpt-5.4"
    assert profile.dpi == 200
    assert profile.extraction_type == "ocr_transcription"


def test_load_default_profile_is_cached(_profile_in_tmp: Path) -> None:
    first = load_default_profile()
    second = load_default_profile()
    assert first is second


def test_load_default_profile_raises_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold cache + missing file → FileNotFoundError."""
    monkeypatch.setattr(extraction_profile, "FILES_ROOT", tmp_path)
    extraction_profile.load_default_profile.cache_clear()
    with pytest.raises(FileNotFoundError, match="Default extraction profile"):
        load_default_profile()


def test_default_profile_as_args_dict_hashes_prompt() -> None:
    """``as_args_dict_for_hashing`` swaps ``prompt`` for ``prompt_hash``."""
    args_dict = _DEFAULT_PROFILE.as_args_dict_for_hashing()
    assert "prompt" not in args_dict
    assert args_dict["prompt_hash"] == hash_prompt(_DEFAULT_PROFILE.prompt)
    assert args_dict["model"] == _DEFAULT_PROFILE.model
    assert args_dict["dpi"] == _DEFAULT_PROFILE.dpi
    assert args_dict["extraction_type"] == _DEFAULT_PROFILE.extraction_type
