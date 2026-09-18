"""Render the static WAKE doorbell prompt for claude.ai scheduled liaison tasks.

The paste is the doorbell (F2): addresses + episodic frame only; payload stays
on the bus/graph. Amendment A1 digest delivery; memos 10479#118 (plant an
address, never a dump) and #120 (direct ``Use the <slug> skill`` lines).

``DOORBELL_CAP`` is 1400 bytes: basis floor is ~1132 B once the status quintuple
is carried; the ``commission:`` line costs exactly 250 B (measured, including the
admission-gate tokens); 1400 leaves 221 B of ladder headroom. The binder's rule is "the basis is never shed to fit a
cap; the cap moves."
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bus_watch.doorbell_skills import (
    dispatch_skills_for_surface,
    doorbell_skills,
    liaison_protocol_sot_uri,
    seat_dispatch_surface,
    seat_doorbell_surface,
)

DOORBELL_CAP = 1400

SUCCESSOR_WAKE_CAP = 2048
_SUCCESSOR_ROW_TRUNC_MARKER = "..."

# Echo-body placeholders in ritual order; the paste stays byte-identical for
# equal arguments (F2 M5), so shedding removes fields, never reorders them.
# ``digest:`` carries the tip the wake is scored on and ``chat:`` is the CSE
# handle the seat reports back — only the other two are sheddable.
_DIGEST_PLACEHOLDER = "digest: <DIGEST subject> — attention <n>, checkpoint_due <bool>"
_ECHO_FIELDS = (
    _DIGEST_PLACEHOLDER,
    "objective: <root.last_subject>",
    "chat: <url>",
    "tools: <count>",
)
_SHEDDABLE_ECHO_FIELDS = ("tools: <count>", "objective: <root.last_subject>")
# The admission gate reads line-start tokens, so a commission whose scope lives in
# prose caps is refused ``empty_directive_scope``; a pinned model on the ask lane is
# refused ``ask_escalation_unsupported``. Both refusals cost a whole tick, so the
# doorbell names the tokens rather than leaving them to the seat's memory.
_COMMISSION_HINT = (
    "if attention mint; quiet echo; line-start `scope:` + `files_expected:` + "
    "`vision:` (prose caps are not tokens); ¬desired_model; "
)
_STALE_CONTRACT = "re-fetch DIGEST; on epoch mismatch echo STALE and stop"

_HEADER_RE = re.compile(
    r"^WAKE — liaison, house agent-bus:(?P<root>\S+) (?P<slug>.+)$",
)
_ECHO_RE = re.compile(r"agent-bus:(?P<ring>\d+) \(echo\)")


@dataclass(frozen=True)
class ParsedDoorbell:
    root: str
    slug: str
    ring: str | None


@dataclass(frozen=True)
class EnsureDoorbellResult:
    wrote: bool
    slug: str
    ring: str | None


__all__ = [
    "DOORBELL_CAP",
    "SUCCESSOR_WAKE_CAP",
    "EnsureDoorbellResult",
    "ParsedDoorbell",
    "basis_floor_bytes",
    "doorbell_prompt_uri",
    "ensure_doorbell_file",
    "parse_doorbell_file",
    "render_address",
    "render_doorbell",
    "render_successor_wake",
    "successor_wake_unshed_byte_length",
]


def doorbell_prompt_uri(root: str) -> str:
    """Cortex URI for the static liaison WAKE doorbell paste."""
    return f"cortex://notes/system/threads/{root}-liaison-wake-doorbell.md"


def parse_doorbell_file(text: str) -> ParsedDoorbell:
    """Recover ``root``, ``slug``, and ``ring`` from an on-disk doorbell paste."""
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty doorbell file")
    header = _HEADER_RE.match(lines[0])
    if not header:
        raise ValueError(f"unrecognized doorbell header: {lines[0]!r}")
    root = header.group("root")
    slug = header.group("slug")
    ring: str | None = None
    for line in lines:
        if not line.startswith("addresses:"):
            continue
        echo = _ECHO_RE.search(line)
        if echo is None:
            break
        echo_id = echo.group("ring")
        ring = echo_id if echo_id != root else None
        break
    return ParsedDoorbell(root=root, slug=slug, ring=ring)


def ensure_doorbell_file(
    path: Path,
    *,
    root: str,
    slug: str | None = None,
    slug_explicit: bool = False,
    ring: str | None = None,
    ring_explicit: bool = False,
    slug_default: str = "liaison-wake",
    seat: str = "web-anthropic",
    surface: str = "ide",
    include_commission: bool = True,
    **render_kw: object,
) -> EnsureDoorbellResult:
    """Write ``path`` when rendered bytes differ; preserve on-disk args by default.

    When the file already exists, ``slug`` and ``ring`` are recovered from its
    header and ``addresses:`` line unless the caller marked them explicit.
    Explicit overrides that change recovered values are printed to stderr.
    """
    if path.is_file():
        parsed = parse_doorbell_file(path.read_text(encoding="utf-8"))
        effective_slug = slug if slug_explicit else parsed.slug
        effective_ring = ring if ring_explicit else parsed.ring
        if slug_explicit and slug != parsed.slug:
            print(
                f"doorbell slug: {parsed.slug!r} -> {effective_slug!r}",
                file=sys.stderr,
            )
        if ring_explicit and ring != parsed.ring:
            old_ring = parsed.ring if parsed.ring is not None else parsed.root
            new_ring = effective_ring if effective_ring is not None else root
            print(
                f"doorbell ring: {old_ring!r} -> {new_ring!r}",
                file=sys.stderr,
            )
    else:
        effective_slug = slug if slug is not None else slug_default
        effective_ring = ring if ring_explicit else None

    rendered = render_doorbell(
        root,
        effective_slug,
        ring=effective_ring,
        seat=seat,
        surface=surface,
        include_commission=include_commission,
        **render_kw,  # type: ignore[arg-type]
    )
    rendered_bytes = rendered.encode("utf-8")
    if path.is_file() and path.read_bytes() == rendered_bytes:
        return EnsureDoorbellResult(
            wrote=False, slug=effective_slug, ring=effective_ring
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rendered_bytes)
    return EnsureDoorbellResult(wrote=True, slug=effective_slug, ring=effective_ring)


def render_address(uri: str) -> str:
    """Tool-call-shaped ``md_read`` address; ``#`` splits path from section."""
    if "#" in uri:
        path, section = uri.split("#", 1)
        return f"fs(op=md_read, path={path}, section={section})"
    return f"fs(op=md_read, path={uri})"


