from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .models import AgentName

_DEFAULT_ACTIVE_SINCE_DAYS = 14
_ACTIVE_SINCE_DAY_RE = re.compile(r"^(\d+)d$", re.IGNORECASE)


def parse_active_since(
    value: str | datetime | None,
    *,
    default_days: int = _DEFAULT_ACTIVE_SINCE_DAYS,
) -> datetime:
    """Normalize ``active_since`` query input to a UTC cutoff datetime.

    Accepts ISO8601 UTC strings or ``<int>d`` shorthand (e.g. ``14d``).
    ``None`` ⇒ now − ``default_days``.
    """
    if value is None:
        return datetime.now(UTC) - timedelta(days=default_days)
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    raw = value.strip()
    day_match = _ACTIVE_SINCE_DAY_RE.match(raw)
    if day_match:
        days = int(day_match.group(1))
        return datetime.now(UTC) - timedelta(days=days)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def active_since_window_label(value: str | datetime | None) -> str:
    """Human label for the active_since window (boot digest)."""
    if value is None:
        return f"{_DEFAULT_ACTIVE_SINCE_DAYS}d window"
    if isinstance(value, datetime):
        return value.isoformat()
    raw = value.strip()
    day_match = _ACTIVE_SINCE_DAY_RE.match(raw)
    if day_match:
        return f"{day_match.group(1)}d window"
    return raw

# Self-addressed guard removed — root cause fixed (fix-1, todo:agent-bus-from-attribution-root-cause).

# Free-form strings. The agent_bus docstring documents a `namespace:value`
# convention (e.g. `project:claudeburst`, `type:bug`, `agent:cursor`) but
# nothing is enforced here — tags are normalized in the DB layer
# (strip + lowercase + dedupe) via _normalize_tags before storage.

# Agent-bus convention: turns are short briefings with sidecar markdown
# references. Full documents belong in notes/system/threads/ and are
# referenced, not inlined. Team-dispatch uses the explicit long-body lane for
# pre-staged prompt context; that lane is still hard-capped.
MAX_TURN_BODY_CHARS = 8_000
MAX_LONG_TURN_BODY_CHARS = 64_000
MAX_SIDECAR_CONTENT_CHARS = 256 * 1024
# Soft advisory target for inline turn bodies. For CHECKPOINT subjects the
# advisory is exempt; when residue is metered elsewhere it counts authored
# residue chars only (derived projection + RESUME footer are exempt).
BRIEFING_TARGET_CHARS = 2_000


def sidecar_content_too_large_envelope(*, body_chars: int) -> dict[str, object]:
    """Structured 413 detail for oversized sidecar_content."""
    return {
        "code": "sidecar_content_too_large",
        "reason": "sidecar_content_too_large",
        "limit_chars": MAX_SIDECAR_CONTENT_CHARS,
        "body_chars": body_chars,
        "message": (
            f"sidecar_content exceeds {MAX_SIDECAR_CONTENT_CHARS:,} chars. "
            "Split the artifact or trim before retrying send."
        ),
        "retryable": True,
        "source": "agent_bus_store.send",
    }


def sidecar_write_failed_envelope(
    *,
    thread_id: str | None,
    error: str,
) -> dict[str, object]:
    """Structured error when the durable sidecar write fails before turn insert."""
    data: dict[str, object] = {"error": error}
    if thread_id is not None:
        data["thread_id"] = thread_id
    return {
        "code": "sidecar_write_failed",
        "reason": "sidecar_write_failed",
        "message": "Durable sidecar write failed; turn was not inserted.",
        "retryable": True,
        "source": "agent_bus_store.send",
        "data": data,
    }


def sidecar_content_limit_error(content: str) -> dict[str, object] | None:
    if len(content) <= MAX_SIDECAR_CONTENT_CHARS:
        return None
    return sidecar_content_too_large_envelope(body_chars=len(content))


