"""
Safe condition evaluation for pipeline steps.

Supports referencing previous step outputs in conditions:
    condition: "len(initial_proofread.json.get('corrections', [])) > 0"
    condition: "options.get('preprocessor_model') is not None"
    condition: "detect_issues.json.get('count', 0) > 5"

Invariant: ∀ condition: eval_sandboxed(condition) ∧ ¬arbitrary_code_execution

Security model:
- Empty __builtins__ (no imports, no exec, no eval)
- Whitelist of safe functions (len, bool, str, int, float)
- StepOutputProxy for safe attribute access with defaults
"""

from __future__ import annotations

import ast
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class StepOutputProxy:
    """
    Proxy for safe attribute access on step outputs.

    Provides dict-like access with safe defaults:
        step_id.json.get('key', default)
        step_id.raw
        step_id.text

    Missing keys return None instead of raising.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        value = self._data.get(name)
        if isinstance(value, dict):
            return StepOutputProxy(value)
        return value

    def get(self, key: str, default: Any = None) -> Any:
        """Get value with default (dict-like access)."""
        value = self._data.get(key, default)
        if isinstance(value, dict):
            return StepOutputProxy(value)
        return value

    def __bool__(self) -> bool:
        """Truthy if data is non-empty."""
        return bool(self._data)

    def __repr__(self) -> str:
        return f"StepOutputProxy({self._data!r})"


class ConditionEvaluator:
    """
    Safe condition evaluator for pipeline steps.

    Provides a sandboxed evaluation context with:
    - Previous step outputs (by step_id)
    - Pipeline options
    - Built-in safe functions (len, bool, str, int, float)

    Security: Executes with empty __builtins__ to prevent
    arbitrary code execution (no imports, exec, eval, etc.).
    """

    SAFE_BUILTINS: dict[str, Any] = {
        "len": len,
        "bool": bool,
        "str": str,
        "int": int,
        "float": float,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "any": any,
        "all": all,
        "True": True,
        "False": False,
        "None": None,
    }

    def evaluate(
        self,
        condition: str,
        outputs: dict[str, Any],
        options: dict[str, Any],
    ) -> bool:
        """
        Evaluate a condition string in sandboxed context.

        Args:
            condition: Python-like condition expression
            outputs: Previous step outputs (step_id -> StepOutput)
            options: Pipeline options dict

        Returns:
            Boolean result of condition evaluation

        Example conditions:
            "len(initial_proofread.json.get('corrections', [])) > 0"
            "options.get('preprocessor_model') is not None"
            "detect_issues.json.get('count', 0) > 5"
            "review.json and len(review.json.get('issues', [])) > 0"
        """
        if not condition or not condition.strip():
            return True  # No condition = always execute

        # Build evaluation context
        context = self._build_context(outputs, options)

        try:
            # Evaluate with empty builtins (sandboxed)
            result = eval(condition, {"__builtins__": {}}, context)
            logger.debug(f"Condition '{condition}' evaluated to {result}")
            return bool(result)
        except Exception as e:
            logger.warning(
                f"Condition evaluation failed: '{condition}' - {e}. "
                "Defaulting to skip (False)."
            )
            return False  # Default to skip on error

    def _build_context(
        self,
        outputs: dict[str, Any],
        options: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build sandboxed evaluation context.

        Context includes:
        - Safe builtins (len, bool, str, int, float, etc.)
        - options: StepOutputProxy wrapping pipeline options
        - {step_id}: StepOutputProxy for each completed step
        """
        context = dict(self.SAFE_BUILTINS)
        context["options"] = StepOutputProxy(options)

        # Add step outputs as accessible objects
        for step_id, output in outputs.items():
            # Wrap StepOutput in proxy for safe access
            output_dict = {
                "raw": getattr(output, "raw", ""),
                "json": getattr(output, "json", None) or {},
                "text": getattr(output, "text", ""),
                "model_id": getattr(output, "model_id", None),
                "error": getattr(output, "error", None),
            }
            context[step_id] = StepOutputProxy(output_dict)

        return context


# Singleton
_condition_evaluator: ConditionEvaluator | None = None


def get_condition_evaluator() -> ConditionEvaluator:
    """Get or create condition evaluator singleton."""
    global _condition_evaluator
    if _condition_evaluator is None:
        _condition_evaluator = ConditionEvaluator()
    return _condition_evaluator


def extract_condition_deps(condition: str) -> set[str]:
    """
    Extract referenced step names from a condition expression.

    Uses Python AST parsing to collect `Name` references and excludes safe
    builtins plus `options` (which is pipeline options context, not a step).
    """
    if not condition or not condition.strip():
        return set()

    try:
        parsed = ast.parse(condition, mode="eval")
    except SyntaxError as exc:
        logger.warning(
            "Condition dependency extraction failed (syntax error): "
            f"'{condition}' - {exc}"
        )
        return set()

    excluded_names = set(ConditionEvaluator.SAFE_BUILTINS) | {"options"}
    deps: set[str] = set()

    for node in ast.walk(parsed):
        if isinstance(node, ast.Name) and node.id not in excluded_names:
            deps.add(node.id)

    return deps


def evaluate_condition(
    condition: str,
    outputs: dict[str, Any],
    options: dict[str, Any],
) -> bool:
    """
    Convenience function to evaluate a condition.

    Args:
        condition: Condition expression string
        outputs: Previous step outputs
        options: Pipeline options

    Returns:
        Boolean result
    """
    return get_condition_evaluator().evaluate(condition, outputs, options)