_render_address = render_address


def _digest_echo_quintuple(
    *,
    attention_row_ids: tuple[str, ...],
    as_of: str,
    digest_source: str,
    scope_root: str,
    scope_lanes: tuple[str, ...],
    fingerprint: str,
) -> str:
    ids = ",".join(attention_row_ids) if attention_row_ids else "(none)"
    lanes = ",".join(scope_lanes) if scope_lanes else ""
    scope = f"{scope_root}+{lanes}" if lanes else scope_root
    return (
        f"digest: value={ids} as_of={as_of} source={digest_source} "
        f"scope={scope} epoch={fingerprint} — {_STALE_CONTRACT}"
    )


def _quintuple_requested(
    *,
    attention_row_ids: tuple[str, ...] | None,
    as_of: str | None,
    digest_source: str | None,
    scope_lanes: tuple[str, ...] | None,
    fingerprint: str | None,
) -> bool:
    return all(
        v is not None
        for v in (attention_row_ids, as_of, digest_source, scope_lanes, fingerprint)
    )


def basis_floor_bytes(
    root: str,
    slug: str,
    *,
    attention_row_ids: tuple[str, ...] = (),
    as_of: str = "2026-09-14T07:00:00Z",
    digest_source: str = "a" * 16,
    scope_lanes: tuple[str, ...] = (),
    fingerprint: str = "b" * 16,
) -> int:
    """Byte length of a render with the full status quintuple and no sheddables."""
    text = render_doorbell(
        root,
        slug,
        ring=root,
        include_commission=False,
        attention_row_ids=attention_row_ids,
        as_of=as_of,
        digest_source=digest_source,
        scope_lanes=scope_lanes,
        fingerprint=fingerprint,
    )
    return len(text.encode("utf-8"))


def _seat_surface(seat: str) -> str:
    """Map a dispatch seat id to the ``doorbell_skills`` surface key."""
    return seat_doorbell_surface(seat)


def _seat_has_jupiter_shell(seat: str) -> bool:
    """True when the seat can arm ``watch-supervise.sh`` (Jupiter IDE shell)."""
    return str(seat or "").strip().lower() in ("cursor-sdk", "cursor")


