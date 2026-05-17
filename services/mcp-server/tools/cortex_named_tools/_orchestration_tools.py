"""Boot and session-close MCP tool registrations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ._boot_diff import _build_boot_diff
from ._boot_runner import BootMode, run_cortex_boot

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_KNOWN_FAMILIES = frozenset({"claude", "gpt", "grok", "gemini"})


def _parse_seat_slug(slug: str) -> tuple[str | None, str | None]:
    """Parse a '{family}-{platform}' slug into (family, platform).

    Returns (None, None) when the slug doesn't match a known family prefix.
    Used only by boot_inspect's diff_with parameter.
    """
    if not slug:
        return None, None
    parts = slug.split("-", 1)
    if len(parts) == 2 and parts[0] in _KNOWN_FAMILIES:
        return parts[0], parts[1]
    return None, None


def register_orchestration_tools(mcp: FastMCP) -> None:
    """Register boot and session-close tools on *mcp*."""

    @mcp.tool(title="Cortex Boot")
    def cortex_boot(
        family: str | None = None,
        platform: str | None = None,
        role: str | None = None,
        transcript_id: str = "",
    ) -> dict[str, Any]:
        """Slim boot briefing for session start. Returns a compact briefing card
        (~5-10KB) with priority signals and a section manifest for on-demand pulls.

        The briefing card contains: deadlines, unread bus summary, review queue
        count, last session summary, top todos, self-observations, and temporal
        alerts. The Last Session block renders the session summary; handoff
        prose is NOT auto-surfaced (handoffs are user-facing artifacts for
        manual copy-paste at end of chat, not boot orientation material — see
        assertion 8384, session web-2026-05-04-1057). The section manifest
        includes a `continuity` entry pointing at `GET /boot-continuity via
        cortex-api` for explicit fetches when an agent wants the prior
        handoff.
        Heavy data (full sessions, assertions, gated entities, file
        contents) is NOT inlined — pull on demand via manifest hints.

        Args:
          family        — model family: claude / gpt / grok / gemini (default: claude)
          platform      — platform surface: cursor / api / web (default: cursor)
          role          — optional functional team seat: lead / reviewer / gatherer /
                          synthesizer / artisan / skeptic / investigator.
                          Role does NOT change the seat slug — it annotates the session
                          and scopes the role memory anchor. The session_id is always
                          {family}-{platform}-YYYY-MM-DD-HHMM regardless of role.
          transcript_id — if provided, loads continuation context for that transcript.
                          The transcript entity must already exist in Cortex, which means
                          the session it references must have already closed. When a
                          dispatcher passes its own *in-progress* session ID (a
                          forward-reference), the entity does not yet exist — boot
                          proceeds without continuation context and surfaces a
                          ``transcript_id_note`` in the response. This is expected
                          behavior, not a failure.

        Key response fields:
          session_id             — server-minted ID; hold for entire session
          briefing_card          — compact Markdown briefing (~3-5KB)
          sections_available     — manifest of deeper-pull sections with fetch hints
          operational_context_ref — path to operational context file (read on demand)
        """
        return run_cortex_boot(
            family=family,
            platform=platform,
            role=role,
            transcript_id=transcript_id,
        )

    @mcp.tool(title="Boot Inspect")
    def boot_inspect(
        family: str | None = None,
        platform: str | None = None,
        role: str | None = None,
        transcript_id: str = "",
        diff_with: str = "",
    ) -> dict[str, Any]:
        """Read-only inspection of the boot surface without boot side effects.

        Runs the same fetch/render graph as `cortex_boot`, but in INSPECT mode:
        no operational-context file write, no audit dump write, and no
        `mcp.cortex.boot*` event emissions. Use this for audit/review and
        profile diffs without mutating boot state.

        Args:
          family        — model family: claude / gpt / grok / gemini
          platform      — platform surface: cursor / api / web
          role          — optional functional team seat
          transcript_id — optional continuation transcript context for primary
          diff_with     — optional secondary seat slug ({family}-{platform}, e.g.
                          "claude-web", "grok-api") to diff against primary
        """
        primary = run_cortex_boot(
            family=family,
            platform=platform,
            role=role,
            transcript_id=transcript_id,
            mode=BootMode.INSPECT,
        )
        if not diff_with:
            return primary

        diff_family, diff_platform = _parse_seat_slug(diff_with)
        secondary = run_cortex_boot(
            family=diff_family,
            platform=diff_platform,
            transcript_id="",
            mode=BootMode.INSPECT,
        )
        return {
            "primary": primary,
            "secondary": secondary,
            "diff": _build_boot_diff(primary, secondary),
        }

    @mcp.tool(title="Session Close (Reminder)")
    def session_close(
        agent: str = "cursor",
        session_id: str = "",
    ) -> dict[str, Any]:
        """HARD-DEPRECATED — use cortex(tool="session_close", ...) for atomic closes.

        This tool only returned step-by-step instructions without performing
        the close. The atomic version (cortex dispatch to
        ``/session-journals/close``) reads the Cursor agent-transcripts
        JSONL, assembles the verbatim layer server-side, validates
        structure, writes the file, and creates entity + journal row +
        continues edge in a **single validated transaction**.

        The 2059 hallucination (agent reported success despite failed
        writes during restart) was caused by following the old path. This
        tool now fails loudly.

        Use:
        cortex(tool="session_close", arguments={
          "session_id": "cursor-YYYY-MM-DD-HHMM",
          "agent": "cursor",
          "transcript_jsonl_path": "<path under CURSOR_AGENT_TRANSCRIPTS_ROOT>",
          "session_summary_md": "## Session Summary\\n\\n**Decisions:** ...",
          "summary": "<summary >=20 chars>",
          ...
        })

        Args:
          agent, session_id — ignored (fails with directive)
        """
        return {
            "error": "deprecated_session_close_reminder",
            "use": (
                "cortex(tool='session_close', arguments={"
                "'session_id': '...', 'agent': 'cursor', "
                "'transcript_jsonl_path': '<path>', "
                "'session_summary_md': '## Session Summary\\n...', "
                "'summary': '<summary>=20 chars', 'domains': [...], ...}) — "
                "atomic path that derives the verbatim layer server-side and "
                "writes file + DB tx with validation. See agent-bus thread 824 "
                "and libs/cortex_store/dispatch_ops/ops_journals.py."
            ),
            "detail": (
                "The reminder path enabled hallucinated closes; atomic path "
                "prevents it. Pre-Phase-2 transcript_md argument is gone — "
                "supply transcript_jsonl_path instead."
            ),
        }
