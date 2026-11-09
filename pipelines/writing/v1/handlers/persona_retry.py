"""Bounded persona author retry when options.persona_retry is enabled."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from systems.pipeline.core.prompts import PromptBuilder

from . import prompt_store
from .judge_schema import PersonaJudgment

_JUDGMENT_READING_CAP = 240
MAX_PERSONA_ATTEMPTS = 2
AuthorCaller = Callable[[str], Awaitable[Any]]
ValidateFn = Callable[..., Awaitable[Any]]


def _failed_verdict(refused: str) -> Any:
    from .local_persona_author import PersonaValidationResult

    return PersonaValidationResult(text="", ok=False, refused=refused)


def persona_retry_enabled(options: dict[str, Any] | None) -> bool:
    """True when the pipeline option explicitly enables persona retry."""
    opts = options or {}
    return bool(opts.get("persona_retry", False))


def retriable_persona_refusal(verdict: Any) -> bool:
    """Only persona_validation_failed (any detail) may trigger attempt 2."""
    if verdict.ok:
        return False
    return verdict.refused == "persona_validation_failed"


def build_retry_feedback(verdict: Any) -> str:
    """Render do-not-introduce feedback for attempt 2."""
    invented = list(verdict.judged_invented or [])
    if invented:
        tokens = ", ".join(invented)
        return (
            "Your prior attempt introduced tokens not present in the source blocks. "
            f"Do not introduce: {tokens}."
        )
    detail = verdict.detail or "invented_or_invalid_prose"
    return (
        "Your prior attempt failed persona validation. "
        f"Do not repeat this failure mode ({detail})."
    )


def _judgments_payload(
    judgments: tuple[PersonaJudgment, ...] | None,
) -> list[dict[str, str]] | None:
    if not judgments:
        return None
    return [
        {
            "token": row.token,
            "kind": row.kind,
            "basis": row.basis,
            "reading": row.reading[:_JUDGMENT_READING_CAP],
        }
        for row in judgments
    ]


def render_persona_prompt(
    *,
    brief_block: str,
    documents_block: str,
    architect_brief_block: str,
    retry_feedback: str = "",
) -> str:
    """Build the local persona author prompt from the YAML template."""
    row = prompt_store.load_prompts().get("local_persona_author")
    if not isinstance(row, dict):
        return ""
    template = str(row.get("template") or "").strip()
    if not template:
        return ""
    return PromptBuilder().render(
        template,
        {
            "brief": brief_block,
            "documents": documents_block,
            "architect_brief": architect_brief_block,
            "retry_feedback": retry_feedback,
        },
    )


def attempt_record(
    attempt: int,
    verdict: Any,
    *,
    rejected_text: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> dict[str, Any]:
    """Serialize one persona attempt for the step json."""
    row: dict[str, Any] = {
        "attempt": attempt,
        "ok": verdict.ok,
        "refused": verdict.refused,
        "detail": verdict.detail,
        "flagged_candidates": verdict.flagged_candidates,
        "judged_invented": verdict.judged_invented,
        "judgments": _judgments_payload(verdict.judgments),
        "rejected_text": rejected_text,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if verdict.ok:
        row["refused"] = None
    return row


def _tokens_from_result(result: Any) -> tuple[int | None, int | None]:
    prompt = getattr(result, "prompt_tokens", None)
    completion = getattr(result, "completion_tokens", None)
    if prompt is None and isinstance(result, dict):
        prompt = result.get("prompt_tokens")
    if completion is None and isinstance(result, dict):
        completion = result.get("completion_tokens")
    return (
        int(prompt) if prompt is not None else None,
        int(completion) if completion is not None else None,
    )


def _content_from_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    return content if isinstance(content, str) else str(content or "")


async def run_persona_attempts(
    *,
    options: dict[str, Any] | None,
    brief_block: str,
    documents_block: str,
    architect_brief_block: str,
    author_caller: AuthorCaller,
    judge_caller: Any,
    validate_persona_prose: ValidateFn,
) -> tuple[Any, list[dict[str, Any]]]:
    """Run one or two author attempts with optional retry after validation failure."""
    enabled = persona_retry_enabled(options)
    attempts: list[dict[str, Any]] = []
    retry_feedback = ""
    verdict: Any = None

    max_attempts = MAX_PERSONA_ATTEMPTS if enabled else 1
    for attempt in range(1, max_attempts + 1):
        prompt = render_persona_prompt(
            brief_block=brief_block,
            documents_block=documents_block,
            architect_brief_block=architect_brief_block,
            retry_feedback=retry_feedback,
        )
        if not prompt:
            verdict = _failed_verdict("persona_prompt_missing")
            attempts.append(
                attempt_record(
                    attempt,
                    verdict,
                    rejected_text="",
                    prompt_tokens=None,
                    completion_tokens=None,
                )
            )
            break
        author_error: str | None = None
        try:
            raw_result = await author_caller(prompt)
        except Exception as exc:
            author_error = type(exc).__name__
            verdict = _failed_verdict("persona_author_failed")
            record = attempt_record(
                attempt,
                verdict,
                rejected_text="",
                prompt_tokens=None,
                completion_tokens=None,
            )
            record["error"] = author_error
            attempts.append(record)
            break
        prompt_tokens, completion_tokens = _tokens_from_result(raw_result)
        raw = _content_from_result(raw_result)
        verdict = await validate_persona_prose(
            raw,
            brief_block=brief_block,
            documents_block=documents_block,
            architect_brief_block=architect_brief_block,
            judge_caller=judge_caller,
        )
        attempts.append(
            attempt_record(
                attempt,
                verdict,
                rejected_text=verdict.text or raw,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )
        if verdict.ok:
            break
        if attempt >= max_attempts:
            break
        if not retriable_persona_refusal(verdict):
            break
        retry_feedback = build_retry_feedback(verdict)

    assert verdict is not None
    return verdict, attempts
