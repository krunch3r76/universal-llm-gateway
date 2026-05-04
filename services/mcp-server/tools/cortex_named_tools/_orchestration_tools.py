"""Boot and session-close MCP tool registrations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ._boot_diff import _build_boot_diff
from ._boot_runner import BootMode, run_cortex_boot

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_orchestration_tools(mcp: FastMCP) -> None:
    """Register boot and session-close tools on *mcp*."""

    @mcp.tool(title="Cortex Boot")
    def cortex_boot(
        agent: str = "cursor",
        transcript_id: str = "",
    ) -> dict[str, Any]:
        """Slim boot briefing for session start. Returns a compact briefing card
        (~5-10KB) with priority signals and a section manifest for on-demand pulls.

        The briefing card contains: deadlines, unread bus summary, review queue
        count, last session summary, top todos, self-observations, and temporal
        alerts. When the latest session has a captured handoff, the Last Session
        block renders a verbatim **Handoff** section; otherwise it falls back to
        the summary plus `_Hint: no_handoff_captured_`. The section manifest also
        includes a `continuity` entry pointing at `GET /boot-continuity via cortex-api`.
        Heavy data (full sessions, assertions, gated entities, file
        contents) is NOT inlined — pull on demand via manifest hints.

        Args:
          agent         — agent profile: cursor, web, api, api_claude, oppie, orion, subagent (default: "cursor")
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
        return run_cortex_boot(agent=agent, transcript_id=transcript_id)

    @mcp.tool(title="Boot Inspect")
    def boot_inspect(
        agent: str = "cursor",
        transcript_id: str = "",
        diff_with: str = "",
    ) -> dict[str, Any]:
        """Read-only inspection of the boot surface without boot side effects.

        Runs the same fetch/render graph as `cortex_boot`, but in INSPECT mode:
        no operational-context file write, no audit dump write, and no
        `mcp.cortex.boot*` event emissions. Use this for audit/review and
        profile diffs without mutating boot state.

        Args:
          agent         — primary agent profile to inspect
          transcript_id — optional continuation transcript context for primary
          diff_with     — optional secondary agent profile to diff against
        """
        primary = run_cortex_boot(
            agent=agent,
            transcript_id=transcript_id,
            mode=BootMode.INSPECT,
        )
        if not diff_with:
            return primary

        secondary = run_cortex_boot(
            agent=diff_with,
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
        the close. The atomic version (cortex dispatch to /session-journals/close)
        validates transcript content/length/structure, writes the file, and creates
        entity + journal row + continues edge in a **single validated transaction**.

        The 2059 hallucination (agent reported success despite failed writes during
        restart) was caused by following the old path. This tool now fails loudly.

        Use:
        cortex(tool="session_close", arguments={
          "session_id": "...",
          "agent": "cursor",
          "transcript_md": "# full transcript markdown ...",
          "summary": "summary >=20 chars",
          ...
        })

        Args:
          agent, session_id — ignored (fails with directive)
        """
        return {
            "error": "deprecated_session_close_reminder",
            "use": (
                "cortex(tool='session_close', arguments={"
                "'session_id': '...', 'agent': 'cursor', 'transcript_md': '<full md>', "
                "'summary': '<summary>=20 chars', 'domains': [...], ...}) — "
                "atomic path that writes file + DB tx with validation. See "
                "agent-bus thread 824 and libs/cortex_store/dispatch_ops/ops_journals.py"
            ),
            "detail": "The reminder path enabled hallucinated closes; atomic path prevents it.",
        }
