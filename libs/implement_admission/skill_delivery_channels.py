"""Exactly-one-channel skill delivery validation (D7 / F5)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from implement_admission.skill_source_table import (
    TEMPLATE_VERSION,
    canonical_table_key,
    resolve_canonical_source_uri,
)

_AFFORDANCE_FRAMING = (
    "You are afforded the following skills for this task. Apply a skill when its "
    "trigger matches; these are task guidance, not identity claims or proof of "
    "unavailable tools."
)
_INLINE_SKILL_HEADER_RE = re.compile(
    r"^<!-- skill-inline:(?P<slug>[a-z0-9][-a-z0-9_]*)"
    r"(?:\s+source:(?P<source>\S+))?"
    r"(?:\s+rev:(?P<rev>\S+))?"
    r"\s+digest:(?P<digest>\S+)\s*-->",
    re.IGNORECASE | re.MULTILINE,
)
_INLINE_SKILL_BLOCK_RE = re.compile(
    r"<!-- skill-inline:(?P<slug>[a-z0-9][-a-z0-9_]*)"
    r"(?:\s+source:(?P<source>\S+))?"
    r"(?:\s+rev:(?P<rev>\S+))?"
    r"\s+digest:(?P<digest>\S+)\s*-->"
    r"\s*```(?:markdown)?\s*\n",
    re.IGNORECASE | re.DOTALL,
)


def _outer_invariants_close_index(text: str) -> int | None:
    """Return the outer </invariants> index — not examples inside inlined bodies."""
    open_idx = text.find("<invariants>")
    if open_idx < 0:
        return None
    close_idx = text.find("</invariants>", open_idx + len("<invariants>"))
    if close_idx < 0:
        return None
    return close_idx


def _inline_block_close_is_structural(text: str, close_idx: int) -> bool:
    after = text[close_idx + 4 :].lstrip("\n")
    if not after:
        return True
    structural_prefixes = (
        "<!-- skill-inline:",
        "</invariants>",
        "- Use the `",
        "- Skill names for orientation",
        "<task_guidance>",
        "<corpus>",
        "<mcp_capabilities>",
        "<output_format>",
        "<scope>",
    )
    return after.startswith(structural_prefixes)


def _inline_block_close_index(text: str, payload_start: int) -> int:
    """Return index of the closing fence line for one inline skill block."""
    cursor = payload_start
    while True:
        idx = text.find("\n```", cursor)
        if idx < 0:
            raise ValueError("inline skill block missing closing fence")
        if _inline_block_close_is_structural(text, idx):
            return idx + 4
        cursor = idx + 4
_USE_SKILL_RE = re.compile(
    r"Use the `(?P<slug>[a-z0-9][-a-z0-9_]*)` skill",
    re.IGNORECASE,
)
_FS_SKILL_POINTER_RE = re.compile(
    r"fs\(.*agent-skills/(?P<slug>[a-z0-9][-a-z0-9_]*)\.md",
    re.IGNORECASE,
)

# Allowlist = workspace-only skills web handoffs require inlined.
# Web-native skills (e.g. lead-seat-boot) stay pointer/self-fetch.
_WEB_ANTHROPIC_INLINE_SLUGS = frozenset(
    {
        "architecture-invariants",
        "ulg-architecture",
        "consult-routing",
        "dispatch-workflow",
        "handoff-packet-authoring",
        "implement-work-item",
        "implement-todo",
        "modularize-discipline",
        "mcp-surface-change",
        "build-pipeline",
        "debug-with-events",
        "service-lifecycle",
    }
)


@dataclass(frozen=True, slots=True)
class SkillChannelViolation:
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class SkillInlineResolveError(Exception):
    code: str
    reason: str

    def __str__(self) -> str:
        return f"{self.code}: {self.reason}"


@dataclass(frozen=True, slots=True)
class InlineSkillBlock:
    slug: str
    source_uri: str | None
    rev: str | None
    digest: str
    fenced_payload: str
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class InlineBodyResolution:
    slug: str
    body: str
    digest: str
    source_uri: str
    rev: str
    byte_len: int


@dataclass(frozen=True, slots=True)
class SkillInlineBudgetExceeded(Exception):  # noqa: N818
    total_bytes: int
    budget_bytes: int
    per_slug_bytes: dict[str, int]

    @property
    def reason(self) -> str:
        per = ", ".join(
            f"{slug}={size}" for slug, size in sorted(self.per_slug_bytes.items())
        )
        return (
            f"inline skill body sum {self.total_bytes} bytes exceeds budget "
            f"{self.budget_bytes}; per-slug: {per}. Trim collected slugs or "
            "raise skill_delivery.handoff_inline_budget_bytes."
        )


def _sha256_full(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def fenced_payload_for_body(body: str) -> str:
    return f"{_AFFORDANCE_FRAMING}\n\n{body}"


def _resolve_inline_block(
    text: str, match: re.Match[str]
) -> InlineSkillBlock:
    slug = canonical_table_key(match.group("slug"))
    declared = match.group("digest").strip()
    tail = text[match.end() :]
    fence_open = re.match(r"\s*```(?:markdown)?\s*\n", tail)
    if not fence_open:
        raise ValueError(f"inline skill {slug!r} missing fenced payload")
    payload_start = match.end() + fence_open.end()
    cursor = payload_start
    while cursor < len(text):
        idx = text.find("\n```", cursor)
        if idx < 0:
            raise ValueError(f"inline skill {slug!r} missing closing fence")
        if not _inline_block_close_is_structural(text, idx):
            cursor = idx + 4
            continue
        payload = text[payload_start:idx]
        if _sha256_full(payload) == declared:
            return InlineSkillBlock(
                slug=slug,
                source_uri=match.group("source"),
                rev=match.group("rev"),
                digest=declared,
                fenced_payload=payload,
                span=(match.start(), idx + 4),
            )
        cursor = idx + 4
    raise ValueError(f"inline skill {slug!r} fenced payload digest mismatch")


def _next_structural_inline_header(
    text: str, start: int
) -> re.Match[str] | None:
    for match in _INLINE_SKILL_HEADER_RE.finditer(text, start):
        tail = text[match.end() :]
        if re.match(r"\s*```(?:markdown)?\s*\n", tail):
            return match
    return None


def inline_block_span_lenient(text: str, match: re.Match[str]) -> tuple[int, int]:
    """Bound one inline block by structure only — for stale header digest rebuild."""
    tail = text[match.end() :]
    fence_open = re.match(r"\s*```(?:markdown)?\s*\n", tail)
    if not fence_open:
        raise ValueError("inline skill block missing fenced payload")
    payload_start = match.end() + fence_open.end()
    cursor = payload_start
    while cursor < len(text):
        idx = text.find("\n```", cursor)
        if idx < 0:
            raise ValueError("inline skill block missing closing fence")
        if _inline_block_close_is_structural(text, idx):
            return match.start(), idx + 4
        cursor = idx + 4
    raise ValueError("inline skill block missing closing fence")


def declared_inline_digests(text: str) -> dict[str, str]:
    """Declared header digests for every structural inline block."""
    digests: dict[str, str] = {}
    cursor = 0
    while True:
        match = _next_structural_inline_header(text, cursor)
        if match is None:
            break
        slug = canonical_table_key(match.group("slug"))
        digests[slug] = match.group("digest").strip()
        cursor = match.end() + 1
    return digests


def replace_inline_block_for_slug(text: str, slug: str, fresh_block: str) -> str:
    """Replace one inline block, including stale declared digests."""
    target = canonical_table_key(slug)
    cursor = 0
    while True:
        match = _next_structural_inline_header(text, cursor)
        if match is None:
            return text
        if canonical_table_key(match.group("slug")) != target:
            cursor = match.end() + 1
            continue
        try:
            span = _resolve_inline_block(text, match).span
        except ValueError:
            span = inline_block_span_lenient(text, match)
        return text[: span[0]] + fresh_block.strip() + text[span[1] :]
    return text


def parse_inline_skill_blocks(text: str) -> list[InlineSkillBlock]:
    """Bind each skill-inline header to exactly one fenced payload."""
    blocks: list[InlineSkillBlock] = []
    seen: set[str] = set()
    cursor = 0
    while True:
        match = _next_structural_inline_header(text, cursor)
        if match is None:
            break
        slug = canonical_table_key(match.group("slug"))
        try:
            block = _resolve_inline_block(text, match)
        except ValueError:
            cursor = match.end() + 1
            continue
        if slug in seen:
            raise ValueError(f"duplicate inline skill block for {slug!r}")
        seen.add(slug)
        blocks.append(block)
        cursor = block.span[1]
    return blocks


def _table_body_for_slug(slug: str) -> str | None:
    from cortex_store.routes.boot._skill_trigger import _resolve_skill_file

    try:
        source_uri = resolve_canonical_source_uri(slug)
    except Exception:
        return None
    path = _resolve_skill_file(source_uri, slug)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def text_without_inline_payload_regions(text: str) -> str:
    """Mask inline-block spans so pointer detection ignores inlined bodies."""
    blocks = sorted(parse_inline_skill_blocks(text), key=lambda b: b.span[0])
    if not blocks:
        return text
    out: list[str] = []
    cursor = 0
    for block in blocks:
        start, end = block.span
        out.append(text[cursor:start])
        out.append(" " * (end - start))
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def packet_skill_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    scan = text_without_inline_payload_regions(text)
    for match in _USE_SKILL_RE.finditer(scan):
        slugs.add(canonical_table_key(match.group("slug")))
    for match in _FS_SKILL_POINTER_RE.finditer(scan):
        slugs.add(canonical_table_key(match.group("slug")))
    for match in _INLINE_SKILL_HEADER_RE.finditer(text):
        slugs.add(canonical_table_key(match.group("slug")))
    return slugs


def validate_exactly_one_skill_channel(text: str) -> SkillChannelViolation | None:
    """Reject when the same slug is both inlined and pointer-delivered."""
    inline = {block.slug for block in parse_inline_skill_blocks(text)}
    scan = text_without_inline_payload_regions(text)
    pointer = {
        canonical_table_key(m.group("slug")) for m in _USE_SKILL_RE.finditer(scan)
    } | {
        canonical_table_key(m.group("slug"))
        for m in _FS_SKILL_POINTER_RE.finditer(scan)
    }
    overlap = sorted(inline & pointer)
    if overlap:
        return SkillChannelViolation(
            code="skill_dual_channel",
            reason=f"skill {overlap[0]!r} present on both inline and pointer channels",
        )
    return None


def validate_inline_skill_hashes(text: str) -> SkillChannelViolation | None:
    """Reject stale inline skill digests at dispatch time (rebuild, not warn)."""
    blocks: list[InlineSkillBlock] = []
    seen: set[str] = set()
    cursor = 0
    while True:
        match = _next_structural_inline_header(text, cursor)
        if match is None:
            break
        slug = canonical_table_key(match.group("slug"))
        try:
            block = _resolve_inline_block(text, match)
        except ValueError as exc:
            return SkillChannelViolation(code="skill_inline_malformed", reason=str(exc))
        if slug in seen:
            return SkillChannelViolation(
                code="skill_inline_malformed",
                reason=f"duplicate inline skill block for {slug!r}",
            )
        seen.add(slug)
        blocks.append(block)
        cursor = block.span[1]
    for block in blocks:
        payload_digest = _sha256_full(block.fenced_payload)
        if block.digest != payload_digest:
            return SkillChannelViolation(
                code="skill_hash_mismatch",
                reason=(
                    f"inline skill {block.slug!r} fenced payload digest mismatch "
                    "— rebuild packet"
                ),
            )
        resolved_body = _table_body_for_slug(block.slug)
        if resolved_body is None:
            return SkillChannelViolation(
                code="skill_unresolvable_on_seat",
                reason=f"inline skill {block.slug!r} unresolvable: body_missing",
            )
        expected_payload = fenced_payload_for_body(resolved_body)
        if block.fenced_payload != expected_payload:
            return SkillChannelViolation(
                code="skill_hash_mismatch",
                reason=f"inline skill {block.slug!r} hash != SOT — rebuild packet",
            )
    return None


def format_inline_skill_block(
    slug: str,
    *,
    source_uri: str,
    rev: str,
    body: str,
) -> str:
    payload = fenced_payload_for_body(body)
    digest = _sha256_full(payload)
    header = (
        f"<!-- skill-inline:{slug} source:{source_uri} rev:{rev} "
        f"digest:{digest} -->"
    )
    return f"\n{header}\n```markdown\n{payload}\n```"


def slugs_requiring_web_inline(slugs: set[str]) -> set[str]:
    return {s for s in slugs if s in _WEB_ANTHROPIC_INLINE_SLUGS}


def partition_web_skill_channels(
    collected_slugs: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    inline = tuple(sorted(slugs_requiring_web_inline(collected_slugs)))
    pointer = tuple(sorted(collected_slugs - set(inline)))
    return inline, pointer


def resolve_inline_bodies(
    inline_slugs: tuple[str, ...],
) -> list[InlineBodyResolution]:
    from cortex_store.routes.boot._skill_trigger import _resolve_skill_file

    resolved: list[InlineBodyResolution] = []
    for slug in inline_slugs:
        try:
            source_uri = resolve_canonical_source_uri(slug)
        except Exception as exc:
            raise SkillInlineResolveError(
                code="skill_unresolvable_on_seat",
                reason=f"inline skill {slug!r} unresolvable: {exc}",
            ) from exc
        path = _resolve_skill_file(source_uri, slug)
        if path is None:
            raise SkillInlineResolveError(
                code="skill_unresolvable_on_seat",
                reason=f"inline skill {slug!r} unresolvable: body_missing",
            )
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillInlineResolveError(
                code="skill_unresolvable_on_seat",
                reason=f"inline skill {slug!r} unresolvable: {exc}",
            ) from exc
        fenced = fenced_payload_for_body(body)
        resolved.append(
            InlineBodyResolution(
                slug=slug,
                body=body,
                digest=_sha256_full(fenced),
                source_uri=source_uri,
                rev=f"table:{TEMPLATE_VERSION}",
                byte_len=len(body.encode("utf-8")),
            )
        )
    return resolved


def enforce_inline_budget(
    resolved: list[InlineBodyResolution],
    budget_bytes: int,
) -> None:
    per_slug = {item.slug: item.byte_len for item in resolved}
    total = sum(per_slug.values())
    if total > budget_bytes:
        raise SkillInlineBudgetExceeded(
            total_bytes=total,
            budget_bytes=budget_bytes,
            per_slug_bytes=per_slug,
        )