def render_doorbell(
    root: str,
    slug: str,
    *,
    ring: str | None = None,
    seat: str = "web-anthropic",
    surface: str = "ide",
    skills: tuple[str, ...] | None = None,
    extra_addresses: tuple[str, ...] = (),
    fired_by: str | None = None,
    cap: int = DOORBELL_CAP,
    include_commission: bool = True,
    attention_row_ids: tuple[str, ...] | None = None,
    as_of: str | None = None,
    digest_source: str | None = None,
    scope_lanes: tuple[str, ...] | None = None,
    fingerprint: str | None = None,
) -> str:
    """Static doorbell text for ``root``; echoes go to ``ring`` (the root when None).

    Identical for equal arguments (F2 M5) and capped so extra ``md_read`` addresses
    cannot grow the paste into a dump. A planted address outranks a placeholder: over
    ``cap`` the renderer sheds the optional echo fields and then the whole
    ``commission:`` line before raising ``ValueError``, so seating a live wake with
    one extra address is a render, not a hand-paste. The commission guard is never
    shed while the line remains — either the line is complete or absent (10479#881).
    Addresses, ``Use the <slug> skill`` lines, and the frame are never shed — a
    doorbell without its address is not a doorbell (10479#118). The
    DIGEST fetch window is 10 turns, not 3: root housekeeping (admits, INFO, CP
    pointers) outran a 3-turn window by six turns on 2026-09-12 (agent-bus:10479#139).

    When ``skills`` is omitted, Use-line slugs derive from ``seat`` via
    ``seat_doorbell_surface`` (the ``surface`` kwarg is legacy-only). Life seats
    plant the liaison SOT as a resolvable ``md_read`` address under the cap.
    """
    effective_surface = _seat_surface(seat)
    skill_slugs = skills if skills is not None else doorbell_skills(effective_surface)
    echo = ring if ring else root
    address_parts = [
        f"agent_bus_read(fetch, thread={root}, last=10, compact=true)",
        f"agent-bus:{echo} (echo)",
    ]
    if skills is None and seat_dispatch_surface(seat) == "life":
        address_parts.append(render_address(liaison_protocol_sot_uri()))
    address_parts.extend(render_address(addr) for addr in extra_addresses)
    addresses = "; ".join(address_parts)
    # Default keeps the scheduled-task frame (R15). CDP/live seating passes fired_by
    # so the episodic frame stays true (10158 M2) instead of a false ritual label.
    fire = fired_by if fired_by is not None else f"scheduled task liaison-wake-{root}"
    quintuple = _quintuple_requested(
        attention_row_ids=attention_row_ids,
        as_of=as_of,
        digest_source=digest_source,
        scope_lanes=scope_lanes,
        fingerprint=fingerprint,
    )
    if quintuple:
        digest_line = _digest_echo_quintuple(
            attention_row_ids=attention_row_ids or (),
            as_of=str(as_of),
            digest_source=str(digest_source),
            scope_root=root,
            scope_lanes=scope_lanes or (),
            fingerprint=str(fingerprint),
        )
        echo_fields = [digest_line, "chat: <url>"]
    else:
        echo_fields = list(_ECHO_FIELDS)
    show_commission = include_commission

    def compose() -> str:
        body = "ORIENTED / " + " / ".join(echo_fields)
        lines = [
            f"WAKE — liaison, house agent-bus:{root} {slug}",
            "duty: read the latest DIGEST; act only on its attention; page only on designed stops.",
            "disclosure: orientation ritual; one echo before the first move.",
            f'objective: latest DIGEST {root} turn (subject starts "DIGEST {root}").',
            f"addresses: {addresses}",
        ]
        lines.extend(f"Use the {skill} skill." for skill in skill_slugs)
        lines.extend(
            [
                f"frame: fired by {fire}; seat {seat}; prior wake = last ORIENTED turn on agent-bus:{echo}.",
                f'echo: agent_bus(send, thread={echo}, subject="ORIENTED {root}", body="{body}")',
            ]
        )
        if show_commission:
            lines.append(
                f"commission: cursor_request(new_slug=r15-wake-<slug>, parent_thread={root}, lane_role=sub_mission, …) — {_COMMISSION_HINT}¬thread={root}."
            )
        return "\n".join(lines) + "\n"

    def shed() -> bool:
        """Drop the least load-bearing fragment; False when only the ritual is left."""
        nonlocal show_commission
        if quintuple:
            return False
        for field in _SHEDDABLE_ECHO_FIELDS:
            if field in echo_fields:
                echo_fields.remove(field)
                return True
        if show_commission:
            show_commission = False
            return True
        return False

    message = compose()
    while len(message.encode("utf-8")) > cap and shed():
        message = compose()
    encoded = message.encode("utf-8")
    if len(encoded) > cap:
        raise ValueError(f"doorbell exceeds {cap} bytes ({len(encoded)})")
    return message


