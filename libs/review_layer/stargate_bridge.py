"""Stargate-facing entry for API-role review-layer admit (lazy hook import)."""

from __future__ import annotations

from typing import Any


async def review_layer_api_admit(
    request_id: str,
    prompt_text: str,
    frontier_error: type[Any],
) -> tuple[str, dict[str, Any] | None]:
    """Import hook lazily and map all failures to ``frontier_error`` 422."""
    try:
        from review_layer.hook import bind_api_prompt

        return await bind_api_prompt(request_id, prompt_text, frontier_error)
    except Exception as exc:
        if exc.__class__ is frontier_error:
            raise
        raise frontier_error(
            request_id=request_id,
            field="prompt",
            reason="review layer module unavailable",
            status_code=422,
            code="REVIEW_TARGET_UNREACHABLE",
            details={
                "kind": "infra",
                "fix_hint": (
                    "Retry after Stargate reload; or hand-materialize "
                    "cortex://tmp/review-layers/<id>/ and cite it."
                ),
            },
        ) from exc
