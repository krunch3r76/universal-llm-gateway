"""Soft-limit auto-spill: oversized turn bodies → cortex sidecar + pointer.

Store-HTTP shared helper for POST /turns, /threads/with-turn, and
/threads/send (no caller ``sidecar_content``). Soft limit is context hygiene
(8k), not a hard transport cap; ``allow_long_body`` keeps the 64k hard 413 lane.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from cortex_store.dispatch_ops._thread_sidecar import (
    SidecarContentTooLargeError,
    SidecarWriteError,
    append_sidecar_pointer_line,
    write_thread_sidecar_for_send,
)

from .body_briefing_advisory import BriefingAdvisory, briefing_advisory
from .checkpoint_charter_lint import orchestration_charter_advisory
from .checkpoint_projection import CheckpointBodyTooLargeError
from .turns_models import (
    MAX_LONG_TURN_BODY_CHARS,
    MAX_SIDECAR_CONTENT_CHARS,
    MAX_TURN_BODY_CHARS,
    body_too_large_envelope,
    sidecar_content_too_large_envelope,
    sidecar_write_failed_envelope,
)

if TYPE_CHECKING:
    from .turns_models import TurnCreated

AUTO_OVERFLOW_SLUG = "auto-overflow"
AUTO_OVERFLOW_BRIEFING = (
    "Body auto-spilled over soft inline limit ({body_chars} chars). "
    "Full content in sidecar."
)


@dataclass(frozen=True)
class PreparedBody:
    """Body ready for turn insert after soft-spill / limit checking.

    ``sidecar_uri`` / ``sidecar_sha256`` are set only when auto-spill wrote a
    sidecar. ``sidecar_sha256`` matches ``write_thread_sidecar_for_send`` —
    SHA-256 of the spill **content** bytes (not the on-disk frontmatter wrapper).
    """

    body: str
    sidecar_uri: str | None = None
    sidecar_sha256: str | None = None
    advisory: BriefingAdvisory | None = None


class BodyTooLargeError(Exception):
    """Hard body-limit exceeded on the allow_long lane (413 ``body_too_large``)."""

    def __init__(self, envelope: dict[str, object]) -> None:
        self.envelope = envelope
        super().__init__(envelope.get("message", "body_too_large"))


def prepare_body_for_insert(
    *,
    thread: str,
    subject: str,
    body: str,
    from_agent: str,
    allow_long_body: bool = False,
    thread_tags: list[str] | None = None,
    supersedes_turn: int | None = None,
) -> PreparedBody:
    """Prepare a turn body for insert — soft-spill when over the 8k soft limit.

    Returns the body unchanged when under the effective limit (8k soft, or 64k
    when ``allow_long_body``). Soft-lane overflows write an oversized sidecar via
    ``write_thread_sidecar_for_send`` and return a short briefing + ``Sidecar:``
    pointer. Propagates ``SidecarWriteError`` / ``SidecarContentTooLargeError``;
    raises ``BodyTooLargeError`` for the allow_long hard 64k cap.
    """
    limit = MAX_LONG_TURN_BODY_CHARS if allow_long_body else MAX_TURN_BODY_CHARS
    if len(body) <= limit:
        advisory = orchestration_charter_advisory(
            body=body,
            subject=subject,
            thread_tags=thread_tags,
            supersedes_turn=supersedes_turn,
        )
        if advisory is None:
            advisory = briefing_advisory(
                body=body,
                subject=subject,
                allow_long_body=allow_long_body,
                has_sidecar=False,
                thread_tags=thread_tags,
                supersedes_turn=supersedes_turn,
            )
        return PreparedBody(body=body, advisory=advisory)

    if allow_long_body:
        raise BodyTooLargeError(
            body_too_large_envelope(limit=limit, body_chars=len(body))
        )

    if len(body) > MAX_SIDECAR_CONTENT_CHARS:
        raise SidecarContentTooLargeError(body_chars=len(body))

    sidecar = write_thread_sidecar_for_send(
        thread=thread,
        subject=subject,
        content=body,
        from_agent=from_agent,
        sidecar_slug=AUTO_OVERFLOW_SLUG,
        oversized=True,
    )
    briefing = AUTO_OVERFLOW_BRIEFING.format(body_chars=len(body))
    stored = append_sidecar_pointer_line(briefing, sidecar_uri=sidecar.uri)
    return PreparedBody(
        body=stored,
        sidecar_uri=sidecar.uri,
        sidecar_sha256=sidecar.sha256,
    )


def spill_error_http(
    exc: BaseException,
    *,
    thread_id: str | None = None,
) -> tuple[int, dict[str, object]] | None:
    """Map soft-spill / body-limit exceptions to ``(status_code, detail)``.

    Returns ``None`` when ``exc`` is not a known spill/limit error so callers
    can re-raise unrelated failures unchanged.
    """
    if isinstance(exc, BodyTooLargeError):
        return 413, exc.envelope
    if isinstance(exc, CheckpointBodyTooLargeError):
        return 413, exc.envelope
    if isinstance(exc, SidecarContentTooLargeError):
        return 413, sidecar_content_too_large_envelope(body_chars=exc.body_chars)
    if isinstance(exc, SidecarWriteError):
        return 503, sidecar_write_failed_envelope(
            thread_id=thread_id,
            error=str(exc),
        )
    return None


def build_turn_created(
    prepared: PreparedBody,
    *,
    turn_id: int,
    thread: str,
    turn_number: int,
    created_at: datetime,
    from_agent: str,
    to_agent: str,
    subject: str,
    superseded_turn_number: int | None = None,
    superseded_turn_id: int | None = None,
) -> TurnCreated:
    """Build TurnCreated and emit briefing advisory observation when applicable."""
    from .events.advisory_fired import emit_advisory_fired
    from .events.turn_body_advisory import emit_turn_body_over_briefing
    from .turns_models import TurnCreated

    advisory_dict: dict[str, object] | None = None
    if prepared.advisory is not None:
        emit_turn_body_over_briefing(
            thread=thread,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
            body_chars=prepared.advisory.body_chars,
            target_chars=prepared.advisory.target_chars,
        )
        emit_advisory_fired(
            advisory=prepared.advisory.reason,
            turn_kind=prepared.advisory.turn_kind,
            chars=prepared.advisory.body_chars,
            suppressed_by_profile=prepared.advisory.suppressed_by_profile,
            thread=thread,
            subject=subject,
        )
        advisory_dict = asdict(prepared.advisory)
    return TurnCreated(
        id=turn_id,
        thread=thread,
        turn_number=turn_number,
        created_at=created_at,
        sidecar_uri=prepared.sidecar_uri,
        sidecar_sha256=prepared.sidecar_sha256,
        briefing_advisory=advisory_dict,
        superseded_turn_number=superseded_turn_number,
        superseded_turn_id=superseded_turn_id,
    )


__all__ = [
    "AUTO_OVERFLOW_BRIEFING",
    "AUTO_OVERFLOW_SLUG",
    "BriefingAdvisory",
    "BodyTooLargeError",
    "PreparedBody",
    "build_turn_created",
    "SidecarContentTooLargeError",
    "SidecarWriteError",
    "prepare_body_for_insert",
    "spill_error_http",
]
