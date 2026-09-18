"""Site-neutral chat session harvest — classifier, archive writer, grok adapter.

CDP adapters (claude/grok) are lazy — edge GPU images lack playwright and still
import ``chat_harvest.chrome`` via pipeline/CDP model helpers.
"""

from __future__ import annotations

from typing import Any

from chat_harvest.archive import (
    Alignment,
    ArchiveConflictError,
    ArchiveRefusalError,
    align_transcripts,
    archive_chat_transcript,
    archive_dest,
    conv12,
    cortex_files_root,
    legacy_md_dest,
    legacy_md_rel_path,
    message_digest,
)
from chat_harvest.chrome import (
    RELAY_ENVELOPE_SUBJECT_RE,
    TOOL_BADGE_LINE_RE,
    is_chrome_only,
    is_failed_relay_envelope_subject,
    is_prompt_echo,
    is_relay_envelope_subject,
    strip_chrome,
    substantive_reply_body,
)
from chat_harvest.messages import (
    build_harvest_envelope,
    ensure_turn_index,
    load_harvest_envelope,
    message_index,
    turns_to_messages,
)
from chat_harvest.models import (
    DEFAULT_RELAY_STATE_FILE,
    ChatHarvestRequest,
    ChatHarvestResponse,
    ChatPasteRequest,
    ChatPasteResponse,
    ChatTurn,
    ClassifyOk,
    ClassifyRefuse,
    ClassifyResult,
    ConflictDetail,
    classify_chat_url,
    project_messages_view,
    relay_lock_fresh,
)

_LAZY_ADAPTERS = frozenset(
    {
        "execute_claude_harvest",
        "execute_claude_paste",
        "execute_grok_harvest",
        "execute_grok_paste",
        "harvest_full_transcript",
        "scroll_stabilize",
    }
)

__all__ = [
    "Alignment",
    "ArchiveConflictError",
    "ArchiveRefusalError",
    "ChatHarvestRequest",
    "ChatHarvestResponse",
    "ChatPasteRequest",
    "ChatPasteResponse",
    "ChatTurn",
    "ClassifyOk",
    "ClassifyRefuse",
    "ClassifyResult",
    "ConflictDetail",
    "DEFAULT_RELAY_STATE_FILE",
    "align_transcripts",
    "archive_chat_transcript",
    "archive_dest",
    "build_harvest_envelope",
    "classify_chat_url",
    "conv12",
    "ensure_turn_index",
    "is_chrome_only",
    "is_failed_relay_envelope_subject",
    "is_prompt_echo",
    "is_relay_envelope_subject",
    "RELAY_ENVELOPE_SUBJECT_RE",
    "strip_chrome",
    "substantive_reply_body",
    "TOOL_BADGE_LINE_RE",
    "cortex_files_root",
    "execute_claude_harvest",
    "execute_claude_paste",
    "execute_grok_harvest",
    "execute_grok_paste",
    "harvest_full_transcript",
    "legacy_md_dest",
    "legacy_md_rel_path",
    "load_harvest_envelope",
    "message_digest",
    "message_index",
    "project_messages_view",
    "relay_lock_fresh",
    "scroll_stabilize",
    "turns_to_messages",
]


def __getattr__(name: str) -> Any:
    if name not in _LAZY_ADAPTERS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in ("execute_claude_harvest", "execute_claude_paste"):
        from chat_harvest.claude_chat_adapter import (
            execute_claude_harvest,
            execute_claude_paste,
        )

        globals()["execute_claude_harvest"] = execute_claude_harvest
        globals()["execute_claude_paste"] = execute_claude_paste
        return globals()[name]
    from chat_harvest.grok_adapter import (
        execute_grok_harvest,
        execute_grok_paste,
        harvest_full_transcript,
        scroll_stabilize,
    )

    globals()["execute_grok_harvest"] = execute_grok_harvest
    globals()["execute_grok_paste"] = execute_grok_paste
    globals()["harvest_full_transcript"] = harvest_full_transcript
    globals()["scroll_stabilize"] = scroll_stabilize
    return globals()[name]
