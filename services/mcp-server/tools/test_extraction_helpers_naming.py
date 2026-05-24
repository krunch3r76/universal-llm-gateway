"""Tests for the naming-grammar helpers in ``tools._sidecar_naming``.

Covers phase-c.1 acceptance: ``normalize_page_spec`` + ``compute_args_hash``.

Separate from ``test_extraction_helpers.py`` (which covers
``validate_sidecar_frontmatter`` and its schema-loader machinery in
``tools._sidecar_schema``) to keep each test module under the ``[quality]``
SLOC ceiling. No /data/files mount is needed for these tests — both helpers
are pure functions.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md, sections
"Sidecar naming, partial extractions, and variant artifacts > Page spec
normalization" and "Args hash".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from tools._sidecar_naming import (
    ArgsHash,
    PageSpec,
    compute_args_hash,
    normalize_page_spec,
)

# ─── normalize_page_spec ─────────────────────────────────────────────────────


def test_none_is_full_extraction() -> None:
    result = normalize_page_spec(None)
    assert result == PageSpec(
        filename_infix="",
        is_full=True,
        pages_for_frontmatter="all",
    )


def test_all_literal_is_full_extraction() -> None:
    result = normalize_page_spec("all")
    assert result == PageSpec(
        filename_infix="",
        is_full=True,
        pages_for_frontmatter="all",
    )


def test_list_covering_total_pages_collapses_to_full() -> None:
    """Spec example: ``[1,2,3,4]`` against a 4-page PDF → ``all``."""
    result = normalize_page_spec([1, 2, 3, 4], total_pages=4)
    assert result.is_full is True
    assert result.filename_infix == ""
    assert result.pages_for_frontmatter == "all"


def test_run_of_three_coalesces_to_range() -> None:
    """Spec example: ``[2,3,4] → 2-4``."""
    result = normalize_page_spec([2, 3, 4])
    assert result == PageSpec(
        filename_infix="2-4",
        is_full=False,
        pages_for_frontmatter=[2, 3, 4],
    )


def test_non_contiguous_pages_join_with_underscore() -> None:
    """Spec example: ``[1,3,5] → 1_3_5``."""
    result = normalize_page_spec([1, 3, 5])
    assert result.filename_infix == "1_3_5"
    assert result.is_full is False
    assert result.pages_for_frontmatter == [1, 3, 5]


def test_mixed_runs_and_singletons_render_correctly() -> None:
    """Spec example: ``[2,3,4,7,8,9] → 2-4_7-9``."""
    result = normalize_page_spec([2, 3, 4, 7, 8, 9])
    assert result.filename_infix == "2-4_7-9"
    assert result.pages_for_frontmatter == [2, 3, 4, 7, 8, 9]


def test_pair_renders_as_underscore_not_dash() -> None:
    """Two-page runs use the literal form; ``[2,3] → 2_3``, not ``2-3``."""
    result = normalize_page_spec([2, 3])
    assert result.filename_infix == "2_3"


def test_singleton_renders_as_page_number() -> None:
    result = normalize_page_spec([5])
    assert result.filename_infix == "5"
    assert result.pages_for_frontmatter == [5]


def test_unsorted_input_normalizes_to_sorted() -> None:
    result = normalize_page_spec([4, 2, 3])
    assert result.filename_infix == "2-4"
    assert result.pages_for_frontmatter == [2, 3, 4]


def test_duplicate_pages_deduplicate() -> None:
    result = normalize_page_spec([2, 2, 3, 4, 4])
    assert result.filename_infix == "2-4"
    assert result.pages_for_frontmatter == [2, 3, 4]


def test_complex_run_pattern() -> None:
    """Mixed singletons, pairs, and ranges in one input."""
    result = normalize_page_spec([1, 4, 5, 8, 9, 10, 15])
    # 1 alone, 4_5 pair, 8-10 range, 15 alone.
    assert result.filename_infix == "1_4_5_8-10_15"


def test_partial_with_total_pages_does_not_collapse() -> None:
    """A partial list doesn't collapse to ``all`` even when total_pages is given."""
    result = normalize_page_spec([2, 3], total_pages=10)
    assert result.is_full is False
    assert result.filename_infix == "2_3"


