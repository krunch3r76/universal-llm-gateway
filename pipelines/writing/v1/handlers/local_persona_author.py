"""Frontier prose persona for local drafters — fail closed, no fallback."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.prompts import PromptBuilder
from universal_logging import get_logger
from writing_pipeline.events import WritingPersonaRefused

from .assemble import _fence
from .independence import load_writing_models
from .judge_schema import PersonaJudgment, parse_persona_judgments, persona_judge_schema
from .persona_retry import run_persona_attempts
from .prompt_architect import _NAME, _NUMBER
from .prompt_store import load_prompts

_BULLET = re.compile(r"(?m)^\s*[-*•]\s+\S")
_NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+\S")
_NUMBERED_PAREN = re.compile(r"(?m)^\s*\d+\)\s+\S")
_LETTERED = re.compile(r"(?m)^\s*[a-zA-Z]\.\s+\S")
_HEADING = re.compile(r"(?m)^\s*#+\s+\S")
_BLOCKS_WORD_RE = re.compile(r"[a-z][a-z'-]*")
_THINKING_PREAMBLE = re.compile(
    r"^\s*<\s*(?:redacted_thinking|think|thinking|reasoning)\b",
    re.IGNORECASE,
)
_JUDGE_THINKING_STRIP = re.compile(
    r"^\s*<think>[\s\S]*?</think>\s*",
    re.IGNORECASE,
)
_MAX_PERSONA_CANDIDATES = 32
_JUDGMENT_READING_CAP = 240
_MARKUP_TAG = re.compile(r"<[^>]+>")
_LOGGER = get_logger(__name__)
_CAPITAL_STOPWORDS = frozenset(
    {
        "you",
        "the",
        "when",
        "she",
        "his",
        "and",
        "but",
        "for",
        "with",
        "this",
        "that",
        "they",
        "their",
        "there",
        "then",
        "than",
        "from",
        "into",
        "your",
        "our",
        "who",
        "what",
        "where",
        "while",
        "also",
        "just",
        "only",
        "still",
        "even",
        "each",
        "every",
        "some",
        "such",
        "these",
        "those",
        "not",
        "can",
        "will",
        "would",
        "could",
        "should",
        "have",
        "has",
        "had",
        "are",
        "was",
        "were",
        "been",
        "does",
        "did",
    }
)

JudgeCaller = Callable[[str, list["Candidate"]], Awaitable[str]]


@dataclass(frozen=True)
class Candidate:
    """Heuristic flag passed to the persona judge."""

    token: str
    form: Literal["capitalized_word", "number"]
    position: Literal["sentence_start", "mid_sentence", "both"]
    contexts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PersonaValidationResult:
    """Outcome of persona prose validation (deterministic + optional judge)."""

    text: str
    ok: bool
    refused: str | None = None
    detail: str | None = None
    flagged_candidates: list[str] | None = None
    judged_invented: list[str] | None = None
    judgments: tuple[PersonaJudgment, ...] | None = None
    judge_called: bool = False


def _step(payload: dict[str, Any]) -> StepOutput:
    """Wrap a JSON dict as a pipeline step output."""
    return StepOutput(raw=json.dumps(payload), json=payload)


def _persona_judge_template() -> tuple[str, str]:
    """Load persona invented-judge system prompt and user template."""
    row = load_prompts().get("persona_invented_judge")
    if not isinstance(row, dict):
        return "", ""
    return (
        str(row.get("system_prompt") or "").strip(),
        str(row.get("template") or "").strip(),
    )


def _persona_allowed_tokens(
    *,
    brief_block: str,
    documents_block: str,
    architect_brief_block: str,
) -> set[str]:
    """Allowlist nouns and numbers taken only from persona prompt blocks."""
    allowed: set[str] = set()
    for chunk in (brief_block, documents_block, architect_brief_block):
        allowed |= {token.lower() for token in _NAME.findall(chunk)}
        allowed |= set(_NUMBER.findall(chunk))
    return allowed


def _has_list_structure(text: str) -> bool:
    """Return True when persona text uses bullets, numbers, or headings."""
    patterns = (_BULLET, _NUMBERED, _NUMBERED_PAREN, _LETTERED, _HEADING)
    return any(pattern.search(text) for pattern in patterns)


def _blocks_word_set(blocks_lower: str) -> set[str]:
    """Lowercase words extracted from persona prompt blocks (whole tokens only)."""
    return set(_BLOCKS_WORD_RE.findall(blocks_lower))


def _token_in_blocks_words(lower: str, blocks_words: set[str]) -> bool:
    """True when ``lower`` matches a blocks word, including trailing-s possessives."""
    if lower in blocks_words:
        return True
    variants = {lower}
    if lower.endswith("'s") and len(lower) > 2:
        variants.add(lower[:-2])
    if lower.endswith("s") and len(lower) > 3 and not lower.endswith("'s"):
        variants.add(lower[:-1])
    return bool(variants & blocks_words)


def _is_sentence_initial_at(text: str, start: int) -> bool:
    """True when the token at ``start`` begins a sentence."""
    if start == 0:
        return True
    before = text[:start]
    if re.search(r"\n\s*$", before):
        return True
    prefix = before.rstrip()
    if not prefix:
        return True
    return bool(re.search(r"[.!?:]\s*$", prefix))


def _sentence_span(text: str, pos: int) -> tuple[int, int]:
    """Return the start/end indices of the sentence containing ``pos``."""
    boundaries = [0]
    for match in re.finditer(r"(?<=[.!?:])\s+|\n+", text):
        boundaries.append(match.end())
    boundaries.append(len(text))
    start = 0
    for idx in range(len(boundaries) - 1):
        seg_start = boundaries[idx]
        seg_end = boundaries[idx + 1]
        if seg_start <= pos < seg_end:
            return seg_start, seg_end
        start = seg_end
    return start, len(text)


def _position_label(
    *,
    saw_start: bool,
    saw_mid: bool,
) -> Literal["sentence_start", "mid_sentence", "both"]:
    """Map sentence-position flags to a single candidate position label."""
    if saw_start and saw_mid:
        return "both"
    if saw_start:
        return "sentence_start"
    return "mid_sentence"


def _persona_candidates(
    text: str,
    *,
    allowed: set[str],
    blocks_words: set[str],
) -> list[Candidate]:
    """Collect capitalized words and numbers that need judge review."""
    hits: list[tuple[int, str, Literal["capitalized_word", "number"]]] = []

    for match in _NAME.finditer(text):
        token = match.group(0)
        lower = token.lower()
        if (
            lower in allowed
            or lower in _CAPITAL_STOPWORDS
            or _token_in_blocks_words(lower, blocks_words)
        ):
            continue
        hits.append((match.start(), token, "capitalized_word"))

    for match in _NUMBER.finditer(text):
        token = match.group(0)
        if token in allowed:
            continue
        hits.append((match.start(), token, "number"))

    hits.sort(key=lambda row: row[0])
    by_token: dict[str, dict[str, Any]] = {}
    for pos, token, form in hits:
        entry = by_token.setdefault(
            token,
            {
                "form": form,
                "saw_start": False,
                "saw_mid": False,
                "contexts": [],
            },
        )
        if _is_sentence_initial_at(text, pos):
            entry["saw_start"] = True
        else:
            entry["saw_mid"] = True
        s_start, s_end = _sentence_span(text, pos)
        snippet = _fence(text[s_start:s_end].strip())
        if snippet and snippet not in entry["contexts"] and len(entry["contexts"]) < 2:
            entry["contexts"].append(snippet)

    return [
        Candidate(
            token=token,
            form=meta["form"],
            position=_position_label(
                saw_start=meta["saw_start"],
                saw_mid=meta["saw_mid"],
            ),
            contexts=list(meta["contexts"]),
        )
        for token, meta in by_token.items()
    ]


def _persona_deterministic_failure(text: str) -> tuple[str, str] | None:
    """Return refused reason and detail when prose fails non-judgment checks."""
    if not text:
        return "persona_validation_failed", "empty_prose"
    if _THINKING_PREAMBLE.search(text):
        return "persona_validation_failed", "thinking_preamble"
    if _has_list_structure(text):
        return "persona_validation_failed", "list_structure"
    return None


def _normalize_grounding_span(value: str) -> str:
    """Collapse case and whitespace for substring checks."""
    collapsed = re.sub(r"\s+", " ", value.strip().casefold())
    return collapsed.strip(".,;:!?\"'()[]")


def _basis_grounded(basis: str, blocks_text: str) -> bool:
    """True when ``basis`` is attested in the concatenated blocks."""
    if not basis.strip():
        return False
    plain_blocks = _MARKUP_TAG.sub(" ", blocks_text)
    norm_basis = _normalize_grounding_span(basis)
    norm_blocks = _normalize_grounding_span(plain_blocks)
    return norm_basis in norm_blocks


def _invented_from_judgments(
    rows: list[PersonaJudgment], *, blocks_text: str
) -> list[str]:
    """Collect tokens the judge marked invented after deterministic basis checks."""
    invented: list[str] = []
    for row in rows:
        if row.kind == "new_entity_or_fact":
            invented.append(row.token)
        elif row.kind == "restates_attested" and not _basis_grounded(
            row.basis, blocks_text
        ):
            invented.append(row.token)
    return invented


def _strip_judge_response(raw: str) -> str:
    """Remove a leading thinking block before JSON parsing."""
    return _JUDGE_THINKING_STRIP.sub("", raw.strip(), count=1)


def _persona_judgments_payload(
    judgments: tuple[PersonaJudgment, ...] | None,
) -> list[dict[str, str]] | None:
    """Serialize judge rows for step and event payloads."""
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


def _candidate_payload(candidates: list[Candidate]) -> list[dict[str, Any]]:
    """Build the flagged-candidates JSON the judge prompt expects."""
    return [
        {
            "token": row.token,
            "form": row.form,
            "position": row.position,
            "contexts": row.contexts,
        }
        for row in candidates
    ]


async def validate_persona_prose(
    raw_text: str,
    *,
    brief_block: str,
    documents_block: str,
    architect_brief_block: str,
    judge_caller: JudgeCaller | None = None,
) -> PersonaValidationResult:
    """Validate persona prose: deterministic gates, then optional invented judge."""
    text = raw_text.strip()
    blocks_text = "\n".join((brief_block, documents_block, architect_brief_block))
    blocks_lower = blocks_text.lower()
    blocks_words = _blocks_word_set(blocks_lower)
    allowed = _persona_allowed_tokens(
        brief_block=brief_block,
        documents_block=documents_block,
        architect_brief_block=architect_brief_block,
    )
    failure = _persona_deterministic_failure(text)
    if failure:
        refused, detail = failure
        return PersonaValidationResult(text=text, ok=False, refused=refused, detail=detail)
    candidates = _persona_candidates(text, allowed=allowed, blocks_words=blocks_words)
    flagged = [row.token for row in candidates]
    if len(candidates) > _MAX_PERSONA_CANDIDATES:
        return PersonaValidationResult(
            text=text,
            ok=False,
            refused="persona_validation_failed",
            detail="too_many_candidates",
            flagged_candidates=flagged,
        )
    if not candidates:
        return PersonaValidationResult(text=text, ok=True)
    if judge_caller is None:
        return PersonaValidationResult(
            text=text,
            ok=False,
            refused="persona_judge_unavailable",
            flagged_candidates=flagged,
        )
    prose_fenced = _fence(text)
    try:
        raw_judge = await judge_caller(prose_fenced, candidates)
    except Exception:  # noqa: BLE001
        return PersonaValidationResult(
            text=text,
            ok=False,
            refused="persona_judge_unavailable",
            flagged_candidates=flagged,
            judge_called=True,
        )
    stripped = _strip_judge_response(
        raw_judge if isinstance(raw_judge, str) else str(raw_judge or "")
    )
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return PersonaValidationResult(
            text=text,
            ok=False,
            refused="persona_judge_invalid",
            detail="json_decode",
            flagged_candidates=flagged,
            judge_called=True,
        )
    rows = parse_persona_judgments(parsed, flagged)
    if rows is None:
        return PersonaValidationResult(
            text=text,
            ok=False,
            refused="persona_judge_invalid",
            detail="judge_coverage_mismatch",
            flagged_candidates=flagged,
            judge_called=True,
        )
    invented = _invented_from_judgments(rows, blocks_text=blocks_text)
    if invented:
        return PersonaValidationResult(
            text=text,
            ok=False,
            refused="persona_validation_failed",
            flagged_candidates=flagged,
            judged_invented=invented,
            judgments=tuple(rows),
            judge_called=True,
        )
    return PersonaValidationResult(
        text=text,
        ok=True,
        flagged_candidates=flagged,
        judged_invented=[],
        judgments=tuple(rows),
        judge_called=True,
    )


class WritingLocalPersonaAuthorHandler(BaseHandler):
    """Emit persona_prose for local drafters; invalid output fails the branch."""

    step_type = "writing_local_persona_author_v1"

    def __init__(
        self,
        caller: Any | None = None,
        judge_caller: JudgeCaller | None = None,
    ) -> None:
        """Optional callers inject model output for offline tests."""
        super().__init__()
        self._caller = caller
        self._judge_caller = judge_caller

    def _refuse(
        self,
        context: Any,
        reason: str,
        *,
        rejected_text: str | None = None,
        error: str | None = None,
        flagged_candidates: list[str] | None = None,
        judged_invented: list[str] | None = None,
        judgments: tuple[PersonaJudgment, ...] | None = None,
        detail: str | None = None,
        persona_attempts: list[dict[str, Any]] | None = None,
        publish_event: bool = True,
    ) -> StepOutput:
        """Build a fail-closed payload and publish an observation event when possible."""
        persona_judgments = _persona_judgments_payload(judgments)
        if publish_event:
            try:
                self._publish_bus_event(
                    context,
                    WritingPersonaRefused(
                        reason=reason,
                        flagged_candidates=flagged_candidates,
                        judged_invented=judged_invented,
                        persona_judgments=persona_judgments,
                        detail=detail,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "persona refuse bus publish failed reason=%s: %s",
                    reason,
                    exc,
                )
        payload: dict[str, Any] = {
            "ok": False,
            "refused": reason,
            "persona_refused": reason,
        }
        if rejected_text:
            payload["persona_rejected_text"] = rejected_text
        if error:
            payload["error"] = error
        if detail:
            payload["detail"] = detail
        if flagged_candidates is not None:
            payload["persona_flagged_candidates"] = flagged_candidates
        if judged_invented is not None:
            payload["persona_judged_invented"] = judged_invented
        if persona_judgments is not None:
            payload["persona_judgments"] = persona_judgments
        if persona_attempts is not None:
            payload["persona_attempts"] = persona_attempts
        return _step(payload)

    async def _invoke_persona_judge(
        self,
        step: Any,
        context: Any,
        *,
        persona_prose_fenced: str,
        candidates: list[Candidate],
        brief_block: str,
        documents_block: str,
        architect_brief_block: str,
    ) -> str:
        """Run the invented-token judge via the persona_judge model alias."""
        system, template = _persona_judge_template()
        if not template:
            raise RuntimeError("persona_judge_prompt_missing")
        tokens = [row.token for row in candidates]
        prompt = PromptBuilder().render(
            template,
            {
                "persona_prose": persona_prose_fenced,
                "brief": brief_block,
                "documents": documents_block,
                "architect_brief": architect_brief_block,
                "flagged_candidates": json.dumps(
                    _candidate_payload(candidates), ensure_ascii=False
                ),
                "candidate_count": str(len(candidates)),
            },
        )
        models = load_writing_models()
        model_id = str(
            models.get("persona_judge") or models.get("local_persona_author") or ""
        )
        if not model_id:
            raise RuntimeError("persona_model_missing")
        max_tokens = 64 + 220 * len(candidates)
        result = await self._call_model(
            model_id,
            prompt,
            step,
            context,
            system_prompt=system,
            temperature=0.0,
            max_tokens=max_tokens,
            json_schema=persona_judge_schema(tokens),
            call_label="persona_judge",
        )
        content = result.content
        return content if isinstance(content, str) else str(content or "")

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Run persona authoring; any failure returns ok false with persona_refused."""
        try:
            return await self._execute_impl(step, context)
        except Exception as exc:  # noqa: BLE001
            return self._refuse(
                context, "persona_author_failed", error=type(exc).__name__
            )

    async def _execute_impl(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        assemble = outputs.get("assemble")
        assemble_json = (
            assemble.get("json")
            if isinstance(assemble, dict)
            else getattr(assemble, "json", None)
        )
        if not isinstance(assemble_json, dict) or assemble_json.get("refused"):
            return _step({"ok": False, "skipped": True, "_skipped": True})
        arch = outputs.get("prompt_architect")
        arch_json = (
            arch.get("json") if isinstance(arch, dict) else getattr(arch, "json", None)
        )
        architect_block = str((arch_json or {}).get("architect_brief_block") or "")
        brief_block = str(assemble_json.get("brief_block") or "")
        documents_block = str(assemble_json.get("documents_block") or "")
        models = load_writing_models()
        model_id = models.get("local_persona_author") or ""
        if not model_id:
            return self._refuse(context, "persona_model_missing")
        options = getattr(context, "options", {}) or {}

        async def _bound_judge(persona_fenced: str, candidates: list[Candidate]) -> str:
            if self._judge_caller is not None:
                return await self._judge_caller(persona_fenced, candidates)
            return await self._invoke_persona_judge(
                step,
                context,
                persona_prose_fenced=persona_fenced,
                candidates=candidates,
                brief_block=brief_block,
                documents_block=documents_block,
                architect_brief_block=architect_block,
            )

        async def _author_caller(prompt: str) -> Any:
            if self._caller is not None:
                return await self._caller(prompt)
            return await self._call_model(
                model_id,
                prompt,
                step,
                context,
                temperature=0.2,
            )

        verdict, persona_attempts = await run_persona_attempts(
            options=options,
            brief_block=brief_block,
            documents_block=documents_block,
            architect_brief_block=architect_block,
            author_caller=_author_caller,
            judge_caller=_bound_judge,
            validate_persona_prose=validate_persona_prose,
        )

        if not verdict.ok:
            refused = verdict.refused or "persona_validation_failed"
            if refused == "persona_prompt_missing":
                return self._refuse(
                    context,
                    refused,
                    persona_attempts=persona_attempts,
                )
            if refused == "persona_author_failed":
                err = None
                if persona_attempts:
                    err = persona_attempts[-1].get("error")
                return self._refuse(
                    context,
                    refused,
                    error=str(err) if err else "author_caller_failed",
                    persona_attempts=persona_attempts,
                )
            return self._refuse(
                context,
                refused,
                rejected_text=verdict.text or None,
                flagged_candidates=verdict.flagged_candidates,
                judged_invented=verdict.judged_invented,
                judgments=verdict.judgments,
                detail=verdict.detail,
                persona_attempts=persona_attempts,
            )
        prose = verdict.text
        return _step(
            {
                "ok": True,
                "persona_prose": prose,
                "persona_prose_block": f"<persona_prose>\n{_fence(prose)}\n</persona_prose>",
                "persona_attempts": persona_attempts,
            }
        )
