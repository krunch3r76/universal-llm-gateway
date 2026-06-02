"""Boot and session-close MCP tool registrations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_seat.profiles import resolve_seat
from agent_seat.registry import normalize_agent_slug

from ._boot_diff import build_boot_diff
from ._boot_runner import BootMode, run_cortex_boot

if TYPE_CHECKING:
    from fastmcp import FastMCP


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


def _resolve_boot_family_platform(
    *,
    agent: str | None = None,
    family: str | None = None,
    platform: str | None = None,
) -> tuple[str, str]:
    """Map boot call axes to canonical (family, platform).

    ``agent`` is the primary seat slug (e.g. ``cursor`` → ``claude-cursor``,
    ``grok-direct``). Explicit ``family`` / ``platform`` apply only when
    ``agent`` is absent or does not parse as ``{family}-{platform}``.
    """
    if agent:
        slug = normalize_agent_slug(agent)
        parsed_family, parsed_platform = _parse_seat_slug(slug)
        if parsed_family is not None:
            return parsed_family, parsed_platform
    fam = family.lower() if family else None
    return resolve_seat(family=fam, platform=platform)


def register_orchestration_tools(mcp: FastMCP) -> None:
    """Register boot and session-close tools on *mcp*."""

    @mcp.tool(title="Cortex Boot")
    def cortex_boot(
        agent: str | None = None,
        family: str | None = None,
        platform: str | None = None,
        role: str | None = None,
        transcript_id: str = "",
        views: list[str] | None = None,
    ) -> dict[str, Any]:
        """Slim boot briefing for session start. Returns a compact briefing card
        (~25-35KB typical) with priority signals and a section manifest for on-demand pulls.

        The briefing card contains: deadlines, unread bus summary, review queue
        count, last session summary, top todos, self-observations, temporal
        alerts, and an agent-skills **index** (slug + trigger + fs md_read hint —
        full SKILL.md bodies are not inlined). The Last Session block renders the
        session summary; handoff prose is NOT auto-surfaced (handoffs are user-facing artifacts for
        manual copy-paste at end of chat, not boot orientation material — see
        assertion 8384, session web-2026-05-04-1057). The section manifest
        includes a `continuity` entry pointing at `GET /boot-continuity via
        cortex-api` for explicit fetches when an agent wants the prior
        handoff.
        Heavy data (full sessions, assertions, gated entities, file
        contents) is NOT inlined — pull on demand via manifest hints.

        Args:
          agent         — seat slug (primary): cursor, claude-web, grok-direct, etc.
                          Legacy aliases (cursor → claude-cursor) normalize via
                          agent_seat.registry. When set, overrides family/platform
                          unless the slug does not parse as {family}-{platform}.
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
          views         — optional list of entity IDs to materialize as subgraph views.
                          Each entry is fetched via render_subgraph(root=<entity_id>, hops=1)
                          and surfaced in the briefing card as structural counts (entities,
                          edges) plus a retrieval hint. No prose is inlined (§C.3). The
                          section_manifest includes a render_subgraph entry per view for
                          on-demand full materialization (§C.4).

        Key response fields:
          session_id             — server-minted ID; hold for entire session
          briefing_card          — compact Markdown briefing (~3-5KB)
          sections_available     — manifest of deeper-pull sections with fetch hints
          operational_context_ref — path to operational context file (read on demand)
        """
        boot_family, boot_platform = _resolve_boot_family_platform(
            agent=agent, family=family, platform=platform
        )
        return run_cortex_boot(
            family=boot_family,
            platform=boot_platform,
            role=role,
            transcript_id=transcript_id,
            views=views,
        )

    @mcp.tool(title="Boot Inspect")
    def boot_inspect(
        agent: str | None = None,
        family: str | None = None,
        platform: str | None = None,
        role: str | None = None,
        transcript_id: str = "",
        diff_with: str = "",
        views: list[str] | None = None,
    ) -> dict[str, Any]:
        """Read-only inspection of the boot surface without boot side effects.

        Runs the same fetch/render graph as `cortex_boot`, but in INSPECT mode:
        no operational-context file write, no audit dump write, and no
        `mcp.cortex.boot*` event emissions. Use this for audit/review and
        profile diffs without mutating boot state.

        Args:
          agent         — seat slug (same semantics as cortex_boot)
          family        — model family: claude / gpt / grok / gemini
          platform      — platform surface: cursor / api / web
          role          — optional functional team seat
          transcript_id — optional continuation transcript context for primary
          diff_with     — optional secondary seat slug ({family}-{platform}, e.g.
                          "claude-web", "grok-api") to diff against primary
        """
        boot_family, boot_platform = _resolve_boot_family_platform(
            agent=agent, family=family, platform=platform
        )
        primary = run_cortex_boot(
            family=boot_family,
            platform=boot_platform,
            role=role,
            transcript_id=transcript_id,
            mode=BootMode.INSPECT,
            views=views,
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
            "diff": build_boot_diff(primary, secondary),
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
                "prevents it. Supply either transcript_jsonl_path for Cursor "
                "sessions or transcript_md for web sessions."
            ),
        }