def body_too_large_envelope(*, limit: int, body_chars: int) -> dict[str, object]:
    """Structured 413 detail for oversized turn bodies."""
    return {
        "reason": "body_too_large",
        "limit_chars": limit,
        "body_chars": body_chars,
        "suggestion": "sidecar_markdown_or_allow_long_body",
        "message": (
            f"Turn body exceeds {limit:,} chars. "
            "Agent-bus convention: short briefing + sidecar markdown reference. "
            "Write long content to notes/system/threads/<thread>-<subject>.md "
            "and reference it in a brief body. If inline long-form delivery is "
            "required for this recipient, retry with allow_long_body=true."
        ),
    }


def turn_body_limit_error(
    body: str, *, allow_long_body: bool = False
) -> dict[str, object] | None:
    """Return the structured limit error unless this request opts into long form."""
    limit = MAX_LONG_TURN_BODY_CHARS if allow_long_body else MAX_TURN_BODY_CHARS
    if len(body) <= limit:
        return None
    return body_too_large_envelope(
        limit=limit,
        body_chars=len(body),
    )


def post_continuation_misuse_error(
    slug: str, after_turn: int | None
) -> dict[str, object] | None:
    """Reject continuation-shaped misuse of the create-thread (`post`) path.

    `post` (POST /threads/with-turn) always mints a NEW thread. Two arg shapes
    signal the caller actually meant to continue an existing thread via `reply`
    (POST /turns); silently creating a forked thread is the 1140->1142 footgun.

      - slug all-digits -> a thread ID jammed into the slug field
      - after_turn > 0  -> a continuation field; inert on create (the unread
                           check in create_thread_with_turn runs against the
                           freshly created empty thread, so it never fires).
                           after_turn=0 is the skip-sentinel (_post_impl injects
                           it; same contract as reply's ``if after_turn > 0``).

    Returns a structured 400 envelope, or None for a legitimate new-thread post.
    """
    if slug.isdigit():
        return {
            "reason": "slug_looks_like_thread_id",
            "slug": slug,
            "message": (
                f"slug {slug!r} is all digits and looks like a thread ID. "
                "post always creates a NEW thread (id is auto-assigned; slug is "
                "a human label). To continue an existing thread use "
                "reply(thread=<id>, after_turn=<n>). To create a new thread, "
                "pick a descriptive non-numeric slug."
            ),
            "suggestion": "use_reply_to_continue",
        }
    if after_turn is not None and after_turn > 0:
        return {
            "reason": "after_turn_not_valid_on_post",
            "after_turn": after_turn,
            "message": (
                "after_turn is a continuation field and has no effect on post, "
                "which always creates a new thread. To continue an existing "
                "thread use reply(thread=<id>, after_turn=<n>)."
            ),
            "suggestion": "use_reply_to_continue",
        }
    return None


# --- Attachment schemas ---


class AttachmentCreate(BaseModel):
    """Metadata for a file attached to a turn."""

    filename: str
    path: str
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class Attachment(AttachmentCreate):
    """Stored attachment with database ID."""

    id: int


# --- Turn/Thread status enums ---


class TurnStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    WAITING = "waiting"


class ThreadStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    WAITING = "waiting"
    CLOSED = "closed"


# --- Turn schemas ---


class TurnCreate(BaseModel):
    model_config = {"populate_by_name": True}

    thread: str
    thread_slug: str | None = None
    from_agent: AgentName = Field(alias="from")
    to: AgentName
    subject: str
    body: str
    allow_long_body: bool = False
    status: TurnStatus = TurnStatus.OPEN
    after_turn: int | None = None
    supersedes_turn: int | None = None
    supersedes_turn_id: int | None = Field(
        default=None,
        description="Deprecated alias for row id — prefer supersedes_turn (turn_number).",
    )
    attachments: list[AttachmentCreate] | None = None