def _successor_liaison_slug(seat: str = "cursor-sdk") -> str:
    return doorbell_skills(_seat_surface(seat))[0]


def _inline_successor_dispatch_skills(text: str, seat: str) -> str:
    """Prepend ``<skills_inline>`` bodies for CDP/CSE/life successors (not Use-lines)."""
    surface = seat_dispatch_surface(seat)
    if not surface:
        return text
    slugs = dispatch_skills_for_surface(surface)
    if not slugs:
        return text
    from claude_bundles import catalog as catalog_mod
    from claude_bundles import cowork_skill_delivery as delivery_mod
    from claude_bundles.catalog import load_skill_catalog
    from claude_bundles.cowork_skill_delivery import prepend_cdp_dispatch_skills

    relaxed = lambda: load_skill_catalog(validate_sot=False)
    old_cat = catalog_mod.get_skill_catalog
    old_del = delivery_mod.get_skill_catalog
    catalog_mod.get_skill_catalog = relaxed
    delivery_mod.get_skill_catalog = relaxed
    try:
        merged, _, _ = prepend_cdp_dispatch_skills(text, slugs)
    finally:
        catalog_mod.get_skill_catalog = old_cat
        delivery_mod.get_skill_catalog = old_del
    return merged


def _successor_duty_line(contract: str) -> str:
    if contract != "none":
        return (
            "duty: dispatch -> read back -> verify -> CP. Commission the work on a child lane "
            "(cursor_request, parent_thread=<root>, lane_role=sub_mission); read its closeout; "
            "verify against git before any 'landed' word; then checkpoint. "
            "Orienting and writing STAY is not the leg."
        )
    return (
        "duty: run the tick; checkpoint; hop only if hop_qualifies. "
        "Hop only when autonomous follow-up remains; HOLD_MERGE / empty NOW / quiet tick → STAY."
    )


def _compose_successor_wake(
    root_id: str,
    *,
    gear: str,
    row: str,
    seat: str = "cursor-sdk",
    tip_turn: int | None = None,
    tip_checkpoint_turn: int | None = None,
    spawn_signal_sources: list[str] | None = None,
    ring: str | None = None,
    extra_addresses: tuple[str, ...] = (),
    contract: str = "none",
) -> str:
    """Compose successor paste without cap shedding (for validation and render)."""
    echo = ring if ring else root_id
    tip_turn_val = tip_turn if tip_turn is not None else ""
    tip_cp_val = tip_checkpoint_turn if tip_checkpoint_turn is not None else ""
    signal_text = ",".join(spawn_signal_sources or []) or "none"
    extras = "".join(f"; {render_address(addr)}" for addr in extra_addresses)
    resume_args = f'{{"op":"resume","thread":"{root_id}"}}'
    liaison_slug = _successor_liaison_slug(seat)
    lines = [
        f"resume {root_id}",
        "",
        f"WAKE — liaison headless successor, house agent-bus:{root_id} — contract: {contract}.",
        _successor_duty_line(contract),
        "disclosure: orientation ritual; one echo before the first move.",
        f"objective: tip turn #{tip_turn_val} on agent-bus:{root_id}; "
        f"tip CHECKPOINT #{tip_cp_val}; row={row}; gear: {gear}; "
        f"spawn_signal={signal_text}.",
        f"addresses: dispatch(tool=\"continuity\", arguments='{resume_args}'); "
        f"agent_bus_read(thread_get, thread={root_id}); agent-bus:{echo} (echo){extras}",
    ]
    if seat_dispatch_surface(seat) is None:
        lines.append(
            f"Use the {liaison_slug} skill. "
            f"LOAD the {liaison_slug} skill body; do not skim."
        )
    if _seat_has_jupiter_shell(seat):
        lines.append("LOAD AND EXECUTE runbook:bus-consult-watcher (legs 1-3).")
    lines.extend(
        [
            f"frame: spawned by liaison-ticker gear {gear}; seat {seat}; "
            f"predecessor = prior lease holder on agent-bus:{root_id}. "
            "§ Peer-house: keep both; cdp/opus-5 → 2nd pool → cursor/claude-opus-5; "
            "¬ cursor/claude-fable-5-1; ¬ hop away unreconciled.",
            f'echo: agent_bus(send, thread={echo}, subject="ORIENTED {root_id}", '
            f'body="ORIENTED / tip: <CHECKPOINT subject> cp_ordinal=<n> / row: <row> / seat: {seat}") '
            "before the first mutating move.",
        ]
    )
    return "\n".join(lines) + "\n"


