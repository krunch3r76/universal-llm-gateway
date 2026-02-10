"""
Generic prompt template rendering.

No domain-specific logic - pure string substitution.
Domains build their own context dicts with domain-specific variables.

Supports:
- Simple placeholders: {text}, {name}
- Dot-notation placeholders: {config.style}, {options.tone}
- Type-safe: preserves JSON braces without escaping

Invariants:
- ∀ template, context: render(template, context) performs {key} substitution
- No language codes, regions, or translation concepts
"""

from __future__ import annotations

import re
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Pattern matches {word} or {word.nested.path}
_PLACEHOLDER_PATTERN = re.compile(r"\{([\w]+(?:\.[\w]+)*)\}")


class PromptBuilder:
    """
    Generic template substitution using regex-based replacement.

    DESIGN DECISION: Uses regex substitution, NOT str.format().

    Rationale: str.format() requires escaping all literal braces as {{ }},
    which is error-prone for prompt templates containing JSON examples,
    code blocks, or other brace-heavy content. Regex substitution only
    matches {word_chars} patterns, leaving literal braces untouched.

    Supports dot notation for nested access:
        builder = PromptBuilder()
        prompt = builder.render(
            "Use {config.style} style for {text}",
            {"text": "Hello", "config": {"style": "formal"}}
        )
        # Result: 'Use formal style for Hello'

    Example with JSON preservation:
        prompt = builder.render(
            'Translate {text}. Return: {"translation": "..."}',
            {"text": "Hello"}
        )
        # Result: 'Translate Hello. Return: {"translation": "..."}'
        # Note: JSON braces are preserved without escaping!
    """

    def render(self, template: str, context: dict[str, Any]) -> str:
        """
        Render template with context variables (strict mode).

        Uses regex-based substitution for brace-safety. Matches
        {placeholder} and {nested.placeholder} patterns.
        Literal braces (JSON, code) are preserved automatically.

        Args:
            template: Prompt template with {placeholders}
            context: Variables to substitute (supports nested dicts)

        Returns:
            Rendered prompt string

        Raises:
            ValueError: If required placeholder is missing from context
        """
        placeholders = self.get_placeholders(template)
        missing = []

        for ph in placeholders:
            if self._resolve_path(ph, context) is None:
                missing.append(ph)

        if missing:
            raise ValueError(f"Missing template variables: {sorted(missing)}")

        def replace(match: re.Match) -> str:
            path = match.group(1)
            value = self._resolve_path(path, context)
            return str(value)

        return _PLACEHOLDER_PATTERN.sub(replace, template)

    def render_safe(
        self,
        template: str,
        context: dict[str, Any],
        missing_value: str = "",
    ) -> str:
        """
        Render with missing variables replaced by default.

        Args:
            template: Prompt template with {placeholders}
            context: Variables to substitute
            missing_value: Value for missing variables

        Returns:
            Rendered prompt string (no errors on missing vars)
        """

        def replace(match: re.Match) -> str:
            path = match.group(1)
            value = self._resolve_path(path, context)
            if value is None:
                logger.debug(f"Template variable '{path}' not in context")
                return missing_value
            return str(value)

        return _PLACEHOLDER_PATTERN.sub(replace, template)

    def render_with_fallback(
        self,
        template: str,
        context: dict[str, Any],
    ) -> str:
        """
        Render, keeping unmatched placeholders as-is.

        Useful for partial rendering where some vars filled later.
        """

        def replace(match: re.Match) -> str:
            path = match.group(1)
            value = self._resolve_path(path, context)
            if value is not None:
                return str(value)
            return match.group(0)  # Keep original {key}

        return _PLACEHOLDER_PATTERN.sub(replace, template)

    def render_format_unsafe(
        self,
        template: str,
        context: dict[str, Any],
    ) -> str:
        """
        Render using str.format() - UNSAFE for templates with literal braces.

        WARNING: Only use this if you control the template and have escaped
        all literal braces as {{ and }}. Prefer render() for prompt templates.

        Args:
            template: Template with {placeholders} and escaped {{literal}}
            context: Variables to substitute

        Returns:
            Rendered string

        Raises:
            ValueError: If placeholder missing or brace syntax error
        """
        try:
            return template.format(**context)
        except KeyError as e:
            raise ValueError(f"Missing template variable: {e}") from e
        except ValueError as e:
            raise ValueError(f"Template brace syntax error: {e}") from e

    def get_placeholders(self, template: str) -> set[str]:
        """
        Extract placeholder names from template.

        Args:
            template: Prompt template

        Returns:
            Set of placeholder paths (e.g., {"text", "config.style"})
        """
        return set(_PLACEHOLDER_PATTERN.findall(template))

    def validate_context(
        self,
        template: str,
        context: dict[str, Any],
    ) -> list[str]:
        """
        Check if context has all required placeholders.

        Returns:
            List of missing placeholder paths (empty = valid)
        """
        required = self.get_placeholders(template)
        missing = []
        for ph in required:
            if self._resolve_path(ph, context) is None:
                missing.append(ph)
        return missing

    def _resolve_path(self, path: str, context: dict[str, Any]) -> Any | None:
        """
        Resolve a dotted path in context.

        Args:
            path: "key" or "key.nested.path"
            context: Context dictionary

        Returns:
            Resolved value or None if not found
        """
        parts = path.split(".")
        value: Any = context

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                return None

        return value


# Singleton instance
_prompt_builder: PromptBuilder | None = None


def get_prompt_builder() -> PromptBuilder:
    """Get or create prompt builder singleton."""
    global _prompt_builder
    if _prompt_builder is None:
        _prompt_builder = PromptBuilder()
    return _prompt_builder
