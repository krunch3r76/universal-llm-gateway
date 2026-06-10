"""
Collect docstring inventory and quality signals for docstring enhancement.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, override

from doc_extraction import extract_file_inventory, extract_subsystem_inventory
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

if TYPE_CHECKING:
    from systems.pipeline.core.execution.resolver import PipelineContextProtocol
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

MODULE_MIN_WORDS = 15
CLASS_MIN_WORDS = 15
FUNCTION_MIN_WORDS = 10
_STOPWORDS = {"the", "a", "an", "for", "of", "and", "to", "is", "this"}


def _repo_root() -> Path:
    """Return repository root regardless of Stargate process cwd."""
    return Path(__file__).resolve().parents[3]


def _word_count(docstring: str) -> int:
    return len(docstring.split())


def _is_name_echo(docstring: str, name: str) -> bool:
    """True if first sentence mostly restates the symbol name."""
    if not name:
        return False
    first_sentence = docstring.split(".")[0].split("\n")[0].strip().lower()
    words = first_sentence.split()
    if len(words) > 6:
        return False
    name_parts = set(
        w.lower() for w in re.split(r"[_\s]+|(?<=[a-z])(?=[A-Z])", name) if w
    )
    doc_words = set(words) - _STOPWORDS
    if not doc_words:
        return True
    overlap = doc_words & name_parts
    return len(overlap) >= len(doc_words) * 0.7


def _append_doc_issues(
    *,
    issues: list[dict[str, Any]],
    path: str,
    line: int,
    scope: Literal["module", "class", "function", "method"],
    name: str,
    docstring: str,
    threshold: int,
    check_length: bool,
) -> None:
    if not docstring:
        issues.append(
            {
                "path": path,
                "line": line,
                "scope": scope,
                "name": name,
                "issue": "empty",
                "severity": "critical",
                "words": 0,
                "threshold": threshold,
                "excerpt": "",
            }
        )
        return

    words = _word_count(docstring)
    if check_length and words < threshold:
        issues.append(
            {
                "path": path,
                "line": line,
                "scope": scope,
                "name": name,
                "issue": "too_short",
                "severity": "warning",
                "words": words,
                "threshold": threshold,
                "excerpt": docstring[:80] + ("..." if len(docstring) > 80 else ""),
            }
        )
    if _is_name_echo(docstring, name):
        issues.append(
            {
                "path": path,
                "line": line,
                "scope": scope,
                "name": name,
                "issue": "name_echo",
                "severity": "warning",
                "words": words,
                "threshold": threshold,
                "excerpt": docstring[:80],
            }
        )


def _build_quality_issues(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for module in inventory.get("modules", []):
        path = str(module.get("path", ""))
        if not path:
            continue
        module_name = Path(path).stem
        check_length = module_name != "__init__"
        _append_doc_issues(
            issues=issues,
            path=path,
            line=1,
            scope="module",
            name=module_name,
            docstring=str(module.get("docstring", "")).strip(),
            threshold=MODULE_MIN_WORDS,
            check_length=check_length,
        )

    for cls in inventory.get("classes", []):
        class_name = str(cls.get("name", ""))
        if not class_name or class_name.startswith("_"):
            continue
        path = str(cls.get("path", ""))
        line = int(cls.get("line", 1))
        _append_doc_issues(
            issues=issues,
            path=path,
            line=line,
            scope="class",
            name=class_name,
            docstring=str(cls.get("docstring", "")).strip(),
            threshold=CLASS_MIN_WORDS,
            check_length=True,
        )
        for method in cls.get("methods", []):
            method_name = str(method.get("name", ""))
            if not method_name or method_name.startswith("_"):
                continue
            _append_doc_issues(
                issues=issues,
                path=path,
                line=int(method.get("line", line)),
                scope="method",
                name=f"{class_name}.{method_name}",
                docstring=str(method.get("docstring", "")).strip(),
                threshold=FUNCTION_MIN_WORDS,
                check_length=True,
            )

    for fn in inventory.get("functions", []):
        function_name = str(fn.get("name", ""))
        if not function_name or function_name.startswith("_"):
            continue
        _append_doc_issues(
            issues=issues,
            path=str(fn.get("path", "")),
            line=int(fn.get("line", 1)),
            scope="function",
            name=function_name,
            docstring=str(fn.get("docstring", "")).strip(),
            threshold=FUNCTION_MIN_WORDS,
            check_length=True,
        )

    return issues


def _error_output(step_id: str, message: str) -> StepOutput:
    return StepOutput(
        raw=json.dumps({"error": message}),
        json={"error": message},
        step_id=step_id,
        error=message,
    )


class CollectInventoryHandler(BaseHandler):
    """Collect code inventory and docstring quality gaps for target path."""

    step_type: str = "docstring_enhance_collect_inventory"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        context_protocol = cast(
            "PipelineContextProtocol",
            cast(object, context),
        )
        resolver = NamespaceResolver(context_protocol)
        inputs = step.handler_inputs or {}
        target_path_value = self._resolve_input(resolver, step, "target_path", inputs)

        if not isinstance(target_path_value, str):
            return _error_output(
                step.id,
                "target_path must be a string path",
            )
        target_path_raw = target_path_value.strip()
        if not target_path_raw:
            return _error_output(step.id, "target_path is empty")

        workspace_root = _repo_root()
        target = Path(target_path_raw)
        if not target.is_absolute():
            target = workspace_root / target
        target = target.resolve()

        try:
            _ = target.relative_to(workspace_root)
        except ValueError:
            return _error_output(
                step.id,
                f"target_path outside repository root: {target_path_raw}",
            )

        if not target.exists():
            return _error_output(step.id, f"target_path not found: {target_path_raw}")

        inventory: dict[str, Any]
        if target.is_dir():
            inventory = extract_subsystem_inventory(
                target, workspace_root, include_bodies=True
            )
            inventory["target_kind"] = "directory"
        else:
            if target.suffix != ".py":
                return _error_output(
                    step.id, "target_path must be a .py file or directory"
                )
            file_inv = extract_file_inventory(
                target, workspace_root, include_bodies=True
            )
            inventory = {
                "subsystem_path": target.as_posix(),
                "subsystem_name": target.stem,
                "architecture_doc_path": "",
                "file_count": 1,
                "modules": [
                    {
                        "path": file_inv["path"],
                        "docstring": file_inv["module_docstring"],
                    }
                ],
                "classes": [
                    {"path": file_inv["path"], **cls} for cls in file_inv["classes"]
                ],
                "functions": [
                    {"path": file_inv["path"], **fn} for fn in file_inv["functions"]
                ],
                "imports": [
                    {"path": file_inv["path"], "import": stmt}
                    for stmt in file_inv["imports"]
                ],
                "existing_doc": "",
                "target_kind": "file",
            }

        quality_issues = _build_quality_issues(inventory)
        critical_count = sum(
            1 for issue in quality_issues if issue["severity"] == "critical"
        )
        warning_count = sum(
            1 for issue in quality_issues if issue["severity"] == "warning"
        )
        inventory["quality_issues"] = quality_issues
        inventory["quality_summary"] = {
            "critical": critical_count,
            "warning": warning_count,
            "total": len(quality_issues),
        }

        return StepOutput(
            raw=json.dumps(inventory, indent=2),
            json=inventory,
            step_id=step.id,
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        inputs = step.handler_inputs or {}
        if "target_path" not in inputs:
            errors.append(
                f"Step '{step.id}': docstring_enhance_collect_inventory requires 'target_path' in handler_inputs"
            )
        return errors
