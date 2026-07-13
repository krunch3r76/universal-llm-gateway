"""Boot and session-close MCP tool registrations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_seat.profiles import resolve_seat
from agent_seat.registry import (
    normalize_agent_slug,
    normalize_bus_address,
    resolve_capability_cell_from_bus_address,
)

from ._boot_diff import build_boot_diff
from ._boot_runner import BootMode, run_cortex_brief

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _parse_seat_slug(slug: str) -> tuple[str | None, str | None]:
    """Parse a seat slug or bus address into (family, platform).

    Accepts new endpoint addresses (``web-anthropic``, ``cursor``) and legacy
    ``{family}-{platform}`` capability cells.
    """
    if not slug:
        return None, None
    resolved = resolve_capability_cell_from_bus_address(slug)
    if resolved is not None:
        return resolved
    parts = slug.split("-", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, None


def _resolve_boot_family_platform(
    *,
    agent: str | None = None,
    family: str | None = None,
    platform: str | None = None,
) -> tuple[str, str]:
    """Map boot call axes to canonical (family, platform).

    ``agent`` is the primary seat slug or bus address (``web-anthropic``,
    ``cursor``, legacy ``claude-web``). Explicit ``family`` / ``platform`` apply
    only when ``agent`` is absent or does not resolve to a capability cell.
    """
    if agent:
        bus_addr = normalize_bus_address(agent)
        resolved = resolve_capability_cell_from_bus_address(bus_addr)
        if resolved is not None:
            return resolved
        slug = normalize_agent_slug(agent)
        parsed = _parse_seat_slug(slug)
        if parsed[0] is not None:
            return parsed
    fam = family.lower() if family else None
    return resolve_seat(family=fam, platform=platform)


def register_orchestration_tools(mcp: FastMCP) -> None:
    """Register boot and session-close tools on *mcp*."""

    @mcp.tool(title="Cortex Brief")
    def cortex_brief(
        agent: str | None = None,
        family: str | None = None,
        platform: str | None = None,
        role: str | None = None,
        transcript_id: str = "",
        views: list[str] | None = None,
        principal: str | None = None,
        profile: str | None = None,
        packet_text: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Mints the session id and returns the briefing card + section manifest.

        Session-opening primary for any seat. The briefing card is a compact Markdown
        summary (soft target ≤ ~8KB inline) with priority signals and a section
        manifest for on-demand pulls — available for you to call directly when you
        need continuity context.

        The briefing card contains: deadlines, unread bus summary, review queue
        count, last session summary, top todos, self-observations, temporal
        alerts, and an agent-skills **index** (slug + trigger + fs md_read hint —
        full SKILL.md bodies are not inlined). The Last Session block renders the
        session summary; handoff prose is NOT auto-surfaced (handoffs are user-facing artifacts for
        manual copy-paste at end of chat, not orientation material — see
        assertion 8384, session web-2026-05-04-1057). The section manifest
        includes a `continuity` entry pointing at `GET /boot-continuity via
        cortex-api` for explicit fetches when an agent wants the prior
        handoff.
        Heavy data (full sessions, assertions, gated entities, file
        contents) is NOT inlined — pull on demand via manifest hints.

        Args:
          agent         — seat slug (primary): cursor, claude-web, grok-cursor, etc.
                          Legacy aliases (cursor → claude-cursor) normalize via
                          agent_seat.registry. When set, overrides family/platform
                          unless the slug does not parse as {family}-{platform}.
          family        — model family: claude / gpt / grok / gemini (default: claude)
          platform      — platform surface: cursor / api / web (default: cursor)
          role          — accepted for back-compat; no longer scopes briefing output. Output
                          is seat-predicated: duties are properties of the seat
                          (family+platform cell), not assignments to the reader.
                          Session IDs mint as ``{bus-address}-YYYY-MM-DD-HHMMSS-{3hex}``
                          where bus-address ∈ ``cursor | web-{provider} | api-{provider}``;
                          capability resolution remains seat-cell-keyed.
          transcript_id — if provided, loads continuation context for that transcript.
                          The transcript entity must already exist in Cortex, which means
                          the session it references must have already closed. When a
                          dispatcher passes its own *in-progress* session ID (a
                          forward-reference), the entity does not yet exist — the call
                          proceeds without continuation context and surfaces a
                          ``transcript_id_note`` in the response. This is expected
                          behavior, not a failure.
          views         — optional list of entity IDs to materialize as subgraph views.
                          Each entry is fetched via render_subgraph(root=<entity_id>, hops=1)
                          and surfaced in the briefing card as structural counts (entities,
                          edges) plus a retrieval hint. No prose is inlined (§C.3). The
                          section_manifest includes a render_subgraph entry per view for
                          on-demand full materialization (§C.4).
          principal     — optional principal entity_id (e.g. person:kaywan-mansubi).
                          Projects curated fields 1+2 at the card head via
                          GET /boot-principal-context. Field 1 (durable_identity) renders
                          only when attributes.durable_identity is set on the entity;
                          field 2 is legal_matter:* temporally active rows only (F3 allowlist).
          profile       — optional inject profile; ``"dispatch"`` enables dispatch-packet
                          scoped injection. ``views=["dispatch"]`` is a backward-compat alias.
          packet_text   — optional packet text for ``<invariants>`` skill parsing when
                          ``profile="dispatch"``.
          domain        — optional axis: ``coding`` | ``life`` | ``mixed-minimal``
                          (default when omitted). Soft-reorders card STATE.

        Key response fields:
          session_id             — server-minted ID; hold for entire session
          briefing_card          — compact Markdown briefing (~3-5KB)
          sections_available     — manifest of deeper-pull sections with fetch hints
          operational_context_ref — path to operational context file (read on demand)
        """
        boot_family, boot_platform = _resolve_boot_family_platform(
            agent=agent, family=family, platform=platform
        )
        return run_cortex_brief(
            family=boot_family,
            platform=boot_platform,
            role=role,
            transcript_id=transcript_id,
            views=views,
            principal=principal,
            profile=profile,
            packet_text=packet_text,
            domain=domain,
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
        principal: str | None = None,
    ) -> dict[str, Any]:
        """Read-only inspection of the briefing surface without side effects.

        Runs the same fetch/render graph as `cortex_brief`, but in INSPECT mode:
        no operational-context file write, no audit dump write, and no
        `mcp.cortex.boot*` event emissions. Use this for audit/review and
        profile diffs without mutating briefing state.

        Args:
          agent         — seat slug (same semantics as cortex_brief)
          family        — model family: claude / gpt / grok / gemini
          platform      — platform surface: cursor / api / web
          role          — accepted for back-compat; no longer scopes briefing output. Output
                          is seat-predicated: duties are properties of the seat
                          (family+platform cell), not assignments to the reader.
          transcript_id — optional continuation transcript context for primary
          diff_with     — optional secondary seat slug ({family}-{platform}, e.g.
                          "claude-web", "grok-api") to diff against primary
        """
        boot_family, boot_platform = _resolve_boot_family_platform(
            agent=agent, family=family, platform=platform
        )
        primary = run_cortex_brief(
            family=boot_family,
            platform=boot_platform,
            role=role,
            transcript_id=transcript_id,
            mode=BootMode.INSPECT,
            views=views,
            principal=principal,
        )
        if not diff_with:
            return primary

        diff_family, diff_platform = _parse_seat_slug(diff_with)
        secondary = run_cortex_brief(
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
          "session_id": "cursor-YYYY-MM-DD-HHMMSS-abc",
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
