"""Resolve prompts and run claude_bundles project-ask on a registry lane."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cowork_output_download import should_attempt_output_download
from claude_bundles.project_ask import (
    HarvestArchiveError,
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

from cdp_ask.models import SubmitProjectAskRequest, classify_stall_stage
from cdp_ask.page_liveness import (
    LadderAdvanceState,
    LadderCallbacks,
    advance_ladder_from_harvest,
    make_harvest_ladder_hook,
)


async def _release_f6_and_advance_proof(
    *,
    progress: LadderAdvanceState | None,
    ladder: LadderCallbacks | None,
    archive_uri: str | None,
) -> None:
    """Clear F6 pending after archive land so content_proof can fire (A2).

    ``output_download_pending`` blocks thin pre-download archives during wait.
    Once ``resolve_harvest_body`` + ``archive_harvest`` have stamped the final
    bytes, the flag must clear and the ladder must re-sample — otherwise
    content_proof stays suppressed for the whole download-attempting run.
    """
    if progress is None or ladder is None or not archive_uri:
        return
    if not progress.output_download_pending:
        return
    progress.output_download_pending = False
    await advance_ladder_from_harvest(
        {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "body_len": max(progress.min_bytes, 1),
        },
        callbacks=ladder,
        progress=progress,
    )


class ArchivePathError(ValueError):
    """Raised when explicit archive_path cannot resolve under CORTEX_FILES_ROOT."""

    def __init__(self, teaching: dict[str, Any]) -> None:
        self.teaching = teaching
        super().__init__(teaching.get("error") or "archive_path invalid")


def cortex_files_root() -> Path:
    """Return the resolved CORTEX_FILES_ROOT path (env override or default mount)."""
    raw = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "mcp-data" / "files").resolve()


def verify_harvest_root() -> Path:
    """Return CORTEX_FILES_ROOT after verifying the harvest directory exists."""
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
    """Load prompt text from inline body, cortex URI, or checkout-relative path.

    When ``purpose`` is an operator-proxy mission tag, ensure CDP skill chips
    and the Opus-operator / Fable-advisor seat-map briefing
    (``claude_bundles.operator_proxy_mission``).
    """
    if req.prompt_text and req.prompt_text.strip():
        text = req.prompt_text.strip()
    elif req.prompt_uri and req.prompt_uri.strip():
        text = _load_prompt_uri(req.prompt_uri.strip())
    elif req.prompt_path and req.prompt_path.strip():
        path = resolve_prompt_path(req.prompt_path.strip(), project_root_base())
        if not path.is_file():
            raise ValueError(f"prompt_path not found: {req.prompt_path!r}")
        text = path.read_text(encoding="utf-8")
    else:
        raise ValueError("provide prompt_text, prompt_uri, or prompt_path")
    from claude_bundles.operator_proxy_mission import (
        ensure_operator_proxy_mission_prompt,
        purpose_implies_mission,
    )

    if purpose_implies_mission(req.purpose, text):
        text = ensure_operator_proxy_mission_prompt(text)
    return [text]


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
    """Resolve the harvest archive filesystem path for one project-ask execution."""
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


def _path_to_cortex_uri(path: Path, root: Path) -> str:
    rel = path.resolve().relative_to(root.resolve())
    return f"cortex://{rel.as_posix()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def resolve_content_proof_targets(
    req: SubmitProjectAskRequest,
    *,
    execution_id: str,
) -> list[tuple[Path, str]]:
    """Return filesystem paths and cortex URIs to watch for early durable proof."""
    root = verify_harvest_root()
    seen: set[Path] = set()
    targets: list[tuple[Path, str]] = []

    def _add(raw_path: Path, uri: str) -> None:
        resolved = raw_path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        targets.append((resolved, uri))

    if req.archive_path:
        _add(Path(resolve_archive_path(req.archive_path)), req.archive_path.strip())

    default_path = Path(default_archive_path(req, execution_id=execution_id))
    _add(default_path, _path_to_cortex_uri(default_path, root))

    prompt_uri = (req.prompt_uri or "").strip()
    if prompt_uri.startswith("cortex://") and prompt_uri.endswith("-r-prompt.md"):
        review_uri = prompt_uri.replace("-r-prompt.md", "-web-anthropic-review.md")
        rel = review_uri.removeprefix("cortex://").lstrip("/")
        _add(root / rel, review_uri)

    return targets


async def run_execution(
    req: SubmitProjectAskRequest,
    *,
    execution_id: str = "",
    abort_check: Callable[[], Awaitable[bool]],
    on_registered: Callable[[str], None] | None = None,
    ladder: LadderCallbacks | None = None,
) -> dict[str, Any]:
    """Run one registry-backed project-ask and return a terminal-shaped result dict."""
    prompts = resolve_prompt(req)
    holder = req.holder.strip() or "cdp-ask-satellite"
    reg = cdp_registry.register_lane(holder=holder, purpose=req.purpose)
    if on_registered is not None:
        on_registered(reg.registration_id)
    on_harvest: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    progress: LadderAdvanceState | None = None
    if ladder is not None:
        if not execution_id.strip():
            raise ValueError(
                "execution_id required for dual-completion ladder (non-empty)"
            )
        progress = LadderAdvanceState(
            targets=resolve_content_proof_targets(req, execution_id=execution_id),
            min_bytes=max(req.min_body, 1),
            sha256_file=_sha256_file,
            execution_id=execution_id,
            output_download_pending=should_attempt_output_download(
                harvest_source=req.harvest_source,
                expected_size=req.expected_size,
                download_output=req.download_output,
            ),
            blocked_archive_paths={
                Path(default_archive_path(req, execution_id=execution_id)).resolve(),
                *(
                    {Path(resolve_archive_path(req.archive_path)).resolve()}
                    if req.archive_path
                    else set()
                ),
            },
        )
        # Held-page samples only — competing connect_cdp blocked dual-completion
        # while converse held the lane (friction 25671).
        on_harvest = make_harvest_ladder_hook(callbacks=ladder, progress=progress)
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
                on_harvest=on_harvest,
                expected_size=req.expected_size,
                harvest_source=req.harvest_source,
                download_output=req.download_output,
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
                if ladder and ladder.on_archiving:
                    await ladder.on_archiving()
                try:
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
                except HarvestArchiveError as exc:
                    return {
                        "ok": False,
                        "registration_id": reg.registration_id,
                        "archive_uri": None,
                        "results": [_result_dict(r) for r in results],
                        "body": last.body,
                        "body_len": last.body_len,
                        "url": last.url,
                        "project_uuid": last.project_uuid,
                        "project_url": last.project_url,
                        "model": last.model,
                        "attested_model": last.attested_model,
                        "error": str(exc),
                        "harvest_provenance": None,
                        "stall_stage": classify_stall_stage(str(exc)),
                    }
            await _release_f6_and_advance_proof(
                progress=progress, ladder=ladder, archive_uri=archive_uri
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
                "harvest_provenance": (
                    last.harvest_provenance if last and last.ok else None
                ),
                "error": None if all(r.ok for r in results) else "conversation failed",
                "stall_stage": (
                    classify_stall_stage(last.error if last else "conversation failed")
                    if not all(r.ok for r in results)
                    else None
                ),
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
            on_harvest=on_harvest,
            expected_size=req.expected_size,
            harvest_source=req.harvest_source,
            download_output=req.download_output,
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
        if not result.ok:
            payload["stall_stage"] = classify_stall_stage(result.error)
        elif result.archive_uri and ladder and ladder.on_archiving:
            await ladder.on_archiving()
        await _release_f6_and_advance_proof(
            progress=progress,
            ladder=ladder,
            archive_uri=result.archive_uri if result.ok else None,
        )
        return payload
    finally:
        if not await abort_check():
            deregister_on_exit(reg, purpose=req.purpose)


def resolve_project_root_path(raw: str) -> Path:
    """Resolve a checkout-relative or absolute prompt path under PROJECT_ROOT."""
    return resolve_prompt_path(raw, project_root_base())
