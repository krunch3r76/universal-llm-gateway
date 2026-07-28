"""DensifyCandidateReady post-Composer / pre-web-harden transition handler."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .admission import FrontierEndpointError
from .densify_triage import (
    COMPOSER_DRAFT_SENTINEL,
    REASONING_TRACE_SENTINEL,
    VALID_DRAFT_ADEQUACY,
    build_generate_review_envelope,
    is_default_on_density,
    validate_density_triage_value,
)
from .events import FrontierDensifyReviewAdmitted
from .material_decision_gate import material_decision_closeout_flags

EventPublisher = Callable[[Any], None]

_DEFAULT_REVIEWER_ROLE = "reviewer"


def _default_reviewer_model() -> str:
    from implement_admission.check_review_substrate import (
        load_check_review_default_model,
    )
    from implement_admission.routing import load_route_policy

    return load_check_review_default_model(load_route_policy())


class DensifyCandidateReadyBody(BaseModel):
    """Post-draft candidate payload — fires after Composer writes staged draft."""

    model_config = {"extra": "forbid"}

    draft_adequacy: str
    staged_draft_uri: str
    reasoning_trace_uri: str
    parent_dispatch_thread_id: str
    parent_execution_id: str | None = None
    parent_request_id: str | None = None
    density_triage: str | None = None
    review_opt_out_reason_code: str | None = None
    material_decision_present: bool = False
    panel_artifact: dict[str, Any] | None = None
    staged_draft_body: str | None = None
    reasoning_trace_body: str | None = None


def _validate_candidate_body(
    body: DensifyCandidateReadyBody,
    *,
    request_id: str,
) -> None:
    if body.draft_adequacy not in VALID_DRAFT_ADEQUACY:
        raise FrontierEndpointError(
            request_id=request_id,
            field="draft_adequacy",
            reason=f"unknown draft_adequacy: {body.draft_adequacy!r}",
            status_code=422,
            code="draft_adequacy_unknown",
        )
    validate_density_triage_value(body.density_triage, request_id=request_id)
    if not body.staged_draft_uri.strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="staged_draft_uri",
            reason="staged_draft_uri is required",
            status_code=422,
            code="staged_draft_uri_missing",
        )
    if not body.reasoning_trace_uri.strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="reasoning_trace_uri",
            reason="reasoning_trace_uri is required",
            status_code=422,
            code="reasoning_trace_uri_missing",
        )
    if not body.parent_dispatch_thread_id.strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="parent_dispatch_thread_id",
            reason="parent_dispatch_thread_id is required",
            status_code=422,
            code="parent_dispatch_thread_id_missing",
        )
    if body.review_opt_out_reason_code is not None:
        from .densify_triage import REVIEW_OPT_OUT_REASON_CODES

        if body.review_opt_out_reason_code not in REVIEW_OPT_OUT_REASON_CODES:
            raise FrontierEndpointError(
                request_id=request_id,
                field="review_opt_out_reason_code",
                reason=(
                    f"unknown review_opt_out_reason_code: "
                    f"{body.review_opt_out_reason_code!r}"
                ),
                status_code=422,
                code="review_opt_out_unknown",
            )
        if not is_default_on_density(body.density_triage):
            raise FrontierEndpointError(
                request_id=request_id,
                field="review_opt_out_reason_code",
                reason="opt-out is only valid for default-on density_triage",
                status_code=422,
                code="review_opt_out_non_default_on",
            )


def _load_artifact_body(uri: str, inline: str | None) -> str:
    if inline is not None:
        return inline
    return f"{COMPOSER_DRAFT_SENTINEL}\n# staged draft from {uri}"


def _load_trace_body(uri: str, inline: str | None) -> str:
    if inline is not None:
        return inline
    return f"{REASONING_TRACE_SENTINEL}\n# reasoning trace from {uri}"


_DEFAULT_NEGATIVE_SPACE = (
    "Per new/changed param or branch: where invalid + does the "
    "spec reject it there with a test?"
)


def build_reviewer_prompt(
    *,
    staged_draft_body: str,
    reasoning_trace_body: str,
    negative_space: str | None = None,
) -> str:
    resolved_negative_space = (
        _DEFAULT_NEGATIVE_SPACE if negative_space is None else negative_space
    )
    return (
        f"<negative_space>{resolved_negative_space}</negative_space>\n"
        f"<composer_draft>\n{staged_draft_body}\n</composer_draft>\n"
        f"<reasoning_trace>\n{reasoning_trace_body}\n</reasoning_trace>"
    )


class _PromptOverride:
    def __init__(self, text: str) -> None:
        self._text = text
        self._original: Any = None

    def __enter__(self) -> None:
        from . import dispatch_thread_context

        self._original = dispatch_thread_context.read_latest_dispatch_thread_body

        async def _stub(**kwargs: Any) -> str:
            return self._text

        dispatch_thread_context.read_latest_dispatch_thread_body = _stub

    def __exit__(self, *args: object) -> None:
        from . import dispatch_thread_context

        dispatch_thread_context.read_latest_dispatch_thread_body = self._original


async def spawn_densify_reviewer_child(
    *,
    request_id: str,
    parent_dispatch_thread_id: str,
    reviewer_prompt: str,
    response: Response,
) -> dict[str, Any]:
    """Spawn one cross-family reviewer on check/review standing default substrate."""
    from model_id import ModelId

    from .route import (
        TeamDispatchGenerateBody,
        TeamDispatchToThreadBody,
        team_dispatch,
    )

    reviewer_model = _default_reviewer_model()
    if ModelId.parse(reviewer_model).backend_type == "cursor_sdk":
        child_body: TeamDispatchGenerateBody | TeamDispatchToThreadBody = (
            TeamDispatchGenerateBody(
                op="generate",
                seat="cursor-sdk",
                dispatch_thread_id=parent_dispatch_thread_id,
                model=reviewer_model,
                contract="light-bounded",
                prompt=reviewer_prompt,
                auto_review_child=False,
                spawn_review_provenance="generate_review_child",
            )
        )
        result = await team_dispatch(child_body, response)
    else:
        child_body = TeamDispatchToThreadBody(
            op="to_thread",
            role=_DEFAULT_REVIEWER_ROLE,
            dispatch_thread_id=parent_dispatch_thread_id,
            thread=parent_dispatch_thread_id,
            subject=f"densify cross-family review — {request_id[:8]}",
            contract="light-bounded",
            model=reviewer_model,
            auto_review_child=True,
        )
        with _PromptOverride(reviewer_prompt):
            result = await team_dispatch(child_body, response)
    if isinstance(result, JSONResponse):
        return {"error": "reviewer_spawn_failed"}
    return result if isinstance(result, dict) else {}


async def handle_densify_candidate_ready(
    *,
    request_id: str,
    body: DensifyCandidateReadyBody,
    response: Response,
    event_publisher: EventPublisher | None = None,
) -> dict[str, Any] | JSONResponse:
    _validate_candidate_body(body, request_id=request_id)

    parent_request_id = body.parent_request_id or request_id
    densify_thread_id = body.parent_dispatch_thread_id
    density = body.density_triage
    default_on = is_default_on_density(density)
    opted_out = body.review_opt_out_reason_code is not None

    material_flags = material_decision_closeout_flags(
        material_decision_present=body.material_decision_present,
        panel_artifact=body.panel_artifact,
    )

    if body.draft_adequacy == "blank":
        if event_publisher is not None and default_on:
            event_publisher(
                FrontierDensifyReviewAdmitted(
                    parent_request_id=parent_request_id,
                    parent_execution_id=body.parent_execution_id,
                    parent_dispatch_thread_id=densify_thread_id,
                    densify_thread_id=densify_thread_id,
                    staged_draft_uri=body.staged_draft_uri,
                    reasoning_trace_uri=body.reasoning_trace_uri,
                    density_triage=density,
                    draft_adequacy=body.draft_adequacy,
                    opt_out=False,
                    opt_out_reason_code=None,
                    reviewer_family=None,
                    reviewer_model=None,
                    target_thread_id=None,
                    review_execution_id=None,
                    review_spawned=False,
                    hold_reason="blank_adequacy",
                )
            )
        return {
            "status": "hold",
            "hold_reason": "blank_adequacy",
            "review_spawned": False,
            "auto_review_spawned": False,
            **material_flags,
        }

    review_spawned = False
    review_execution_id: str | None = None
    spawn_result: dict[str, Any] | None = None
    reviewer_prompt = ""

    should_spawn = (
        body.draft_adequacy in {"partial", "adequate"} and default_on and not opted_out
    )

    if should_spawn:
        draft_body = _load_artifact_body(body.staged_draft_uri, body.staged_draft_body)
        trace_body = _load_trace_body(
            body.reasoning_trace_uri, body.reasoning_trace_body
        )
        reviewer_prompt = build_reviewer_prompt(
            staged_draft_body=draft_body,
            reasoning_trace_body=trace_body,
        )
        spawn_result = await spawn_densify_reviewer_child(
            request_id=request_id,
            parent_dispatch_thread_id=densify_thread_id,
            reviewer_prompt=reviewer_prompt,
            response=response,
        )
        review_spawned = True
        review_execution_id = (
            str(spawn_result.get("execution_id"))
            if isinstance(spawn_result, dict) and spawn_result.get("execution_id")
            else None
        )

    if event_publisher is not None and default_on:
        from implement_admission.check_review_substrate import independence_family

        spawned_model = _default_reviewer_model() if should_spawn else None
        event_publisher(
            FrontierDensifyReviewAdmitted(
                parent_request_id=parent_request_id,
                parent_execution_id=body.parent_execution_id,
                parent_dispatch_thread_id=densify_thread_id,
                densify_thread_id=densify_thread_id,
                staged_draft_uri=body.staged_draft_uri,
                reasoning_trace_uri=body.reasoning_trace_uri,
                density_triage=density,
                draft_adequacy=body.draft_adequacy,
                opt_out=opted_out,
                opt_out_reason_code=body.review_opt_out_reason_code,
                reviewer_family=(
                    independence_family(spawned_model) if spawned_model else None
                ),
                reviewer_model=spawned_model,
                target_thread_id=densify_thread_id if should_spawn else None,
                review_execution_id=review_execution_id,
                review_spawned=review_spawned,
            )
        )

    envelope = build_generate_review_envelope(
        density_triage=density,
        review_opt_out_reason_code=body.review_opt_out_reason_code,
        auto_review_child=False,
    )
    result: dict[str, Any] = {
        "status": "ready",
        "review_spawned": review_spawned,
        "auto_review_spawned": review_spawned,
        "reviewer_prompt": reviewer_prompt,
        **envelope,
        **material_flags,
    }
    if spawn_result is not None:
        result["review_dispatch"] = spawn_result
    return result
