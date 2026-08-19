"""Resolve prompts and run claude_bundles project-ask on a registry lane."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cdp_registry.driving_seat import ensure_driving_operator_seat
from claude_bundles.cowork_output_download import should_attempt_output_download
from claude_bundles.cse_wake_retain import registration_has_wake_debt
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
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES
from cortex_store.files_path_normalize import normalize_cortex_files_path

from cdp_ask.models import SubmitProjectAskRequest, classify_stall_stage
from cdp_ask.page_liveness import (
    LadderAdvanceState,
    LadderCallbacks,
    advance_ladder_from_harvest,
    make_harvest_ladder_hook,
)

_CSE_URL_MARKER = "claude.ai/cowork/cse_"


def bind_execution_lane(req: SubmitProjectAskRequest, *, holder: str):
    """Bind the Chrome host for one project-ask execution.

    Operator-proxy (non-hop) executions on a named ``parent_thread`` reuse or
    mint the driving operator seat so census can see the seated window after
    hop Chromes go dormant. Hops and non-operator purposes still mint a fresh
    ``register_lane`` row.
    """
    purpose = (req.purpose or "").strip()
    kind = (req.mission_kind or "").strip()
    parent = (req.parent_thread or "").strip()
    if purpose in OPERATOR_PURPOSES and kind != "hop" and parent:
        return ensure_driving_operator_seat(
            holder=holder,
            parent_thread=parent,
            purpose=purpose,
            mission_kind=kind or "root",
        )
    return cdp_registry.register_lane(
        holder=holder,
        purpose=req.purpose,
        mission_kind=req.mission_kind,
        parent_thread=req.parent_thread,
    )


def _persist_session_address(
    registration_id: str,
    url: str | None,
    *,
    execution_id: str = "",
) -> None:
    """Record CSE chat_url on the registry row as soon as it is observed."""
    raw = (url or "").strip()
    if not raw or _CSE_URL_MARKER not in raw:
        return
    cdp_registry.bind_session_address(
        registration_id,
        chat_url=raw,
        execution_id=execution_id or None,
    )


def _wrap_harvest_with_address(
    on_harvest: Callable[[dict[str, Any]], Awaitable[None]] | None,
    *,
    registration_id: str,
    execution_id: str,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Compose harvest hook so first CSE URL binds at birth (not only terminal)."""

    async def _hook(state: dict[str, Any]) -> None:
        _persist_session_address(
            registration_id,
            str(state.get("url") or "") or None,
            execution_id=execution_id,
        )
        if on_harvest is not None:
            await on_harvest(state)

    return _hook


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


def resolve_followup_prompt(req: object) -> str:
    """Load one prompt string for followup (inline, cortex URI, or path).

    Prefer ``prompt_uri`` for large advisories. Raises ``ValueError`` when all
    ingress fields are absent (caller maps to ``no_prompt``).
    """
    prompt_text = getattr(req, "prompt_text", None)
    prompt_uri = getattr(req, "prompt_uri", None)
    prompt_path = getattr(req, "prompt_path", None)
    if prompt_text and str(prompt_text).strip():
        return str(prompt_text).strip()
    if prompt_uri and str(prompt_uri).strip():
        return _load_prompt_uri(str(prompt_uri).strip())
    if prompt_path and str(prompt_path).strip():
        path = resolve_prompt_path(str(prompt_path).strip(), project_root_base())
        if not path.is_file():
            raise ValueError(f"prompt_path not found: {prompt_path!r}")
        return path.read_text(encoding="utf-8")
    raise ValueError("provide prompt_text, prompt_uri, or prompt_path")


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


def resolve_stargate_execution_id(req: SubmitProjectAskRequest) -> str:
    """Return Stargate seating id for skill-delivery attest correlation.

    Prefer the explicit submit field; fall back to parsing
    ``…/ephemeral/cdp-endpoint/<id>/…`` from ``prompt_uri`` (v1 carrier).
    Empty when neither is available — keys still required present at emit.
    """
    explicit = (req.stargate_execution_id or "").strip()
    if explicit:
        return explicit
    uri = (req.prompt_uri or "").strip()
    marker = "ephemeral/cdp-endpoint/"
    if marker not in uri:
        return ""
    rest = uri.split(marker, 1)[1]
    return rest.split("/", 1)[0].strip()


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


def _seat_token_for_archive(req: SubmitProjectAskRequest) -> str:
    """Derive a stable seat token from request model/purpose (trusted submit fields)."""
    model = (req.model or "").lower()
    if "fable" in model:
        return "cdp-fable"
    if "opus" in model:
        return "cdp-opus"
    purpose = (req.purpose or "").strip().lower()
    if purpose in {"operator-proxy", "mission"}:
        return "cdp-opus"
    token = re.sub(r"[^a-z0-9]+", "-", model).strip("-")
    return token or "cdp"


