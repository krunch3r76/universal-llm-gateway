"""POST /api/v1/life/dispatch — life-facing CDP generate relay."""

from __future__ import annotations

import uuid
from typing import Any

from claude_bundles.chat_model_match import compose_cdp_model_with_effort
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

from .admission import FrontierEndpointError
from .cdp_generate import dispatch_cdp_generate, is_cdp_model
from .cdp_project_bindings import cdp_project_binding
from .dispatch_thread_context import resolve_generate_prompt_body
from .route import TeamDispatchGenerateBody

life_dispatch_router = APIRouter(prefix="/api/v1/life", tags=["life-dispatch"])

_DEFAULT_MODEL = "cdp/opus-5"
_LIFE_CALLER = "life"


class LifeDispatchBody(BaseModel):
    """Life MCP ``life_dispatch`` body — four agent-visible properties only."""

    model_config = {"extra": "forbid"}

    prompt: str | None = None
    thread: str | None = None
    model: str = _DEFAULT_MODEL
    skills: list[str] | None = None

    @model_validator(mode="after")
    def _require_prompt_or_thread(self) -> LifeDispatchBody:
        if not self.prompt and not self.thread:
            raise ValueError("exactly one of prompt or thread is required")
        if self.prompt and self.thread:
            raise ValueError("prompt and thread are mutually exclusive")
        return self


def _resolve_sidecar_or_prompt(raw: str) -> tuple[str | None, str | None]:
    text = raw.strip()
    if text.startswith("cortex://"):
        return None, text
    return text, None


@life_dispatch_router.post("/dispatch", status_code=202, response_model=None)
async def life_dispatch(
    body: LifeDispatchBody, response: Response
) -> dict[str, Any] | JSONResponse:
    """Admit a life-surface CDP dispatch bound to the configured Life project."""
    request_id = str(uuid.uuid4())
    try:
        model = compose_cdp_model_with_effort(body.model, None)
        if not is_cdp_model(model):
            raise FrontierEndpointError(
                request_id=request_id,
                field="model",
                reason="life_dispatch requires model=cdp/<picker>",
                status_code=422,
                code="cdp_model_required",
            )

        prompt: str | None = None
        sidecar_ref: str | None = None
        dispatch_thread_id: str | None = None
        if body.thread:
            dispatch_thread_id = body.thread.strip()
            prompt = await resolve_generate_prompt_body(
                request_id=request_id,
                role=_LIFE_CALLER,
                dispatch_thread_id=dispatch_thread_id,
                prompt=None,
                sidecar_ref=None,
                packet_path=None,
            )
        elif body.prompt:
            prompt, sidecar_ref = _resolve_sidecar_or_prompt(body.prompt)

        project_uuid = cdp_project_binding("life", request_id=request_id)
        body_kwargs: dict[str, Any] = {
            "op": "generate",
            "model": model,
            "contract": "light-bounded",
            "purpose": "operator-proxy",
            "caller_agent": _LIFE_CALLER,
            "prompt": prompt,
            "sidecar_ref": sidecar_ref,
            "skills": body.skills,
            "generation_options": {
                "expected_size": "auto",
                "harvest_source": "auto",
                "download_output": False,
            },
        }
        if dispatch_thread_id:
            body_kwargs["dispatch_thread_id"] = dispatch_thread_id
        generate_body = TeamDispatchGenerateBody(**body_kwargs)
        result = await dispatch_cdp_generate(
            request_id=request_id,
            body=generate_body,
            response=response,
            project_uuid=project_uuid,
        )
    except FrontierEndpointError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    if isinstance(result, dict):
        response.status_code = 202
    return result
