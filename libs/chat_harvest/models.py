"""Site-neutral chat harvest models and URL classifier."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

Site = Literal["grok", "claude"]
TurnAuthor = Literal["user", "assistant"]
TurnSource = Literal["archive", "dom"]
IncludeTurns = Literal["none", "last", "range"]
HarvestOutcome = Literal[
    "harvested",
    "streaming",
    "no_conversation",
    "unauthenticated",
    "no_tab",
    "archive_conflict",
    "unreachable",
    "refused",
]
PasteGrant = Literal["explicit", "operator"]


class ChatTurn(BaseModel):
    author: TurnAuthor
    ordinal: int
    text: str
    source: TurnSource


class ChatHarvestRequest(BaseModel):
    url: str
    site: Site | None = None
    metadata_only: bool = False
    include_turns: IncludeTurns = "none"
    limit: int = Field(10, ge=1, le=50)
    after_turn: int | None = None
    supersede: bool = False
    cdp_url: str | None = None


class ChatHarvestResponse(BaseModel):
    outcome: HarvestOutcome
    site: str | None = None
    conversation_id: str | None = None
    url: str | None = None
    archive_uri: str | None = None
    archive_sha256: str | None = None
    harvested_at: str | None = None
    turn_count: int = 0
    last_ordinal: int | None = None
    streaming: bool = False
    truncated: bool = False
    turns: list[ChatTurn] = Field(default_factory=list)
    existing_sha256: str | None = None
    code: str | None = None
    reason: str | None = None


class ChatPasteRequest(BaseModel):
    url: str
    site: Site | None = None
    prompt_text: str | None = None
    prompt_uri: str | None = None
    prompt_path: str | None = None
    grant: PasteGrant
    cdp_url: str | None = None


class ChatPasteResponse(BaseModel):
    ok: bool
    site: str | None = None
    conversation_id: str | None = None
    url: str | None = None
    archive_uri: str | None = None
    archive_sha256: str | None = None
    send_verified: bool = False
    pasted_at: str | None = None
    code: str | None = None
    reason: str | None = None


class ClassifyOk(BaseModel):
    ok: Literal[True] = True
    site: Site
    conversation_id: str
    url: str


class ClassifyRefuse(BaseModel):
    ok: Literal[False] = False
    code: str
    reason: str


ClassifyResult = ClassifyOk | ClassifyRefuse

DEFAULT_RELAY_STATE_FILE = Path("/tmp/grok-claude-relay.state.json")
RELAY_LOCK_MAX_AGE_S = 120.0


def relay_lock_fresh(
    state_file: Path = DEFAULT_RELAY_STATE_FILE,
    *,
    max_age_s: float = RELAY_LOCK_MAX_AGE_S,
) -> bool:
    """True when the grok-claude relay lockfile was updated within *max_age_s*."""
    import json
    import time

    if not state_file.is_file():
        return False
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    updated_at = payload.get("updated_at")
    if updated_at is None:
        return False
    if isinstance(updated_at, (int, float)):
        age = time.time() - float(updated_at)
    else:
        from datetime import UTC, datetime

        try:
            parsed = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            age = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
        except ValueError:
            return False
    return 0 <= age < max_age_s


def project_turns_view(
    turns: list[ChatTurn],
    *,
    include_turns: IncludeTurns = "none",
    limit: int = 10,
    after_turn: int | None = None,
) -> tuple[list[ChatTurn], bool]:
    """Return a view slice of *turns* for inline response projection."""
    if include_turns == "none":
        return [], False
    pool = turns
    if after_turn is not None:
        pool = [t for t in turns if t.ordinal > after_turn]
    if include_turns == "last" and pool:
        pool = [pool[-1]]
    truncated = len(pool) > limit
    view = [
        ChatTurn(author=t.author, ordinal=t.ordinal, text=t.text, source="archive")
        for t in pool[:limit]
    ]
    return view, truncated


def build_harvest_response(
    *,
    site: str,
    live_id: str,
    live_url: str,
    turns: list[ChatTurn],
    streaming: bool,
    include_turns: IncludeTurns = "none",
    limit: int = 10,
    after_turn: int | None = None,
    harvested_at: str | None = None,
    archive_uri: str | None = None,
    archive_sha256: str | None = None,
) -> ChatHarvestResponse:
    view, truncated = project_turns_view(
        turns, include_turns=include_turns, limit=limit, after_turn=after_turn
    )
    return ChatHarvestResponse(
        outcome="streaming" if streaming else "harvested",
        site=site,
        conversation_id=live_id,
        url=live_url,
        archive_uri=archive_uri,
        archive_sha256=archive_sha256,
        harvested_at=harvested_at,
        turn_count=len(turns),
        last_ordinal=turns[-1].ordinal if turns else None,
        streaming=streaming,
        truncated=truncated,
        turns=view,
    )


def _normalize_host(host: str) -> str:
    host = (host or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def classify_chat_url(
    url: str,
    *,
    site: Site | None = None,
) -> ClassifyResult:
    """Classify a product-chat URL into site + conversation_id or refuse."""
    if not (url or "").strip():
        return ClassifyRefuse(code="url_required", reason="url is required")

    parsed = urlparse(url.strip())
    host = _normalize_host(parsed.hostname or "")
    path = parsed.path or "/"
    path_lower = path.lower()

    if host == "grok.com":
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "c" and parts[1]:
            inferred_site: Site = "grok"
            conversation_id = parts[1]
        elif path in ("", "/"):
            inferred_site = "grok"
            conversation_id = ""
        else:
            return ClassifyRefuse(
                code="unknown_grok_path",
                reason=f"unsupported grok.com path: {path!r}",
            )
    elif host == "claude.ai":
        if "/cowork/cse_" in path_lower:
            return ClassifyRefuse(
                code="use_cse_session",
                reason="Cowork CSE URLs must use cse_session, not chat_session",
            )
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "chat" and parts[1]:
            inferred_site = "claude"
            conversation_id = parts[1]
        elif parts == ["new"] or path_lower in ("/new", "/new/"):
            inferred_site = "claude"
            conversation_id = ""
        else:
            return ClassifyRefuse(
                code="unknown_claude_path",
                reason=f"unsupported claude.ai path: {path!r}",
            )
    else:
        return ClassifyRefuse(
            code="unsupported_site",
            reason=f"unsupported host: {host!r}",
        )

    if site is not None and site != inferred_site:
        return ClassifyRefuse(
            code="site_mismatch",
            reason=f"explicit site={site!r} does not match host ({inferred_site!r})",
        )

    return ClassifyOk(
        site=inferred_site, conversation_id=conversation_id, url=url.strip()
    )