def test_long_spec_falls_back_to_hash() -> None:
    """A spec whose literal form exceeds 80 chars hashes to ``h<12-char-sha-prefix>``."""
    # Many disjoint pages → forces underscore joiners with no range collapse.
    # 30 pages spread to defeat coalescing: 1,4,7,10,...,88.
    pages = list(range(1, 90, 3))  # length 30
    result = normalize_page_spec(pages)
    assert result.is_full is False
    assert result.filename_infix.startswith("h")
    assert len(result.filename_infix) == 13  # 'h' + 12 hex chars
    # Hash is reproducible from the canonical-encoded normalized list.
    expected_digest = hashlib.sha256(
        json.dumps(pages, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()
    assert result.filename_infix == f"h{expected_digest[:12]}"
    # Frontmatter still carries the full list so the selection is recoverable.
    assert result.pages_for_frontmatter == pages


def test_short_spec_does_not_hash() -> None:
    """Literal form ≤ 80 chars stays literal even when many pages are involved."""
    pages = list(range(1, 31))  # 1..30, contiguous → "1-30" (4 chars)
    result = normalize_page_spec(pages)
    assert result.filename_infix == "1-30"


@pytest.mark.parametrize(
    "bad_input",
    [
        "2-4",  # only "all" allowed for str
        "",  # empty string
        "everything",
    ],
)
def test_string_other_than_all_raises(bad_input: str) -> None:
    with pytest.raises(ValueError, match="pages string"):
        normalize_page_spec(bad_input)


def test_empty_list_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        normalize_page_spec([])


@pytest.mark.parametrize(
    "bad_entry",
    [0, -1, -100],
)
def test_non_positive_pages_raise(bad_entry: int) -> None:
    with pytest.raises(ValueError, match="positive 1-based"):
        normalize_page_spec([1, 2, bad_entry])


@pytest.mark.parametrize(
    "bad_entry",
    [1.5, "two", None, [3]],
)
def test_non_int_entries_raise(bad_entry: Any) -> None:
    with pytest.raises(ValueError, match="positive 1-based"):
        normalize_page_spec([1, bad_entry, 3])


def test_bool_entries_rejected_explicitly() -> None:
    """``bool`` is a subclass of ``int``; reject anyway so ``[True, False]``
    cannot smuggle ``1, 0`` into a page selection."""
    with pytest.raises(ValueError, match="positive 1-based"):
        normalize_page_spec([True, False])  # type: ignore[list-item]


@pytest.mark.parametrize(
    "bad_input",
    [
        42,
        3.14,
        {1, 2, 3},
        {"pages": [1, 2]},
        (1, 2, 3),  # tuple is not list
    ],
)
def test_unrecognized_type_raises(bad_input: Any) -> None:
    with pytest.raises(ValueError, match="None, 'all', or list"):
        normalize_page_spec(bad_input)


def test_pagespec_is_frozen() -> None:
    """``PageSpec`` is a frozen dataclass; mutation raises."""
    spec = normalize_page_spec([2, 3, 4])
    with pytest.raises((AttributeError, TypeError)):
        spec.filename_infix = "9"  # type: ignore[misc]


# ─── compute_args_hash ───────────────────────────────────────────────────────


_DEFAULTS: dict[str, Any] = {
    "model": "openai/gpt-5.4",
    "dpi": 200,
    "prompt_hash": "a" * 64,
    "extraction_type": "ocr_transcription",
}


def test_all_defaults_produces_no_hash() -> None:
    """When every arg matches defaults, the helper returns the no-suffix marker."""
    result = compute_args_hash(dict(_DEFAULTS), _DEFAULTS)
    assert result == ArgsHash(full_hash=None, prefix=None, normalized_args={})


def test_empty_args_produces_no_hash() -> None:
    """An empty args dict is canonical-default by definition."""
    result = compute_args_hash({}, _DEFAULTS)
    assert result.full_hash is None
    assert result.prefix is None
    assert result.normalized_args == {}


def test_single_non_default_produces_hash() -> None:
    """One non-default arg → full hex hash + 6-char prefix in filename infix."""
    args = dict(_DEFAULTS, dpi=300)
    result = compute_args_hash(args, _DEFAULTS)
    assert result.full_hash is not None
    assert len(result.full_hash) == 64
    assert all(c in "0123456789abcdef" for c in result.full_hash)
    assert result.prefix == result.full_hash[:6]
    assert result.normalized_args == {"dpi": 300}


def test_hash_value_is_deterministic_and_canonical() -> None:
    """Hash matches sha256 of ``json.dumps(sort_keys=True, separators=(",", ":"))``."""
    args = dict(_DEFAULTS, dpi=300, model="openai/gpt-5.5")
    result = compute_args_hash(args, _DEFAULTS)
    expected_canonical = json.dumps(
        {"dpi": 300, "model": "openai/gpt-5.5"},
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_hash = hashlib.sha256(
        expected_canonical.encode("utf-8"),
    ).hexdigest()
    assert result.full_hash == expected_hash
    assert result.prefix == expected_hash[:6]


def test_excluded_keys_do_not_affect_hash() -> None:
    """Per-call keys (source_path, extracted_at, etc.) are stripped before hashing."""
    args = dict(
        _DEFAULTS,
        dpi=300,
        source_path="dropbox/foo.pdf",
        destination_path="evidence/.../foo.pdf",
        extracted_at="2026-05-23T20:00:00Z",
        source_sha256="b" * 64,
        pages=[1, 2, 3],
    )
    result = compute_args_hash(args, _DEFAULTS)
    # Only ``dpi`` should remain post-strip; same as the single-non-default test.
    same = compute_args_hash(dict(_DEFAULTS, dpi=300), _DEFAULTS)
    assert result.full_hash == same.full_hash
    assert result.normalized_args == {"dpi": 300}


def test_keys_absent_from_defaults_are_kept() -> None:
    """Keys not in ``default_profile`` have no default to compare against, so they
    participate in the hash."""
    args = dict(_DEFAULTS, custom_knob="experimental")
    result = compute_args_hash(args, _DEFAULTS)
    assert result.normalized_args == {"custom_knob": "experimental"}
    assert result.full_hash is not None


def test_insertion_order_does_not_affect_hash() -> None:
    """sort_keys canonicalization → ``{b:1, a:2}`` hashes the same as ``{a:2, b:1}``."""
    first = compute_args_hash(
        {"dpi": 300, "model": "openai/gpt-5.5"},
        _DEFAULTS,
    )
    second = compute_args_hash(
        {"model": "openai/gpt-5.5", "dpi": 300},
        _DEFAULTS,
    )
    assert first.full_hash == second.full_hash


def test_different_values_produce_different_hashes() -> None:
    """Sanity: distinct non-default values yield distinct hashes."""
    a = compute_args_hash(dict(_DEFAULTS, dpi=300), _DEFAULTS)
    b = compute_args_hash(dict(_DEFAULTS, dpi=400), _DEFAULTS)
    assert a.full_hash != b.full_hash
    assert a.prefix != b.prefix


def test_prompt_hash_field_compares_by_key_identity() -> None:
    """The helper does no field-name translation; ``prompt_hash`` vs ``prompt``
    is the caller's responsibility to align."""
    args = dict(_DEFAULTS, prompt_hash="c" * 64)  # different prompt_hash
    result = compute_args_hash(args, _DEFAULTS)
    assert result.normalized_args == {"prompt_hash": "c" * 64}
    assert result.full_hash is not None


@pytest.mark.parametrize(
    "bad_args",
    [42, "string", [1, 2], None, (1, 2)],
)
def test_non_dict_args_raises(bad_args: Any) -> None:
    with pytest.raises(ValueError, match="args must be a dict"):
        compute_args_hash(bad_args, _DEFAULTS)


@pytest.mark.parametrize(
    "bad_default",
    [42, "string", [1, 2], None],
)
def test_non_dict_default_profile_raises(bad_default: Any) -> None:
    with pytest.raises(ValueError, match="default_profile must be a dict"):
        compute_args_hash({"dpi": 300}, bad_default)


def test_argshash_is_frozen() -> None:
    """``ArgsHash`` is a frozen dataclass; mutation raises."""
    result = compute_args_hash(dict(_DEFAULTS, dpi=300), _DEFAULTS)
    with pytest.raises((AttributeError, TypeError)):
        result.full_hash = "x" * 64  # type: ignore[misc]
