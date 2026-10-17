from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_dialogue.excerpts import read_excerpts
from research_dialogue.retrieval import SearchResult

_PROMPTS = Path(__file__).resolve().parent / "prompts"

FORBIDDEN_BODY_KEYS = frozenset({"metadata", "tools", "mcp_servers"})


def _load(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _fill_p2(excerpt_block: str, minor_notes: str) -> str:
    import re

    tpl = _load("p2_turn1.txt")
    out, n = re.subn(
        r'(<document index="1"[^>]*>\n)(.*?)(\n</document>)',
        lambda m: f"{m.group(1)}{excerpt_block.strip()}{m.group(3)}",
        tpl,
        count=1,
        flags=re.DOTALL,
    )
    if n != 1:
        raise ValueError("p2_turn1 template missing document index=1 block")
    return out.replace("{the seven MINOR notes, verbatim from #1402}", minor_notes.strip())


def _format_retrieved(results: list[SearchResult], dropped: list[SearchResult]) -> str:
    parts: list[str] = ["<retrieved>"]
    for res in results:
        if res.dropped:
            continue
        signal_bits = []
        if res.weak_match is not None:
            signal_bits.append("weak" if res.weak_match else "ok")
        if res.rerank_status:
            signal_bits.append(res.rerank_status)
        if res.top_ce is not None:
            signal_bits.append(f"ce {res.top_ce:.2f}")
        signal = " / ".join(signal_bits) if signal_bits else "n/a"
        parts.append(
            f'<search id="{res.search_id}" scope="{res.scope}" query="{res.query}" signal="{signal}">'
        )
        for p in res.passages:
            parts.append(f'<passage source="{p.source}" rank="{p.rank}">')
            parts.append(p.text)
            parts.append("</passage>")
        parts.append("</search>")
    for d in dropped:
        parts.append(f'<dropped scope="{d.scope}" reason="{d.drop_reason or "dropped"}"/>')
    parts.append("</retrieved>")
    return "\n".join(parts)


def _fill_p3(
    *,
    retrieval_results: list[SearchResult],
    dropped: list[SearchResult],
    ledger_verbatim: str,
    challenge: str,
) -> str:
    import re

    tpl = _load("p3_partner.txt")
    retrieved = _format_retrieved(retrieval_results, dropped)
    inner = retrieved
    if inner.startswith("<retrieved>\n") and inner.endswith("</retrieved>"):
        inner = inner[len("<retrieved>\n") : -len("</retrieved>")].strip()
    out, n = re.subn(
        r"(<retrieved>\n)(.*?)(\n</retrieved>)",
        lambda m: f"{m.group(1)}{inner}{m.group(3)}",
        tpl,
        count=1,
        flags=re.DOTALL,
    )
    if n != 1:
        raise ValueError("p3_partner template missing <retrieved> block")
    out = out.replace("{your last <ledger>, verbatim}", ledger_verbatim.strip())
    out = out.replace(
        '{one item from the driver or Kaywan: a passage that cuts against a current recommendation, a falsifier to test now, or a question. Marked "Kaywan:" when he wrote it.}',
        challenge.strip(),
    )
    return out


def _fill_p4(stop_reason: str) -> str:
    return _load("p4_closing.txt").replace("{stop_reason}", stop_reason)


def _user_block(text: str, *, cache_ephemeral: bool) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache_ephemeral:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def build_messages_body(
    *,
    turn_index: int,
    history: list[dict[str, str]],
    deliverable_path: str | None = None,
    minor_notes: str | None = None,
    excerpt_text: str | None = None,
    retrieval_results: list[SearchResult] | None = None,
    dropped_queries: list[SearchResult] | None = None,
    ledger_verbatim: str | None = None,
    challenge: str | None = None,
    closing: bool = False,
    stop_reason: str | None = None,
    model: str = "claude-opus-5-5",
    max_tokens: int = 16_000,
    smoke: bool = False,
) -> dict[str, Any]:
    system_text = _load("p1_system.txt")
    system_block: dict[str, Any] = {
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }
    messages: list[dict[str, Any]] = []

    for idx, item in enumerate(history):
        role = item["role"]
        content = item["content"]
        if role == "user":
            messages.append({"role": "user", "content": [ _user_block(content, cache_ephemeral=False) ]})
        else:
            messages.append({"role": "assistant", "content": content})

    if closing:
        user_text = _fill_p4(stop_reason or "unknown")
    elif turn_index <= 1:
        if excerpt_text is None:
            if deliverable_path is None:
                raise ValueError("deliverable_path or excerpt_text required for turn 1")
            excerpt_text = read_excerpts(deliverable_path)
        if minor_notes is None:
            raise ValueError("minor_notes required for turn 1")
        user_text = _fill_p2(excerpt_text, minor_notes)
    else:
        user_text = _fill_p3(
            retrieval_results=retrieval_results or [],
            dropped=dropped_queries or [],
            ledger_verbatim=ledger_verbatim or "",
            challenge=challenge or "",
        )

    cache_last = not smoke and not closing
    messages.append(
        {
            "role": "user",
            "content": [_user_block(user_text, cache_ephemeral=cache_last)],
        }
    )

    body: dict[str, Any] = {
        "model": model,
        "system": [system_block],
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    return body


def context_token_count(usage: dict[str, Any]) -> int:
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    total = 0
    for k in keys:
        v = usage.get(k)
        if isinstance(v, int) and v >= 0:
            total += v
    return total


def assert_no_forbidden_keys(body: dict[str, Any]) -> None:
    for key in FORBIDDEN_BODY_KEYS:
        if key in body:
            raise ValueError(f"forbidden key present: {key}")
