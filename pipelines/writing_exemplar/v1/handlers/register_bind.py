"""Resolve pipeline_options.register to rag-context prefix or whole-scope."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from systems.pipeline.core.dag import PipelineExecutionError

_TABLE_PATH = Path(__file__).resolve().parent.parent / "registers.yaml"
_table_cache: dict[str, Any] | None = None


def load_register_table() -> dict[str, Any]:
    global _table_cache  # noqa: PLW0603
    if _table_cache is not None:
        return _table_cache
    loaded = yaml.safe_load(_TABLE_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise PipelineExecutionError("writing_exemplar registers.yaml is not a mapping")
    _table_cache = loaded
    return loaded


def normalize_register(raw: Any) -> str:
    text = str(raw or "all").strip().lower()
    return text or "all"


def prefixes_for_register(raw: Any) -> list[str] | None:
    """Return rag_source_prefixes, or None to keep scope_override writing_exemplars."""
    table = load_register_table()
    name = normalize_register(raw)
    aliases = table.get("aliases") or {}
    if name in aliases:
        name = str(aliases[name]).strip().lower()
    registers = table.get("registers") or {}
    if name not in registers:
        known = ", ".join(sorted(registers))
        raise PipelineExecutionError(
            f"Unknown writing_exemplar register {name!r}. Known: {known}"
        )
    dirname = registers[name]
    if dirname is None:
        return None
    root = str(table.get("root") or "").rstrip("/")
    if not root:
        raise PipelineExecutionError("writing_exemplar registers.yaml missing root")
    return [f"{root}/{dirname}"]
