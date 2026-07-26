"""CDP envelope v1 serializer and budget enforcement."""

from __future__ import annotations

from session_store.fence import wrap_fenced
from session_store.models import (
    AttachmentRef,
    Budget,
    EnvelopeOverBudget,
    InvalidRefError,
    SealResult,
    SessionForSeal,
    Turn,
)

_TRUNC_MARKER = "[…truncated — open original: Turn {n:04d} — {role}]"

# Spec §8: K < 2 while turn_count > 2 is invalid. The floor binds the caller's
# entry K, not just the shrink loop.
K_FLOOR = 2


def _assert_cortex_refs(refs: list[str]) -> None:
    for ref in refs:
        if not ref.startswith("cortex://"):
            raise InvalidRefError(ref)


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _tail(lines: list[str], cap: int) -> str:
    return "\n".join(lines[-cap:])


def _xml(tag: str, body: str) -> str:
    return f"<{tag}>\n{body}\n</{tag}>"


def _meta_block(session: SessionForSeal) -> str:
    sealed = session.sealed_at_turn if session.sealed_at_turn is not None else session.turn_count
    return (
        f'<session_meta session_id="{session.session_id}" '
        f'turn_count="{session.turn_count}" sealed_at_turn="{sealed}" '
        f'transcript="{session.transcript_uri}" archive="{session.archive_uri}"/>'
    )


def _instructions_block(transcript_uri: str) -> str:
    return f"""<session_instructions>
You are continuing an ongoing session. Below: Rollup (compressed arc), Index (one
line per archived turn), Live (verbatim recent turns), then the current request.
To open any original turn, call:
  fs(op="md_read", path="{transcript_uri}", section="Turn 0004 — assistant")
Copy the section string exactly as given in Index/Live. Open originals whenever a
distilled line is insufficient for the current request. Do not echo or summarize
this envelope in your reply; answer the current request directly.
</session_instructions>"""


def _cap_turn_body(turn: Turn, per_turn_cap: int) -> str:
    encoded = turn.body.encode("utf-8")
    if len(encoded) <= per_turn_cap:
        return turn.body
    marker = _TRUNC_MARKER.format(n=turn.n, role=turn.role)
    # The marker is irreducible — a cap too small to hold it still emits it so the
    # reader keeps a pointer back to the original turn.
    room = per_turn_cap - _utf8_size(marker) - 1
    if room < 1:
        return marker
    cut = room
    while cut > 0 and (encoded[cut - 1] & 0xC0) == 0x80:
        cut -= 1
    # errors="ignore" drops a trailing lead byte the boundary walk cannot reach.
    return encoded[:cut].decode("utf-8", errors="ignore") + "\n" + marker


def _render_live_turn(turn: Turn, per_turn_cap: int) -> str:
    body = _cap_turn_body(turn, per_turn_cap)
    return f"### Turn {turn.n:04d} — {turn.role}\n{wrap_fenced(body)}"


def _render_live_block(turns: list[Turn], per_turn_cap: int) -> str:
    return "\n\n".join(_render_live_turn(t, per_turn_cap) for t in turns)


def _attachments_block(attachments: list[AttachmentRef]) -> str:
    lines = ["<session_attachments>"]
    for att in attachments:
        note_attr = f' note="{att.note}"' if att.note else ""
        lines.append(
            f'<attachment turn="{att.turn:04d}" name="{att.name}" '
            f'ref="{att.ref}" media="{att.media}"{note_attr}/>'
        )
    lines.append("</session_attachments>")
    return "\n".join(lines)


def _current_request_md(msg: str) -> str:
    return f"## Current request\n\n{msg}\n"


def _wrap_envelope(inner_blocks: list[str]) -> str:
    body = "\n".join(inner_blocks)
    return f'<session_envelope version="1">\n{body}\n</session_envelope>\n\n'


def _live_turns(session: SessionForSeal, k: int) -> list[Turn]:
    if k <= 0 or session.turn_count <= 1:
        return []
    live = session.turns[-k:] if len(session.turns) >= k else list(session.turns)
    return [t for t in live if t.n != session.turn_count]


def _build_blocks(session: SessionForSeal, k: int, budget: Budget) -> list[str]:
    blocks = [_meta_block(session), _instructions_block(session.transcript_uri)]
    if session.rollup_text:
        blocks.append(_xml("session_rollup", session.rollup_text))
    if session.index_lines:
        blocks.append(_xml("session_index", _tail(session.index_lines, budget.index_line_cap)))
    live = _live_turns(session, k)
    if live:
        blocks.append(_xml("session_live", _render_live_block(live, budget.per_turn_cap)))
    if session.attachments:
        blocks.append(_attachments_block(session.attachments))
    return blocks


def _blocks_size(blocks: list[str]) -> int:
    return _utf8_size(_wrap_envelope(blocks))


def seal(
    session: SessionForSeal,
    current_user_msg: str,
    k: int = 6,
    budget: Budget | None = None,
) -> SealResult:
    if budget is None:
        budget = Budget()
    _assert_cortex_refs(session.refs)
    for att in session.attachments:
        _assert_cortex_refs([att.ref])

    k_used = max(k, K_FLOOR) if session.turn_count > K_FLOOR else k
    blocks = _build_blocks(session, k_used, budget)
    request_md = _current_request_md(current_user_msg)

    while _blocks_size(blocks) + _utf8_size(request_md) > budget.total and k_used > K_FLOOR:
        k_used -= 1
        blocks = _build_blocks(session, k_used, budget)

    total_size = _blocks_size(blocks) + _utf8_size(request_md)
    if total_size > budget.total:
        raise EnvelopeOverBudget(total_size, budget.total, k_used)

    prompt = _wrap_envelope(blocks) + request_md
    if current_user_msg not in prompt:
        raise EnvelopeOverBudget(total_size, budget.total, k_used)
    idx = prompt.index(current_user_msg)
    if prompt[idx : idx + len(current_user_msg)] != current_user_msg:
        raise EnvelopeOverBudget(total_size, budget.total, k_used)

    return SealResult(prompt_text=prompt, k_used=k_used, size_bytes=_utf8_size(prompt))
