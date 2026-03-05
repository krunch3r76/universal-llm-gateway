# Architecture Document: handlers

<!-- GENERATED:START -->
## Scope
This document describes the architecture of the `handlers` subsystem, located at `/mnt/torus/projects/universal-llm-gateway/pipelines/doc_generate/handlers`. It focuses on the registration of doc-generate pipeline handlers and the deterministic extraction of docstrings, signatures, and imports from Python subsystem directories.
<!-- GENERATED:END -->

## Module inventory
<!-- GENERATED:START -->
* `pipelines/doc_generate/handlers/__init__.py`: doc-generate pipeline handler registration.
* `pipelines/doc_generate/handlers/events.py`: No docstring provided.
* `pipelines/doc_generate/handlers/extract_docstrings.py`: Deterministic extractor for doc-generate. Reads a Python subsystem directory, extracts docstrings/signatures/imports using tree-sitter-python, and attaches any existing architecture document content.
<!-- GENERATED:END -->

## Key classes
<!-- GENERATED:START -->
* `ExtractDocstringsHandler` (`pipelines/doc_generate/handlers/extract_docstrings.py`):
  Extract docstring inventory for a subsystem directory.
<!-- GENERATED:END -->

## Key functions
<!-- GENERATED:START -->
* `register_handlers(router: DomainRouter) -> None` (`pipelines/doc_generate/handlers/__init__.py`):
  Register doc-generate domain handlers.
* `_repo_root() -> Path` (`pipelines/doc_generate/handlers/extract_docstrings.py`):
  Return repository root regardless of Stargate process cwd.
* `_decode(node: _ts.Node, source: bytes) -> str` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_string_node_to_text(node: _ts.Node, source: bytes) -> str` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_extract_docstring_from_block(block_node: _ts.Node | None, source: bytes) -> str` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_signature(node: _ts.Node, source: bytes) -> str` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_extract_imports(module_node: _ts.Node, source: bytes) -> list[str]` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_extract_class_methods(class_node: _ts.Node, source: bytes) -> list[dict[str, Any]]` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_relative_path(path: Path, anchor: Path) -> str` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_extract_file_inventory(py_file: Path, workspace_root: Path) -> dict[str, Any]` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_error_output(step_id: str, message: str) -> StepOutput` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
* `_publish_event(context: PipelineContext, event: object) -> None` (`pipelines/doc_generate/handlers/extract_docstrings.py`): No docstring provided.
<!-- GENERATED:END -->

## Imports and dependencies
<!-- GENERATED:START -->
The `handlers` subsystem imports the following:

`pipelines/doc_generate/handlers/__init__.py`:
* `from typing import TYPE_CHECKING`
* `from .extract_docstrings import ExtractDocstringsHandler`

`pipelines/doc_generate/handlers/events.py`:
* `from universal_event_bus import Event, event_factory`

`pipelines/doc_generate/handlers/extract_docstrings.py`:
* `import ast`
* `import asyncio`
* `import json`
* `import time`
* `from pathlib import Path`
* `from typing import TYPE_CHECKING, Any, override`
* `import tree_sitter as _ts`
* `import tree_sitter_python as _tspython`
* `from systems.pipeline.core.execution.resolver import NamespaceResolver`
* `from systems.pipeline.core.handlers.builtin import BaseHandler`
* `from systems.pipeline.core.handlers.protocol import StepOutput`
* `from universal_logging import get_logger`
* `from .events import (
    DocGenerateArchitectureFound,
    DocGenerateArchitectureNotFound,
    DocGenerateExtractFailed,
    DocGenerateExtractSuccess,
    DocGeneratePythonEmpty,
)`
<!-- GENERATED:END -->