def default_archive_path(
    req: SubmitProjectAskRequest,
    *,
    execution_id: str = "",
) -> str:
    """Resolve the harvest archive filesystem path for one project-ask execution.

    Consult-class mint embeds seat + **full** execution_id (not exec8 truncation)
    so dual-advisor answers cannot collide on a shared 8-hex prefix.
    """
    if req.archive_path:
        return resolve_archive_path(req.archive_path)
    root = verify_harvest_root()
    seat = _seat_token_for_archive(req)
    if req.project_uuid:
        # Project-scoped archives remain UUID-keyed; seat disambiguates advisors.
        name = f"cdp-ask-archive-{seat}-{req.project_uuid}.md"
    elif execution_id:
        name = f"cdp-ask-archive-{seat}-{execution_id}.md"
    else:
        name = f"cdp-ask-archive-{seat}-new.md"
    return str(root / "notes/system/threads" / name)


def _result_dict(result: ProjectAskResult) -> dict[str, Any]:
    return result.as_dict()


def _wake_debt_extras(registration_id: str, *, ok: bool) -> dict[str, bool]:
    if ok and registration_has_wake_debt(registration_id):
        return {"awaiting_wake_debt": True}
    return {}


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
    reg = bind_execution_lane(req, holder=holder)
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
    on_harvest = _wrap_harvest_with_address(
        on_harvest,
        registration_id=reg.registration_id,
        execution_id=execution_id,
    )
    try:
        if req.converse:
            delete_after = (
                bool(req.delete_after) if req.delete_after is not None else False
            )
            stargate_execution_id = resolve_stargate_execution_id(req)
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
                stargate_execution_id=stargate_execution_id,
                satellite_execution_id=execution_id,
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
                    _persist_session_address(
                        reg.registration_id,
                        last.url,
                        execution_id=execution_id,
                    )
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
                if not last.archive_uri and archive_uri:
                    backfilled = ProjectAskResult(
                        ok=last.ok,
                        body=last.body,
                        url=last.url,
                        project_uuid=last.project_uuid,
                        project_url=last.project_url,
                        model=last.model,
                        body_len=last.body_len,
                        delete_after=last.delete_after,
                        error=last.error,
                        archive_uri=archive_uri,
                        attested_model=last.attested_model,
                        harvest_provenance=last.harvest_provenance,
                    )
                    results[-1] = backfilled
                    last = backfilled
            await _release_f6_and_advance_proof(
                progress=progress, ladder=ladder, archive_uri=archive_uri
            )
            conv_ok = all(r.ok for r in results)
            _persist_session_address(
                reg.registration_id,
                last.url if last else None,
                execution_id=execution_id,
            )
            return {
                "ok": conv_ok,
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
                "error": None if conv_ok else "conversation failed",
                "stall_stage": (
                    classify_stall_stage(last.error if last else "conversation failed")
                    if not conv_ok
                    else None
                ),
                **_wake_debt_extras(reg.registration_id, ok=conv_ok),
            }

        if req.no_project_uuid and not req.converse:
            raise ValueError("no_project_uuid requires converse=true for /new consult")
        if not req.project_uuid and not req.no_project_uuid:
            raise ValueError("project_uuid required unless no_project_uuid=true")

        prompt = prompts[0]
        delete_after = bool(req.delete_after) if req.delete_after is not None else True
        archive = (
            default_archive_path(req, execution_id=execution_id)
            if delete_after
            else (resolve_archive_path(req.archive_path) if req.archive_path else None)
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
            stargate_execution_id=resolve_stargate_execution_id(req),
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
        _persist_session_address(
            reg.registration_id,
            result.url,
            execution_id=execution_id,
        )
        if not result.ok:
            payload["stall_stage"] = classify_stall_stage(result.error)
        elif result.archive_uri and ladder and ladder.on_archiving:
            await ladder.on_archiving()
        await _release_f6_and_advance_proof(
            progress=progress,
            ladder=ladder,
            archive_uri=result.archive_uri if result.ok else None,
        )
        payload.update(_wake_debt_extras(reg.registration_id, ok=result.ok))
        return payload
    finally:
        if not await abort_check():
            if not registration_has_wake_debt(reg.registration_id):
                deregister_on_exit(reg, purpose=req.purpose)


def resolve_project_root_path(raw: str) -> Path:
    """Resolve a checkout-relative or absolute prompt path under PROJECT_ROOT."""
    return resolve_prompt_path(raw, project_root_base())
