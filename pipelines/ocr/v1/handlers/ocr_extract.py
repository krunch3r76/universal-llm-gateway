"""OCR extract handler — directory listing + per-file prose extraction via ocr_core.

``mode: list`` enumerates scannable files (``files`` option or ``directory``).
Default mode maps over ``list_files.json.files`` and calls ``ocr_pages`` per
iteration — no local resize/token-budget/provider logic.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, override

from ocr_core import (
    DEFAULT_OCR_PROMPT,
    OCR_MODEL,
    SCANNABLE_SUFFIXES,
    ocr_pages,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_STARGATE_URL
from universal_event_bus import Event, event_factory
from universal_logging import get_logger

logger = get_logger(__name__)


@event_factory
def ocr_extract_failed(
    *,
    execution_id: str,
    step_id: str,
    path: str,
    error: str,
) -> Event:
    return Event(
        signal="ocr.extract.failed",
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "path": path,
            "error": error,
        },
    )


def _files_root(opts: dict[str, Any]) -> Path:
    raw = opts.get("files_root") or os.environ.get("CORTEX_FILES_ROOT", "")
    if raw:
        return Path(str(raw)).expanduser().resolve()
    return Path.home().joinpath(".cortex", "files").resolve()


def _list_scannable_files(opts: dict[str, Any], *, files_root: Path) -> list[str]:
    """Resolve ``files`` or enumerate ``directory`` (one level, ocr_directory semantics)."""
    files = opts.get("files")
    if isinstance(files, list) and files:
        return [str(p) for p in files if str(p).strip()]

    directory = opts.get("directory")
    if not directory or not str(directory).strip():
        raise ValueError("missing pipeline_options: files (list) or directory (str)")

    abs_dir = (files_root / str(directory)).resolve()
    try:
        abs_dir.relative_to(files_root)
    except ValueError as exc:
        raise ValueError(f"directory {directory!r} resolves outside files_root") from exc

    if not abs_dir.is_dir():
        raise FileNotFoundError(f"Not a directory: {directory}")

    rel_paths = sorted(
        str(p.relative_to(files_root))
        for p in abs_dir.iterdir()
        if p.suffix.lower() in SCANNABLE_SUFFIXES
    )
    if not rel_paths:
        raise FileNotFoundError(f"No scannable files found in {directory}")
    return rel_paths


def _publish_event(context: Any, event: Event) -> None:
    proxy = getattr(context, "_proxy", None)
    event_bus = getattr(proxy, "event_bus", None) if proxy else None
    if event_bus is None:
        return
    _ = asyncio.create_task(event_bus.publish_nowait(event))


class OcrExtractV1Handler(BaseHandler):
    step_type = "ocr_extract_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        mode = step.get_domain_field("mode")
        if mode == "list":
            return await self._execute_list(step, context)
        return await self._execute_extract(step, context)

    async def _execute_list(self, step: Any, context: Any) -> StepOutput:
        opts = getattr(context, "options", {}) or {}
        files_root = _files_root(opts)
        try:
            files = _list_scannable_files(opts, files_root=files_root)
        except (FileNotFoundError, ValueError) as exc:
            err = str(exc)
            payload = {"ok": False, "files": [], "error": err}
            return StepOutput(raw=json.dumps(payload), json=payload, error=err)

        payload = {
            "ok": True,
            "files": files,
            "files_root": str(files_root),
            "count": len(files),
        }
        logger.info("ocr list_files: %d file(s) under %s", len(files), files_root)
        return StepOutput(raw=json.dumps(payload), json=payload)

    async def _execute_extract(self, step: Any, context: Any) -> StepOutput:
        resolved_map = getattr(step, "resolved_map_inputs", None) or {}
        rel_path = resolved_map.get("path")
        opts = getattr(context, "options", {}) or {}
        files_root = _files_root(opts)
        execution_id = str(getattr(context, "execution_id", "") or "")
        step_id = str(getattr(step, "id", None) or getattr(step, "name", "extract"))

        if not isinstance(rel_path, str) or not rel_path.strip():
            err = "missing map input: path"
            payload = {"ok": False, "error": err}
            return StepOutput(raw=json.dumps(payload), json=payload, error=err)

        rel_path = rel_path.strip()
        abs_path = (files_root / rel_path).resolve()
        try:
            abs_path.relative_to(files_root)
        except ValueError:
            err = f"path {rel_path!r} resolves outside files_root"
            self._emit_failure(context, execution_id, step_id, rel_path, err)
            payload = {"ok": False, "path": rel_path, "error": err}
            return StepOutput(raw=json.dumps(payload), json=payload, error=err)

        if not abs_path.exists():
            err = f"file not found: {rel_path}"
            self._emit_failure(context, execution_id, step_id, rel_path, err)
            payload = {"ok": False, "path": rel_path, "error": err}
            return StepOutput(raw=json.dumps(payload), json=payload, error=err)

        if abs_path.suffix.lower() not in SCANNABLE_SUFFIXES:
            err = f"unsupported suffix: {abs_path.suffix}"
            self._emit_failure(context, execution_id, step_id, rel_path, err)
            payload = {"ok": False, "path": rel_path, "error": err}
            return StepOutput(raw=json.dumps(payload), json=payload, error=err)

        model = self._resolve_ocr_model(step, context, opts)
        prompt = self._resolve_ocr_prompt(step, context, opts)
        dpi = int(opts.get("dpi", 200))
        token_budget = opts.get("token_budget")
        if token_budget is not None:
            token_budget = int(token_budget)

        ocr_kwargs: dict[str, Any] = {
            "stargate_url": DEFAULT_STARGATE_URL,
            "prompt": prompt,
            "dpi": dpi,
            "model": model,
        }
        if token_budget is not None:
            ocr_kwargs["token_budget"] = token_budget

        try:
            result = await asyncio.to_thread(ocr_pages, abs_path, **ocr_kwargs)
        except Exception as exc:
            err = str(exc)
            self._emit_failure(context, execution_id, step_id, rel_path, err)
            payload = {"ok": False, "path": rel_path, "error": err}
            return StepOutput(raw=json.dumps(payload), json=payload, error=err)

        text = str(result.get("text") or "")
        if text.startswith("[OCR error:"):
            self._emit_failure(context, execution_id, step_id, rel_path, text)

        result["path"] = rel_path
        result["ok"] = True
        return StepOutput(raw=json.dumps(result, default=str), json=result)

    def _resolve_ocr_model(self, step: Any, context: Any, opts: dict[str, Any]) -> str:
        override = opts.get("model")
        if isinstance(override, str) and override.strip():
            return override.strip()
        if step.model_ref:
            return self._resolve_model_alias(step.model_ref, context)
        return OCR_MODEL

    def _resolve_ocr_prompt(self, step: Any, context: Any, opts: dict[str, Any]) -> str:
        override = opts.get("prompt")
        if isinstance(override, str) and override.strip():
            return override.strip()
        prompt_ref = getattr(step, "prompt_ref", None)
        if prompt_ref:
            rendered = self._render_prompt(prompt_ref, {}, context)
            if rendered.user_prompt.strip():
                return rendered.user_prompt.strip()
        return DEFAULT_OCR_PROMPT

    def _emit_failure(
        self,
        context: Any,
        execution_id: str,
        step_id: str,
        path: str,
        error: str,
    ) -> None:
        logger.warning("ocr extract failed path=%s error=%s", path, error)
        _publish_event(
            context,
            ocr_extract_failed(
                execution_id=execution_id,
                step_id=step_id,
                path=path,
                error=error,
            ),
        )