class TurnCreated(BaseModel):
    """API response for a newly inserted agent-bus turn row after create."""

    id: int
    thread: str
    turn_number: int
    created_at: datetime
    sidecar_uri: str | None = Field(
        default=None,
        description=(
            "Cortex URI when sidecar_content (E4) or soft-spill wrote a sidecar "
            "for this turn."
        ),
    )
    sidecar_sha256: str | None = Field(
        default=None,
        description=(
            "SHA-256 of sidecar content bytes (same domain as "
            "write_thread_sidecar_for_send); None when no sidecar was written."
        ),
    )
    briefing_advisory: dict[str, object] | None = Field(
        default=None,
        description=(
            "Non-blocking advisory when body exceeded BRIEFING_TARGET_CHARS without "
            "allow_long_body, a sidecar, or an inline-contract envelope."
        ),
    )
    superseded_turn_number: int | None = Field(
        default=None,
        description="When this turn structurally supersedes another, its turn_number.",
    )
    superseded_turn_id: int | None = Field(
        default=None,
        description="When this turn structurally supersedes another, its row id.",
    )


class Turn(BaseModel):
    model_config = {"populate_by_name": True, "serialize_by_alias": True}

    id: int
    thread: str
    turn_number: int
    from_agent: AgentName = Field(alias="from", serialization_alias="from")
    to: AgentName
    subject: str
    body: str | None = None
    status: TurnStatus
    supersedes_turn: int | None = None
    created_at: datetime
    read_at: datetime | None = None
    attachments: list[Attachment] | None = None


class TurnList(BaseModel):
    turns: list[Turn]


class UnreadThreadTocRow(BaseModel):
    """One thread's unread-digest entry for recipient-scoped fetch_unread."""

    thread: str
    unread_count: int
    latest_turn_number: int
    slug: str
    last_subject: str | None = None
    last_activity_at: datetime


class UnreadThreadToc(BaseModel):
    """Recipient-scoped unread inbox digest — windowed, enriched, bounded.

    Returned by GET /turns/unread-toc and by recipient-scoped fetch_unread
    (``to`` set, ``thread`` unset). Thread-scoped fetch_unread returns TurnList.
    ``total_*`` counts are unwindowed; ``threads`` respects active_since + limit.
    """

    threads: list[UnreadThreadTocRow]
    total_unread_threads: int
    total_unread_turns: int
    marked_read: int = 0
    truncated: bool = False
    active_since: str | None = None


