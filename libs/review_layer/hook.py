"""API-role admit hook: detection, async materialize, and Stargate error mapping."""

from __future__ import annotations

import asyncio
from typing import Any

from .detect import detect_review_targets, implicit_target_heads, resolve_primary_target
from .materialize import (
    ReviewTargetUnreachable,
    any_cited_layer_present,
    format_review_layer_prompt_block,
    layer_uri,
    materialize_review_layer,
)


class ReviewLayerAdmitError(Exception):
    """Maps to FrontierEndpointError at the Stargate boundary."""

    def __init__(
        self,
        *,
        reason: str,
        kind: str,
        fix_hint: str,
        field: str = "prompt",
    ) -> None:
        self.reason = reason
        self.kind = kind
        self.fix_hint = fix_hint
        self.field = field
        super().__init__(reason)


def _infra_hint() -> str:
    return (
        "Hub git or cortex storage is unavailable. Retry after hub git is healthy "
        "or hand-materialize cortex://tmp/review-layers/<id>/ and cite it."
    )


def _target_hint(*, explicit: bool) -> str:
    if explicit:
        return (
            "Pass review_head: <sha> for merged work, commit the lane branch, or "
            "hand-materialize cortex://tmp/review-layers/<id>/ and cite it."
        )
    return (
        "Commit the lane branch, pass review_head: <ref|sha> on a line-start "
        "prompt line, or hand-materialize cortex://tmp/review-layers/<id>/ "
        "and cite it."
    )


def _classify_materialize_error(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, ReviewTargetUnreachable):
        return "target", exc.reason[:200]
    if isinstance(exc, (PermissionError, FileNotFoundError)):
        return "infra", "storage or git unavailable"
    if isinstance(exc, OSError):
        return "infra", "storage or git unavailable"
    return "infra", "review layer materialize failed"


async def apply_review_layer_admit(
    *,
    request_id: str,
    prompt_text: str,
    cortex_root: Any | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Return updated prompt and optional ``review_layer`` payload for API admits."""
    from implement_admission.closeout_helpers import cortex_files_root

    root = cortex_root or cortex_files_root()
    if any_cited_layer_present(prompt_text, root):
        return prompt_text, None

    detection = detect_review_targets(prompt_text)
    implicit_heads = implicit_target_heads(detection)
    if len(implicit_heads) > 1:
        raise ReviewLayerAdmitError(
            reason="multiple implicit review targets",
            kind="ambiguous",
            fix_hint=(
                "Add a single line-start review_head: <ref|sha> (optional "
                "review_base:) naming the revision to review."
            ),
        )

    target = resolve_primary_target(detection)
    if target is None:
        return prompt_text, None

    explicit = target.kind == "explicit_head"
    try:
        manifest = await asyncio.to_thread(
            materialize_review_layer,
            layer_id=request_id,
            head_ref=target.head,
            base_ref=target.base,
            cortex_root=root,
        )
    except Exception as exc:
        kind, short = _classify_materialize_error(exc)
        if not explicit and kind == "target":
            return prompt_text, {
                "status": "skipped_unresolvable",
                "token": target.head,
            }
        fix = _target_hint(explicit=explicit) if kind == "target" else _infra_hint()
        raise ReviewLayerAdmitError(
            reason=short,
            kind=kind,
            fix_hint=fix,
        ) from exc

    if manifest is None:
        return prompt_text, None

    payload = {
        "uri": layer_uri(request_id),
        "base_sha": manifest.base_sha,
        "head_sha": manifest.head_sha,
        "diff_sha256": manifest.diff_sha256,
    }
    if manifest.truncated:
        payload["truncated"] = True
    block = format_review_layer_prompt_block(layer_id=request_id, manifest=manifest)
    return prompt_text + block, payload


async def bind_api_prompt(
    request_id: str,
    prompt_text: str,
    frontier_error: type[Any],
) -> tuple[str, dict[str, Any] | None]:
    """Run admit and raise ``frontier_error`` (422) on review-layer failures."""
    try:
        return await apply_review_layer_admit(
            request_id=request_id,
            prompt_text=prompt_text,
        )
    except ReviewLayerAdmitError as exc:
        raise frontier_error(
            request_id=request_id,
            field=exc.field,
            reason=exc.reason,
            status_code=422,
            code="REVIEW_TARGET_UNREACHABLE",
            details={"kind": exc.kind, "fix_hint": exc.fix_hint},
        ) from exc
