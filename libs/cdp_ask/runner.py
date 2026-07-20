"""Resolve prompts and run claude_bundles project-ask on a registry lane."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.project_ask import (
    ProjectAskResult,
    archive_harvest,
    run_project_ask,
)
from claude_bundles.project_ask_abort import abort_cleanup, deregister_on_exit
from claude_bundles.project_ask_conversation import run_project_conversation
from claude_bundles.project_ask_prompt_files import (
    project_root_base,
    resolve_prompt_path,
)
from cortex_store.files_path_normalize import normalize_cortex_files_path

from cdp_ask.models import SubmitProjectAskRequest


class ArchivePathError(ValueError):
    """Raised when explicit archive_path cannot resolve under CORTEX_FILES_ROOT."""

    def __init__(self, teaching: dict[str, Any]) -> None:
        self.teaching = teaching
        super().__init__(teaching.get("error") or "archive_path invalid")


def cortex_files_root() -> Path:
    raw = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "mcp-data" / "files").resolve()


def verify_harvest_root() -> Path:
    root = cortex_files_root()
    if not root.is_dir():
        raise RuntimeError(
            "CORTEX_FILES_ROOT harvest root missing or unreachable "
            f"({root}). Set CORTEX_FILES_ROOT to the live cortex files mount "
            "(doc shorthand /mcp-data/files/ is not a Jupiter path)."
        )
    return root


def resolve_archive_path(raw: str) -> str:
    """Resolve explicit archive_path under live CORTEX_FILES_ROOT."""
    root = verify_harvest_root()
    rel, err = normalize_cortex_files_path(
        raw,
        root,
        field="archive_path",
        reason_prefix="archive_path",
    )
    if err is not None:
        raise ArchivePathError(err)
    assert rel is not None
    abs_path = (root / rel).resolve()
    try:
        abs_path.relative_to(root.resolve())
    except ValueError as exc:
        raise ArchivePathError(
            {
                "error": f"archive_path escapes CORTEX_FILES_ROOT: {exc}",
                "reason": "archive_path.sandbox_escape",
                "field": "archive_path",
                "expected": "path resolved under CORTEX_FILES_ROOT",
                "files_root": str(root.resolve()),
                "received": raw,
                "hint": "Do not use .. or absolute paths outside the sandbox.",
            }
        ) from exc
    return str(abs_path)


def resolve_prompt(req: SubmitProjectAskRequest) -> list[str]:
    if req.prompt_text and req.prompt_text.strip():
        return [req.prompt_text.strip()]
    if req.prompt_uri and req.prompt_uri.strip():
        return [_load_prompt_uri(req.prompt_uri.strip())]
    if req.prompt_path and req.prompt_path.strip():
        path = resolve_prompt_path(req.prompt_path.strip(), project_root_base())
        if not path.is_file():
            raise ValueError(f"prompt_path not found: {req.prompt_path!r}")
        return [path.read_text(encoding="utf-8")]
    raise ValueError("provide prompt_text, prompt_uri, or prompt_path")


def _load_prompt_uri(uri: str) -> str:
    if not uri.startswith("cortex://"):
        raise ValueError("prompt_uri must use cortex:// scheme")
    rel = uri.removeprefix("cortex://").lstrip("/")
    root = verify_harvest_root()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"prompt_uri {uri!r} escapes CORTEX_FILES_ROOT") from exc
    if not path.is_file():
        raise ValueError(f"prompt_uri not found: {uri!r} -> {path}")
    return path.read_text(encoding="utf-8")


def default_archive_path(
    req: SubmitProjectAskRequest,
    *,
    execution_id: str = "",
) -> str:
    if req.archive_path:
        return resolve_archive_path(req.archive_path)
    root = verify_harvest_root()
    if req.project_uuid:
        name = f"cdp-ask-archive-{req.project_uuid[:8]}.md"
    elif execution_id:
        name = f"cdp-ask-archive-new-{execution_id[:8]}.md"
    else:
        name = "cdp-ask-archive-new.md"
    return str(root / "notes/system/threads" / name)


def _result_dict(result: ProjectAskResult) -> dict[str, Any]:
    return result.as_dict()


async def run_execution(
    req: SubmitProjectAskRequest,
    *,
    execution_id: str = "",
    abort_check: Callable[[], Awaitable[bool]],
    on_registered: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    prompts = resolve_prompt(req)
    holder = req.holder.strip() or "cdp-ask-satellite"
    reg = cdp_registry.register_lane(holder=holder, purpose=req.purpose)
    if on_registered is not None:
        on_registered(reg.registration_id)
    try:
        if req.converse:
            delete_after = (
                bool(req.delete_after) if req.delete_after is not None else False
            )
            results = await run_project_conversation(
                prompts,
                project_uuid="" if req.no_project_uuid else req.project_uuid,
                model=req.model,
                delete_after=delete_after,
                cdp_url=reg.cdp_url,
                timeout_s=max(req.timeout_s, 600),
                min_growth=req.min_growth,
                min_body=req.min_body,
                ensure_cowork_auto=req.ensure_cowork_auto,
            )
            if await abort_check():
                abort_cleanup(reg, purpose=req.purpose)
                return {
                    "ok": False,
                    "status": "aborted",
                    "registration_id": reg.registration_id,
                    "error": "aborted",
                }
            last = results[-1] if results else None
            archive_uri = last.archive_uri if last else None
            if last and last.ok and not archive_uri and last.body:
                archive_uri = archive_harvest(
                    body=last.body,
                    url=last.url,
                    project_uuid=last.project_uuid,
                    model=last.model,
                    attested_model=last.attested_model,
                    archive_path=default_archive_path(
                        req, execution_id=execution_id
                    ),
                    execution_id=execution_id or None,
                )
            return {
                "ok": all(r.ok for r in results),
                "registration_id": reg.registration_id,
                "archive_uri": archive_uri,
                "results": [_result_dict(r) for r in results],
                "body": last.body if last else "",
                "body_len": last.body_len if last else 0,
                "url": last.url if last else "",
                "project_uuid": last.project_uuid if last else "",
                "project_url": last.project_url if last else "",
                "model": last.model if last else {},
                "attested_model": last.attested_model if last else None,
                "error": None if all(r.ok for r in results) else "conversation failed",
            }

        if req.no_project_uuid and not req.converse:
            raise ValueError(
                "no_project_uuid requires converse=true for /new consult"
            )
        if not req.project_uuid and not req.no_project_uuid:
            raise ValueError("project_uuid required unless no_project_uuid=true")

        prompt = prompts[0]
        delete_after = (
            bool(req.delete_after) if req.delete_after is not None else True
        )
        archive = (
            default_archive_path(req, execution_id=execution_id)
            if delete_after
            else (
                resolve_archive_path(req.archive_path) if req.archive_path else None
            )
        )
        result = await run_project_ask(
            prompt,
            project_uuid=req.project_uuid,
            model=req.model,
            delete_after=delete_after,
            cdp_url=reg.cdp_url,
            timeout_s=req.timeout_s,
            min_growth=req.min_growth,
            min_body=req.min_body,
            archive_path=archive,
            execution_id=execution_id or None,
        )
        if await abort_check():
            abort_cleanup(reg, purpose=req.purpose)
            return {
                "ok": False,
                "status": "aborted",
                "registration_id": reg.registration_id,
                "error": "aborted",
            }
        payload = _result_dict(result)
        payload["registration_id"] = reg.registration_id
        return payload
    finally:
        if not await abort_check():
            deregister_on_exit(reg, purpose=req.purpose)


def resolve_project_root_path(raw: str) -> Path:
    return resolve_prompt_path(raw, project_root_base())