class TurnReadStateBulk(BaseModel):
    """Bulk read-state patch — XOR turn_numbers vs through_turn."""

    turn_numbers: list[int] | None = None
    through_turn: int | None = None
    agent: str | None = None

    @field_validator("turn_numbers")
    @classmethod
    def _positive_turn_numbers(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("turn_numbers must be non-empty when provided")
        if any(n < 1 for n in value):
            raise ValueError("turn_numbers must be >= 1")
        return value


class TurnReadStateBulkResult(BaseModel):
    marked_read: int


class TurnUpdate(BaseModel):
    subject: str | None = None
    body: str | None = None
    append: str | None = None


class TurnStatusUpdate(BaseModel):
    status: TurnStatus
    supersedes_turn: int | None = None


# --- Thread schemas ---


class DispatchLinkSummary(BaseModel):
    """Single dispatch link attached to a lifecycle-managed thread."""

    execution_id: str
    pipeline_id: str
    linked_at: datetime
    terminal_status: str | None = None
    delivery_at: datetime | None = None


class DispatchLinkByExecution(BaseModel):
    """Dispatch link resolved by execution_id (cross-thread lookup)."""

    thread_id: str
    pipeline_id: str
    terminal_status: str | None = None
    terminal_at: datetime | None = None


class ThreadCreate(BaseModel):
    id: str | None = None
    slug: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    # Required to newly add reserved tag ``charter-runner`` (enrollment dual-key).
    enroll_charter_runner: bool = False
    # When set, opts this thread into lifecycle management from creation.
    # None = no lifecycle (backward-compat default).
    lifecycle_state: str | None = None


class ThreadDetail(BaseModel):
    id: str
    slug: str
    status: ThreadStatus
    summary: str | None = None
    turn_count: int
    unread_count: int
    last_subject: str | None = None
    last_turn_from: str | None = None
    last_turn_to: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    bus_lifecycle_state: str | None = None
    dispatch_links: list[DispatchLinkSummary] = Field(default_factory=list)


class ThreadSummaryResponse(BaseModel):
    id: str
    slug: str
    status: ThreadStatus
    summary: str | None = None
    turn_count: int
    unread_count: int
    recent_subjects: list[str]
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ThreadListResponse(BaseModel):
    threads: list[ThreadDetail]


class ThreadUpdate(BaseModel):
    status: ThreadStatus | None = None
    summary: str | None = None
    # None = leave tags unchanged. [] = clear all. [...] = replace with the new set.
    tags: list[str] | None = None
    # Additive ops — do not clobber unspecified tags (mutually exclusive with tags=).
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    # Required when ``tags`` newly adds reserved ``charter-runner``.
    enroll_charter_runner: bool = False


class TurnDelete(BaseModel):
    force: bool = False


class ThreadDelete(BaseModel):
    force: bool = False


class ThreadRename(BaseModel):
    new_id: str


class ThreadWithTurnCreate(BaseModel):
    """Atomic thread creation with first turn - no partial state possible."""

    model_config = {"populate_by_name": True}

    slug: str
    summary: str | None = None
    from_agent: AgentName = Field(alias="from")
    to: AgentName
    subject: str
    body: str
    allow_long_body: bool = False
    status: TurnStatus = TurnStatus.OPEN
    after_turn: int | None = None
    attachments: list[AttachmentCreate] | None = None
    tags: list[str] = Field(default_factory=list)
    enroll_charter_runner: bool = False
    lifecycle_state: str | None = None


class ThreadWithTurnCreated(BaseModel):
    """Response for atomic thread+turn creation."""

    thread: ThreadDetail
    turn: TurnCreated


class TurnSendCreate(BaseModel):
    """Unified send payload — POST /threads/send.

    Exactly one of new_slug (new-thread path) or thread (continue path) required.
    Both or neither → 400 send_xor_violation (validated in route, not here).

    When ``sidecar_content`` is set the server writes the durable cortex sidecar
    after the thread id is known and before the turn row is inserted.

    ``supersedes_turn`` is the same-thread **turn_number** to supersede structurally.
    Deprecated alias ``supersedes_turn_id`` (row id) accepted for one release cycle.
    """

    model_config = {"populate_by_name": True}

    new_slug: str | None = None
    thread: str | None = None
    from_agent: AgentName = Field(alias="from")
    to: AgentName
    subject: str
    body: str
    sidecar_content: str | None = None
    sidecar_slug: str | None = None
    allow_long_body: bool = False
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    # Dual-key for reserved enrollment tag on new_slug path (ignored on continue).
    enroll_charter_runner: bool = False
    lifecycle_state: str | None = None
    after_turn: int | None = None
    status: TurnStatus = TurnStatus.OPEN
    supersedes_turn: int | None = None
    supersedes_turn_id: int | None = Field(
        default=None,
        description="Deprecated alias for row id — prefer supersedes_turn (turn_number).",
    )
    mark_read: bool = False
    close: bool = False
    attachments: list[AttachmentCreate] | None = None


class TurnSendCreated(BaseModel):
    """Unified response for POST /threads/send."""

    send_path: Literal["new_thread", "continue"]
    thread: ThreadDetail
    turn: TurnCreated
    marked_read: int = 0
    sidecar_uri: str | None = None
    sidecar_sha256: str | None = None


class ThreadClose(BaseModel):
    """Atomic close: marks all turns read + sets status to closed."""

    summary: str | None = None
    mark_all_read: bool = True


class DispatchAdmit(BaseModel):
    """Payload for POST /threads/{id}/dispatch-admit."""

    execution_id: str
    pipeline_id: str
    caller_agent: str | None = None


class DispatchTerminate(BaseModel):
    """Payload for POST /threads/{id}/dispatch-terminate."""

    terminal_status: Literal["completed", "failed"]
    execution_id: str | None = None
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None


class DispatchClaimAndPost(BaseModel):
    """Payload for POST /threads/{id}/dispatch-claim-and-post."""

    execution_id: str
    pipeline_id: str
    caller_agent: str | None = None
    from_agent: str
    to_agent: str
    subject: str
    body: str


TRIAGE_THREAD_CAP = 50
TRIAGE_MARK_READ_FLOOR_HOURS = 24
TRIAGE_CLOSE_FLOOR_DAYS = 7
TRIAGE_CONFIRM_TTL_SECONDS = 600
_BROADCAST_TO_AGENTS = frozenset({"all", "team"})


def parse_older_than(value: str | datetime, *, anchor: datetime | None = None) -> datetime:
    """Normalize ``older_than`` to a UTC cutoff (threads at or before qualify)."""
    ref = anchor if anchor is not None else datetime.now(UTC)
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    raw = value.strip()
    if not raw:
        raise ValueError("older_than is required")
    day_match = _ACTIVE_SINCE_DAY_RE.match(raw)
    if day_match:
        days = int(day_match.group(1))
        return ref - timedelta(days=days)
    hour_match = re.match(r"^(\d+)h$", raw, re.IGNORECASE)
    if hour_match:
        hours = int(hour_match.group(1))
        return ref - timedelta(hours=hours)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def older_than_age(value: str | datetime) -> timedelta:
    """Wall-clock age implied by ``older_than`` (now − cutoff)."""
    now_ts = datetime.now(UTC)
    return now_ts - parse_older_than(value, anchor=now_ts)


def older_than_below_floor_envelope(
    *,
    action: str,
    older_than: str,
    floor_label: str,
) -> dict[str, object]:
    return {
        "code": "older_than_below_floor",
        "message": (
            f"triage action {action!r} requires older_than >= {floor_label}; "
            f"got {older_than!r}"
        ),
        "retryable": False,
        "source": "agent_bus_store.triage",
        "data": {"action": action, "older_than": older_than, "floor": floor_label},
    }


def triage_floor_error(action: str, older_than: str) -> dict[str, object] | None:
    """Return 422 envelope when ``older_than`` is below the action floor."""
    now_ts = datetime.now(UTC)
    cutoff = parse_older_than(older_than, anchor=now_ts)
    if action == "close":
        floor_cutoff = now_ts - timedelta(days=TRIAGE_CLOSE_FLOOR_DAYS)
        if cutoff > floor_cutoff:
            return older_than_below_floor_envelope(
                action=action,
                older_than=older_than,
                floor_label=f"{TRIAGE_CLOSE_FLOOR_DAYS}d",
            )
    if action == "mark_read":
        floor_cutoff = now_ts - timedelta(hours=TRIAGE_MARK_READ_FLOOR_HOURS)
        if cutoff > floor_cutoff:
            return older_than_below_floor_envelope(
                action=action,
                older_than=older_than,
                floor_label=f"{TRIAGE_MARK_READ_FLOOR_HOURS}h",
            )
    return None


class ThreadTriageRequest(BaseModel):
    """Bulk inbox hygiene — POST /threads/triage (agent_bus only)."""

    model_config = {"populate_by_name": True}

    from_agent: AgentName = Field(alias="from")
    older_than: str
    status: ThreadStatus | None = None
    action: Literal["mark_read", "close"] = "mark_read"
    dry_run: bool = True
    confirm_token: str | None = None


class ThreadTriageCandidate(BaseModel):
    id: str
    slug: str
    last_activity_at: datetime
    unread_count: int


class ThreadTriageDryRun(BaseModel):
    candidates: list[ThreadTriageCandidate]
    total_candidates: int
    capped: bool
    confirm_token: str
    expires_at: datetime


class ThreadTriageExecuted(BaseModel):
    action: Literal["mark_read", "close"]
    thread_count: int
    marked_read: int = 0
    closed: int = 0
    confirm_token_id: str
