"""Sidecar grammar — page-spec normalization and args-hash composition.

The sidecar grammar's write-time naming concerns:

- ``normalize_page_spec`` — sort, dedupe, coalesce, range-or-list formatter,
  long-spec hash fallback per the spec's naming grammar.
- ``compute_args_hash`` — strip excluded keys, strip default-equal entries,
  sort-keys JSON canonicalize, SHA-256, 6-char prefix for filename infix.

Schema-validation helpers (frontmatter shape, leading-frontmatter parse,
sidecar suffix) live in ``_sidecar_schema``. Profile load and prompt
hashing live in ``_extraction_profile``.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md, sections
"Sidecar naming, partial extractions, and variant artifacts" and "Args hash".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final

# ─── Naming grammar — page-spec normalization ────────────────────────────────


# Spec § "Sidecar naming, partial extractions, and variant artifacts >
# Page spec normalization": the assembled filename infix is hashed when its
# literal form would exceed 80 chars; the hash is the first 12 hex chars of
# the SHA-256 of the normalized integer list (sha12 per v2 plan packet).
_PAGE_SPEC_HASH_LENGTH: Final[int] = 12
_PAGE_SPEC_MAX_LITERAL_LEN: Final[int] = 80


@dataclass(frozen=True, slots=True)
class PageSpec:
    """Normalized page specification for sidecar naming and frontmatter.

    Attributes:
        filename_infix: The string that goes inside ``.pages-<X>.extracted.md``.
            Empty string when ``is_full`` — the caller MUST omit the
            ``.pages-`` segment entirely in that case. Examples: ``"2-4"``,
            ``"1_3_5"``, ``"2-4_7-9"``, ``"h91c2e88ab731"`` (long-spec hash
            fallback, ``h`` prefix per spec).
        is_full: ``True`` iff the spec covers every page of the source. When
            ``True``, the sidecar's frontmatter sets ``pages: "all"`` and
            ``partial: false``.
        pages_for_frontmatter: ``"all"`` when ``is_full``; otherwise the
            canonical sorted/deduplicated int list. This is the authoritative
            list stored in frontmatter even when the filename uses the hash
            fallback, so the literal page selection is always recoverable
            from the sidecar.
    """

    filename_infix: str
    is_full: bool
    pages_for_frontmatter: str | list[int]


def normalize_page_spec(
    pages: list[int] | str | None,
    total_pages: int | None = None,
) -> PageSpec:
    """Normalize a page selection per the spec's naming grammar.

    Implements the rules in spec § "Sidecar naming, partial extractions, and
    variant artifacts > Page spec normalization":

    1. ``None`` or the literal string ``"all"`` → full extraction.
    2. ``list[int]`` of 1-based page numbers, sorted ascending and
       deduplicated.
    3. Runs of three or more contiguous pages coalesce into ``start-end``
       ranges; isolated pages and 2-page runs render literally with ``_``
       joiners (per the spec's example table, ``[1,3,5] → 1_3_5``).
    4. Segments join with ``_``.
    5. When ``total_pages`` is known and the normalized list covers every
       page ``1..total_pages``, return the full-extraction marker (caller
       omits the ``.pages-`` infix).
    6. When the literal filename infix would exceed 80 chars, fall back to
       ``h<12-char-sha-prefix>``. The hash input is the JSON encoding of the
       normalized int list (compact separators, no whitespace) so the
       fallback is deterministic and reproducible from frontmatter.

    Args:
        pages: Page selection. ``None`` and ``"all"`` are equivalent to full
            extraction. Otherwise a non-empty ``list[int]`` of positive
            1-based page numbers.
        total_pages: Optional total page count of the source. When provided,
            a list that covers every page collapses to full-extraction.

    Returns:
        A ``PageSpec`` carrying the filename infix, the ``is_full`` flag,
        and the canonical list to write to frontmatter.

    Raises:
        ValueError: ``pages`` is an unrecognized type, an empty list, a list
            containing non-int or non-positive entries, or the literal
            string is not ``"all"``.
    """
    if pages is None or pages == "all":
        return PageSpec(
            filename_infix="",
            is_full=True,
            pages_for_frontmatter="all",
        )
    if isinstance(pages, str):
        raise ValueError(
            f"pages string must be 'all', got {pages!r}",
        )
    if not isinstance(pages, list):
        raise ValueError(
            f"pages must be None, 'all', or list[int], got "
            f"{type(pages).__name__}",
        )
    if not pages:
        raise ValueError("pages list must be non-empty (use None for 'all')")

    for entry in pages:
        # ``bool`` is a subclass of ``int``; reject it explicitly so that
        # ``pages=[True, False]`` cannot smuggle bogus page numbers in.
        if not isinstance(entry, int) or isinstance(entry, bool) or entry < 1:
            raise ValueError(
                f"pages entries must be positive 1-based ints, got "
                f"{entry!r}",
            )

    normalized: list[int] = sorted(set(pages))

    if total_pages is not None and normalized == list(range(1, total_pages + 1)):
        return PageSpec(
            filename_infix="",
            is_full=True,
            pages_for_frontmatter="all",
        )

    infix = _format_page_runs(normalized)

    if len(infix) > _PAGE_SPEC_MAX_LITERAL_LEN:
        canonical = json.dumps(normalized, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        infix = f"h{digest[:_PAGE_SPEC_HASH_LENGTH]}"

    return PageSpec(
        filename_infix=infix,
        is_full=False,
        pages_for_frontmatter=normalized,
    )


def _format_page_runs(sorted_pages: list[int]) -> str:
    """Coalesce a sorted unique list into a page-spec infix.

    Runs of three or more contiguous pages render as ``start-end``; pairs
    and singletons render literally joined by ``_``. The length-3 threshold
    matches the spec's example table — ``[2,3,4] → 2-4`` (run of 3 collapses)
    but ``[1,3,5] → 1_3_5`` (no run of 3, no collapse). Length-2 runs like
    ``[2,3]`` produce ``2_3`` rather than ``2-3``: identical char count, but
    the literal form is clearer at a glance.
    """
    segments: list[str] = []
    run_start = sorted_pages[0]
    prev = sorted_pages[0]
    for current in sorted_pages[1:]:
        if current == prev + 1:
            prev = current
            continue
        segments.append(_format_run(run_start, prev))
        run_start = current
        prev = current
    segments.append(_format_run(run_start, prev))
    return "_".join(segments)


def _format_run(start: int, end: int) -> str:
    """Format one run: singletons as ``n``, pairs as ``a_b``, ≥3 as ``a-b``."""
    if start == end:
        return str(start)
    if end - start == 1:
        return f"{start}_{end}"
    return f"{start}-{end}"


# ─── Naming grammar — args hash ──────────────────────────────────────────────


# Spec § "Args hash": the filename infix carries a 6-hex-char prefix of the
# full SHA-256; the full hash is stored in frontmatter as ``args_hash``.
_ARGS_HASH_PREFIX_LENGTH: Final[int] = 6


# Spec § "Args hash > Input": these keys are excluded from the hash input
# because they are either (a) per-call values that would prevent legitimate
# idempotency (source_path, destination_path, extracted_at, source_sha256)
# or (b) encoded separately in the filename via the ``.pages-`` infix
# (pages). Callers pre-normalize ``prompt`` into ``prompt_hash`` before
# passing the dict in; this helper does no field-name translation.
_ARGS_HASH_EXCLUDED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source_path",
        "destination_path",
        "extracted_at",
        "source_sha256",
        "pages",
    },
)


@dataclass(frozen=True, slots=True)
class ArgsHash:
    """Output of ``compute_args_hash``.

    Attributes:
        full_hash: 64-char hex SHA-256 of the normalized non-default args,
            or ``None`` when every arg either matches the pinned default
            profile's value or is in the excluded-keys set. ``None`` means
            "this extraction is the canonical default — no ``.args-`` infix
            in the filename, ``args_hash: null`` in frontmatter."
        prefix: First 6 hex chars of ``full_hash`` for the filename infix,
            or ``None`` when ``full_hash`` is ``None``. Goes into the
            filename as ``.args-<prefix>``.
        normalized_args: The dict that was actually hashed, post strip-defaults
            and strip-excluded. Empty dict when ``full_hash`` is ``None``.
            Useful for debugging "why did this get an args-hash" without
            re-running the helper.
    """

    full_hash: str | None
    prefix: str | None
    normalized_args: dict[str, Any]


def compute_args_hash(
    args: dict[str, Any],
    default_profile: dict[str, Any],
) -> ArgsHash:
    """Compute the args-hash filename infix and frontmatter values.

    Implements the rules in spec § "Args hash":

    1. Drop excluded keys (``source_path``, ``destination_path``,
       ``extracted_at``, ``source_sha256``, ``pages`` — pages is encoded in
       its own filename infix).
    2. Drop every key whose value equals the corresponding entry in
       ``default_profile``. Keys absent from ``default_profile`` are kept
       (there is no default to compare against).
    3. If the resulting dict is empty: return ``ArgsHash(None, None, {})``
       so the caller omits the ``.args-`` infix and writes
       ``args_hash: null`` to frontmatter.
    4. Otherwise: ``json.dumps(..., sort_keys=True, separators=(",", ":"))``
       to canonicalize, ``sha256`` the encoded bytes, and take the full
       digest plus a 6-char prefix.

    Caller responsibilities:

    - **Field-name parity.** If the caller wants ``prompt`` to participate
      in the hash, both ``args`` and ``default_profile`` should carry the
      same key (typically the spec's ``prompt_hash`` after the caller
      pre-hashes the prompt text). This helper compares by key identity and
      does no translation.
    - **Type-stable values.** ``json.dumps`` is deterministic for stable
      types (str, int, float, bool, None, list, dict); avoid passing
      values whose ``repr`` is the only useful identity (sets, custom
      objects without ``__json__``). For collections, the caller should
      canonicalize ordering before passing.

    Args:
        args: Extraction args dict, post any caller-side normalization. May
            include keys that are not in ``default_profile`` (they are
            kept) and keys that are in the excluded set (they are dropped).
        default_profile: The pinned default profile dict, in the same
            shape — keys present here represent the canonical defaults that
            strip-on-match.

    Returns:
        An ``ArgsHash`` carrying ``full_hash``, ``prefix``, and the dict
        that was hashed. When all args match defaults, all three fields
        collapse to ``None`` / ``None`` / ``{}``.

    Raises:
        ValueError: ``args`` or ``default_profile`` is not a ``dict``.
    """
    if not isinstance(args, dict):
        raise ValueError(
            f"args must be a dict, got {type(args).__name__}",
        )
    if not isinstance(default_profile, dict):
        raise ValueError(
            f"default_profile must be a dict, got "
            f"{type(default_profile).__name__}",
        )

    normalized: dict[str, Any] = {}
    for key, value in args.items():
        if key in _ARGS_HASH_EXCLUDED_KEYS:
            continue
        if key in default_profile and default_profile[key] == value:
            continue
        normalized[key] = value

    if not normalized:
        return ArgsHash(full_hash=None, prefix=None, normalized_args={})

    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    full = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ArgsHash(
        full_hash=full,
        prefix=full[:_ARGS_HASH_PREFIX_LENGTH],
        normalized_args=normalized,
    )
