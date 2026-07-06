"""Resolve claude.ai bundle ``description`` frontmatter from SOT + entity rows."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

MIN_BUNDLE_DESCRIPTION_LEN = 50
MAX_CLAUDE_AI_DESCRIPTION_LEN = 200

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_TRIGGER_LINE_RE = re.compile(r"^\*\*Trigger(?::\*\*|\*\*:)\s*(.+)$", re.MULTILINE)
_SOT_LINE_RE = re.compile(r"^\*\*SOT")
_SOURCE_LINE_RE = re.compile(r"^\*\*Source:\*\*")
_BROKEN_DESCRIPTIONS = frozenset({">-", "|", ""})
_XML_TAG_RE = re.compile(r"<[^>]+>")


class FrontmatterParseError(ValueError):
    """Malformed YAML frontmatter block."""

    def __init__(self, message: str, *, token_class: str = "yaml_syntax") -> None:
        super().__init__(message)
        self.token_class = token_class


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return YAML frontmatter dict and body after the closing ``---``."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    block = match.group(1)
    try:
        parsed = yaml.safe_load(block) or {}
    except yaml.YAMLError as exc:
        raise FrontmatterParseError(str(exc), token_class="yaml_syntax") from exc
    if not isinstance(parsed, dict):
        parsed = {}
    body = text[match.end() :].lstrip("\n")
    return parsed, body


def _description_scalar_raw(block: str) -> str | None:
    for line in block.splitlines():
        if not line.strip().startswith("description:"):
            continue
        return line.split("description:", 1)[1].strip()
    return None


def lint_frontmatter_description(slug: str, text: str) -> str | None:
    """Fail-loud when SOT ``description:`` is unsafe for YAML or claude.ai."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    block = match.group(1)
    raw_value = _description_scalar_raw(block)
    if raw_value and raw_value[0] not in "\"'|>{[":
        if ": " in raw_value:
            return f"FRONTMATTER: {slug} token_class=unquoted_colon_space"
        if '"' in raw_value or "'" in raw_value:
            return f"FRONTMATTER: {slug} token_class=embedded_quotes"
        if description_has_xml_tags(raw_value):
            return f"FRONTMATTER: {slug} token_class=angle_brackets"
    try:
        parse_frontmatter(text)
    except FrontmatterParseError as exc:
        return f"FRONTMATTER: {slug} token_class={exc.token_class}"
    return None


def first_sentence(text: str | None) -> str:
    if not text:
        return ""
    return text.split(". ", 1)[0].rstrip(".").strip()


def parse_trigger_line(body: str) -> str:
    match = _TRIGGER_LINE_RE.search(body)
    if not match:
        return ""
    return match.group(1).strip()


def _prose_excerpts(body: str) -> list[str]:
    excerpts: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if stripped.startswith(("<!--", "```", "|", "- ", "* ")):
            continue
        if _SOT_LINE_RE.match(stripped) or _SOURCE_LINE_RE.match(stripped):
            continue
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) < 80:
            continue
        if stripped not in excerpts:
            excerpts.append(stripped)
    return sorted(excerpts, key=len, reverse=True)


def description_has_xml_tags(description: str) -> bool:
    return bool(_XML_TAG_RE.search(description))


def is_trigger_grade(description: str) -> bool:
    desc = description.strip()
    if not desc or desc in _BROKEN_DESCRIPTIONS:
        return False
    if len(desc) < MIN_BUNDLE_DESCRIPTION_LEN:
        return False
    if desc.upper() == "RETIRED":
        return False
    if description_has_xml_tags(desc):
        return False
    return True


def resolve_bundle_description(
    slug: str,
    *,
    frontmatter: dict[str, Any],
    body: str,
    entity_description: str | None = None,
) -> str:
    """Pick the best soft-trigger description for a claude.ai bundle."""
    fm_desc = str(frontmatter.get("description") or "").strip()
    trigger = parse_trigger_line(body)
    entity = (entity_description or "").strip()
    prose_excerpts = _prose_excerpts(body)

    candidates: list[str] = []
    for value in (fm_desc, trigger, entity, *prose_excerpts, slug.replace("-", " ")):
        if (
            value
            and value not in _BROKEN_DESCRIPTIONS
            and value not in candidates
            and not description_has_xml_tags(value)
        ):
            candidates.append(value)

    for candidate in candidates:
        if is_trigger_grade(candidate) and len(candidate) <= MAX_CLAUDE_AI_DESCRIPTION_LEN:
            return candidate

    for candidate in candidates:
        if is_trigger_grade(candidate):
            return candidate

    valid = [c for c in candidates if c and c not in _BROKEN_DESCRIPTIONS]
    return max(valid, key=len) if valid else slug


def extract_rendered_description(rendered: str) -> str:
    """Read ``description`` from rendered bundle frontmatter."""
    fm, _ = parse_frontmatter(rendered)
    return str(fm.get("description") or "").strip()


def _yaml_scalar(value: str) -> str:
    if not value:
        return '""'
    if re.fullmatch(r"[^\n\"'#,:{}\\[\\]&*!?|>@%]+", value):
        return value
    return json.dumps(value)


def fit_claude_ai_description(
    description: str,
    *,
    max_len: int = MAX_CLAUDE_AI_DESCRIPTION_LEN,
) -> str:
    """Truncate to claude.ai Customize upload limit (word boundary when possible)."""
    desc = description.strip()
    if len(desc) <= max_len:
        return desc
    budget = max_len - 1
    cut = desc[:budget].rstrip()
    if " " in cut:
        word_cut = cut.rsplit(" ", 1)[0].rstrip(".,;:")
        if len(word_cut) >= MIN_BUNDLE_DESCRIPTION_LEN:
            cut = word_cut
    if len(cut) < MIN_BUNDLE_DESCRIPTION_LEN:
        cut = desc[:budget]
    return f"{cut}…"


def adapt_skill_md_for_claude_ai(text: str) -> tuple[str, bool]:
    """Return SKILL.md with description capped for claude.ai upload."""
    fm, body = parse_frontmatter(text)
    name = str(fm.get("name") or "").strip()
    desc = str(fm.get("description") or "").strip()
    fitted = fit_claude_ai_description(desc)
    if fitted == desc:
        return text, False
    header = f"---\nname: {name}\ndescription: {_yaml_scalar(fitted)}\n---\n\n"
    return header + body, True