def successor_wake_unshed_byte_length(
    root_id: str,
    *,
    gear: str,
    row: str,
    seat: str = "cursor-sdk",
    tip_turn: int | None = None,
    tip_checkpoint_turn: int | None = None,
    spawn_signal_sources: list[str] | None = None,
    ring: str | None = None,
    extra_addresses: tuple[str, ...] = (),
    contract: str = "none",
) -> int:
    """Byte length of the successor paste before cap shedding (``--set`` validation)."""
    return len(
        _compose_successor_wake(
            root_id,
            gear=gear,
            row=row,
            seat=seat,
            tip_turn=tip_turn,
            tip_checkpoint_turn=tip_checkpoint_turn,
            spawn_signal_sources=spawn_signal_sources,
            ring=ring,
            extra_addresses=extra_addresses,
            contract=contract,
        ).encode("utf-8")
    )


def _truncate_row_for_cap(
    *,
    compose: Callable[[str], str],
    row: str,
    cap: int,
    marker: str = _SUCCESSOR_ROW_TRUNC_MARKER,
) -> str:
    """Shorten ``row`` (suffix marker) until ``compose(row)`` fits ``cap`` bytes."""
    if len(compose(row).encode("utf-8")) <= cap:
        return row
    row_bytes = row.encode("utf-8")
    lo, hi = 0, len(row_bytes)
    best = marker if row else row
    while lo <= hi:
        mid = (lo + hi) // 2
        prefix = row_bytes[:mid].decode("utf-8", errors="ignore")
        candidate = (prefix + marker) if prefix else marker
        if len(compose(candidate).encode("utf-8")) <= cap:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def render_successor_wake(
    root_id: str,
    *,
    gear: str,
    row: str,
    seat: str = "cursor-sdk",
    tip_turn: int | None = None,
    tip_checkpoint_turn: int | None = None,
    spawn_signal_sources: list[str] | None = None,
    ring: str | None = None,
    extra_addresses: tuple[str, ...] = (),
    contract: str = "none",
    cap: int = SUCCESSOR_WAKE_CAP,
) -> str:
    """Doorbell-shaped successor paste. Line 1 stays ``resume {root}`` (GIW fence).

    Same ritual as ``render_doorbell`` (duty / disclosure / objective / addresses /
    Use-line / frame / echo). No recall language — echo is a graph-read, not
    hypnotic memory (suggestion_orientation: fpsyg.2025.1433762).

    Over ``cap`` the renderer sheds the free-form ``row`` prose (``now_row`` bind)
    with a visible truncation marker, then optional planted ``extra_addresses`` from
    the tail — addresses, ids, root, and instruction lines are never shed.
    """
    planted = list(extra_addresses)

    def compose(*, row_text: str, addresses: tuple[str, ...]) -> str:
        return _compose_successor_wake(
            root_id,
            gear=gear,
            row=row_text,
            seat=seat,
            tip_turn=tip_turn,
            tip_checkpoint_turn=tip_checkpoint_turn,
            spawn_signal_sources=spawn_signal_sources,
            ring=ring,
            extra_addresses=addresses,
            contract=contract,
        )

    row_text = row
    message = compose(row_text=row_text, addresses=tuple(planted))
    if len(message.encode("utf-8")) <= cap:
        return _inline_successor_dispatch_skills(message, seat)

    def shed() -> bool:
        """Drop the least load-bearing fragment; False when only the ritual is left."""
        nonlocal row_text, planted
        if row_text and _SUCCESSOR_ROW_TRUNC_MARKER not in row_text:
            row_text = _truncate_row_for_cap(
                compose=lambda r: compose(row_text=r, addresses=tuple(planted)),
                row=row_text,
                cap=cap,
            )
            return True
        if planted:
            planted.pop()
            return True
        return False

    while len(message.encode("utf-8")) > cap and shed():
        message = compose(row_text=row_text, addresses=tuple(planted))
    if len(message.encode("utf-8")) > cap:
        row_text = _truncate_row_for_cap(
            compose=lambda r: compose(row_text=r, addresses=()),
            row=row_text,
            cap=cap,
        )
        message = compose(row_text=row_text, addresses=())
    return _inline_successor_dispatch_skills(message, seat)
