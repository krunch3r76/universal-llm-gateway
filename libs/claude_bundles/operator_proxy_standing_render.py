"""Bounded standing-handoff + checkpoint render for operator-proxy hops."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hop_handoff.standing_handoff import standing_handoff_uri

from claude_bundles.maestro_runbook_load import load_maestro_runbook
from claude_bundles.runbook_excerpt import extract_sections

_KB = 1024
CHECKPOINT_BUDGET_B = 8 * _KB
CHECKPOINT_HOP_INLINE_B = 2400
HOP_RENDER_MAX_B = 12_000
HOP_RUNNING_NOW_CAP_B = 200
HOP_INFLIGHT_CAP_B = 400
HOP_LIVE_LEG_CAP_B = 600
HOP_POINTERS_CAP_B = 300
HOP_LESSONS_CAP_B = 1200
HOP_REGISTER_CAP_B = 600
RULES_BUDGET_B = 8 * _KB
SECTION_OWED_CAP_B = 1536
SECTION_OPEN_CAP_B = 600
SECTION_LAST_CAP_B = 1536
SECTION_FIRST_ACTS_CAP_B = 300
PREAMBLE_SOFT_CAP_B = 30 * _KB
RUNBOOK_BUDGET_B = 32 * _KB
JOURNAL_HEAD_BUDGET_B = 64 * _KB

_TRUNCATABLE_SECTIONS = ("LAST LEG", "OWED / NEXT", "OPEN FOR KAYWAN")

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CURRENT_LEG_RE = re.compile(r"^##\s+CURRENT LEG\b", re.MULTILINE)
_REGISTRATION_RE = re.compile(
    r"^[ \t]*(?:[^\n]*?\bseat\s*=[^\n]*?)?\bregistration_id\s+(\S+)",
    re.IGNORECASE | re.MULTILINE,
)
_ARCHIVED_PREV_RE = re.compile(
    r"^archived_prev:\s*(\S+)\s+sha256\s+([0-9a-f]{64})\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_POLL_HINT_RE = re.compile(r"poll_hint\s+(\{.*\})", re.DOTALL)
_K_BULLET_RE = re.compile(r"\bK(\d+)\b")
_LEG_NUM_RE = re.compile(r"\bLEG\s+(\d+)\b")


def _utf8_len(text: str) -> int:
    return len((text or "").encode("utf-8"))


def _truncate_utf8(text: str, max_bytes: int) -> str:
    raw = (text or "").encode("utf-8")
    if len(raw) <= max_bytes:
        return text or ""
    cut = raw[:max_bytes]
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    return cut.decode("utf-8", errors="ignore")


def cap_utf8_for_hop(text: str, max_bytes: int, *, label: str) -> str:
    """Truncate hop slot text and append a budget flag when over *max_bytes*."""
    body = text or ""
    size = _utf8_len(body)
    if size <= max_bytes:
        return body
    flag = f"[hop_budget_exceeded {label} {size}/{max_bytes}]"
    room = max(0, max_bytes - _utf8_len(flag) - 1)
    trimmed = _truncate_utf8(body, room).rstrip()
    return f"{trimmed}\n{flag}" if trimmed else flag


def hop_house_rules_pointer(lane: str) -> str:
    """House-rules slot for hop render (L5); not an inline of the rules file."""
    return (
        f'House rules (do not inline): `fs(op="read", '
        f'path="cortex://notes/system/threads/{lane}-house-rules.md")` '
        f"and quote `read_sha256` from that read.\n"
    )


_HOP_TURN_REF_RE = re.compile(r"#(\d+)\b")


def extract_inflight_work_keys(inflight_section: str) -> frozenset[str]:
    """Work keys named in CURRENT LEG IN FLIGHT (L23)."""
    from claude_bundles.operator_proxy_live_leg import _WORK_KEY_IN_SUBJECT_RE

    return frozenset(
        _normalize_inflight_work_key(k)
        for k in _WORK_KEY_IN_SUBJECT_RE.findall(inflight_section or "")
    )


def hop_boundary_turn_from_header(header: str) -> int:
    """Largest explicit #turn in the CURRENT LEG header (previous-hop floor)."""
    refs = [int(m.group(1)) for m in _HOP_TURN_REF_RE.finditer(header or "")]
    return max(refs) if refs else 0


_INFLIGHT_PLAIN_LINE_RE = re.compile(r"^IN FLIGHT:\s*$", re.IGNORECASE)
_PLAIN_LEG_LABEL_RE = re.compile(
    r"^(?:OPEN|FRICTION|CARRIED|NEXT|Traps)\b(?:\s*\([^)\n]*\))?\s*:",
    re.IGNORECASE,
)
_TRAILING_WORK_KEY_PUNCT_RE = re.compile(r"[,;.:)\]}>]+$")


def _normalize_inflight_work_key(key: str) -> str:
    return _TRAILING_WORK_KEY_PUNCT_RE.sub("", key)


def current_leg_has_inflight_section(current_leg_block: str) -> bool:
    """True when CURRENT LEG has a structured IN FLIGHT section (not prose mention)."""
    block = current_leg_block or ""
    if re.search(r"^###\s+IN FLIGHT\s*$", block, re.MULTILINE):
        return True
    for line in block.splitlines():
        if _INFLIGHT_PLAIN_LINE_RE.match(line.strip()):
            return True
    return False


def inflight_section_from_sidecar(sidecar: str) -> str:
    """Return the IN FLIGHT body from a standing-handoff sidecar (h3 or plain line)."""
    current = _extract_current_leg_block(sidecar)
    if not current:
        return ""
    return _parse_h3_sections(current).get("IN FLIGHT", "")


def _heading_sections(text: str) -> list[tuple[str, str, int]]:
    lines = (text or "").splitlines()
    starts: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = _ATX_HEADING_RE.match(line)
        if match is None:
            continue
        starts.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[tuple[str, str, int]] = []
    for number, (index, level, heading) in enumerate(starts):
        end = len(lines)
        for later in range(number + 1, len(starts)):
            if starts[later][1] <= level:
                end = starts[later][0]
                break
        body = "\n".join(lines[index:end]).rstrip()
        sections.append((heading, body, level))
    return sections


def _extract_current_leg_block(sidecar: str) -> str | None:
    text = sidecar or ""
    match = _CURRENT_LEG_RE.search(text)
    if match is None:
        return None
    start = match.start()
    rest = text[start:]
    lines = rest.splitlines()
    end = len(lines)
    for i in range(1, len(lines)):
        m = _ATX_HEADING_RE.match(lines[i])
        if m and len(m.group(1)) <= 2:
            end = i
            break
    return "\n".join(lines[:end]).rstrip()


def _parse_h3_sections(block: str) -> dict[str, str]:
    """Parse ### sections inside ## CURRENT LEG."""
    lines = (block or "").splitlines()
    # Drop the source H2 here so flag lines prepended at render cannot hide it.
    if lines and _CURRENT_LEG_RE.match(lines[0]):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    preamble_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    inflight_plain_active = False
    for line in lines:
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            inflight_plain_active = False
            current_key = m.group(1).strip()
            sections[current_key] = [line]
            continue
        if _INFLIGHT_PLAIN_LINE_RE.match(line.strip()):
            inflight_plain_active = True
            current_key = "IN FLIGHT"
            sections[current_key] = [line]
            continue
        if inflight_plain_active and current_key == "IN FLIGHT":
            if _PLAIN_LEG_LABEL_RE.match(line.strip()) or _ATX_HEADING_RE.match(line):
                inflight_plain_active = False
                current_key = None
                preamble_lines.append(line)
                continue
        if current_key is None:
            preamble_lines.append(line)
        else:
            sections.setdefault(current_key, []).append(line)
    out: dict[str, str] = {}
    if preamble_lines:
        out["__preamble__"] = "\n".join(preamble_lines).strip()
    for key, chunk in sections.items():
        out[key] = "\n".join(chunk).strip()
    return out


def _atx_chunks(text: str) -> list[tuple[str, str]]:
    """Each heading plus the lines before the next heading, so parents omit children."""
    lines = (text or "").splitlines()
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _ATX_HEADING_RE.match(line)
        if match is not None:
            starts.append((index, match.group(2).strip()))
    if not starts:
        body = text.strip()
        return [("", body)] if body else []
    chunks: list[tuple[str, str]] = []
    if starts[0][0] > 0:
        pre = "\n".join(lines[: starts[0][0]]).strip()
        if pre:
            chunks.append(("", pre))
    for number, (index, heading) in enumerate(starts):
        end = starts[number + 1][0] if number + 1 < len(starts) else len(lines)
        chunks.append((heading, "\n".join(lines[index:end]).rstrip()))
    return chunks


def _legacy_header_text(sidecar: str) -> str:
    """Title block only: the first heading and the lines before the next heading."""
    lines = (sidecar or "").splitlines()
    chunk: list[str] = []
    seen_heading = False
    for line in lines:
        if _ATX_HEADING_RE.match(line):
            if seen_heading:
                break
            seen_heading = True
        chunk.append(line)
    return "\n".join(chunk).strip()


def _legacy_checkpoint_body(sidecar: str, *, house_rules_body: str | None) -> str:
    """Non-schema sidecar: each section once, with every HOUSE RULES span omitted."""
    if not sidecar:
        return ""
    parts: list[str] = []
    for heading, body in _atx_chunks(sidecar):
        if "HOUSE RULES" in heading:
            continue
        if body:
            parts.append(body)
    if parts:
        return "\n\n".join(parts).strip()
    trimmed = sidecar
    if house_rules_body:
        trimmed = trimmed.replace(house_rules_body, "").strip()
    return trimmed


def _cap_section(name: str, body: str) -> tuple[str, str | None]:
    caps = {
        "OWED / NEXT": SECTION_OWED_CAP_B,
        "OPEN FOR KAYWAN": SECTION_OPEN_CAP_B,
        "LAST LEG": SECTION_LAST_CAP_B,
        "FIRST ACTS": SECTION_FIRST_ACTS_CAP_B,
    }
    cap = caps.get(name)
    if cap is None:
        return body, None
    size = _utf8_len(body)
    if size <= cap:
        return body, None
    flag = f"[budget_exceeded {name} {size}/{cap} — read standing handoff]"
    return _truncate_utf8(body, cap) + "\n" + flag, flag


def _assemble_checkpoint(sections: dict[str, str]) -> str:
    known = {
        "__preamble__",
        "IN FLIGHT",
        "OWED / NEXT",
        "OPEN FOR KAYWAN",
        "LAST LEG",
        "FIRST ACTS",
        "HOUSE RULES",
    }
    parts: list[str] = []
    if sections.get("__preamble__"):
        parts.append(sections["__preamble__"])
    if sections.get("IN FLIGHT"):
        parts.append(sections["IN FLIGHT"])
    for name in ("OWED / NEXT", "OPEN FOR KAYWAN", "LAST LEG", "FIRST ACTS"):
        if sections.get(name):
            parts.append(sections[name])
    for heading, body in sections.items():
        if heading not in known and body:
            parts.append(body)
    return "\n\n".join(parts)


def _checkpoint_budget_body_len(sections: dict[str, str]) -> int:
    """Byte length subject to the 8 KB checkpoint cap (excludes IN FLIGHT)."""
    working = dict(sections)
    working.pop("IN FLIGHT", None)
    return _utf8_len(_assemble_checkpoint(working))


def _apply_checkpoint_budget(
    sections: dict[str, str],
    *,
    uri: str,
    max_budget: int = CHECKPOINT_BUDGET_B,
) -> tuple[str, list[str], bool]:
    """Render checkpoint excluding HOUSE RULES; IN FLIGHT is never truncated."""
    flags: list[str] = []
    working = dict(sections)
    inflight = working.get("IN FLIGHT", "")

    first_acts = working.get("FIRST ACTS", "")
    if first_acts:
        capped, flag = _cap_section("FIRST ACTS", first_acts)
        if flag:
            flags.append(flag.replace("standing handoff", uri))
        working["FIRST ACTS"] = capped

    exceeded = _checkpoint_budget_body_len(working) > max_budget
    caps = {
        "LAST LEG": SECTION_LAST_CAP_B,
        "OWED / NEXT": SECTION_OWED_CAP_B,
        "OPEN FOR KAYWAN": SECTION_OPEN_CAP_B,
    }
    for name in _TRUNCATABLE_SECTIONS:
        sec = working.get(name, "")
        if not sec:
            continue
        cap = caps[name]
        sec_size = _utf8_len(sec)
        if sec_size > cap:
            flag = f"[budget_exceeded {name} {sec_size}/{cap} — read {uri}]"
            flags.append(flag)
            working[name] = _truncate_utf8(sec, cap) + "\n" + flag

    while _checkpoint_budget_body_len(working) > max_budget:
        exceeded = True
        trimmed = False
        for name in _TRUNCATABLE_SECTIONS:
            sec = working.get(name, "")
            sec_size = _utf8_len(sec)
            if sec and sec_size > 80:
                new_cap = max(80, sec_size // 2)
                flag = f"[budget_exceeded {name} {sec_size}/{new_cap} — read {uri}]"
                flags.append(flag)
                shortened = _truncate_utf8(sec, new_cap)
                if _utf8_len(shortened) >= sec_size:
                    break
                working[name] = shortened
                trimmed = True
                break
        if not trimmed:
            break

    no_inflight = {k: v for k, v in working.items() if k != "IN FLIGHT"}
    tail = _assemble_checkpoint(no_inflight)
    tail_len = _utf8_len(tail)
    if tail_len > max_budget:
        exceeded = True
        flag = f"[budget_exceeded checkpoint {tail_len}/{max_budget} — read {uri}]"
        if flag not in flags:
            flags.append(flag)
        tail = _truncate_utf8(tail, max_budget) + "\n" + flag

    joined_parts: list[str] = []
    if inflight:
        # IN FLIGHT sits after preamble inside the checkpoint block
        pre = working.get("__preamble__", "")
        if pre:
            joined_parts.append(pre)
        joined_parts.append(inflight)
        if tail and tail != pre:
            if pre and tail.startswith(pre):
                tail = tail[len(pre) :].lstrip("\n")
            if tail:
                joined_parts.append(tail)
    else:
        joined_parts.append(tail)
    joined = "\n\n".join(p for p in joined_parts if p)
    return joined, flags, exceeded or bool(flags)


@dataclass
class InflightRow:
    raw_line: str
    worker: str | None = None
    execution_id: str | None = None
    poll_hint_json: str | None = None


def parse_inflight_rows(inflight_section: str) -> list[InflightRow]:
    rows: list[InflightRow] = []
    for line in (inflight_section or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        row = InflightRow(raw_line=line)
        hint = _POLL_HINT_RE.search(stripped)
        if hint:
            row.poll_hint_json = hint.group(1)
        worker_m = re.search(r"worker\s+(\d+)", stripped)
        if worker_m:
            row.worker = worker_m.group(1)
        exec_m = re.search(r"exec(?:ution_id)?\s+(\S+)", stripped)
        if exec_m:
            row.execution_id = exec_m.group(1)
        rows.append(row)
    return rows


def render_inflight_block(
    rows: list[InflightRow],
    *,
    lineage_lookup: Callable[[str, str], str | None] | None = None,
    max_bytes: int | None = None,
) -> str:
    lines = ["## In flight (from the checkpoint; liveness from bus lineage)"]
    lookup = lineage_lookup
    for row in rows:
        suffix = "liveness: unverified"
        if lookup and row.worker and row.execution_id:
            try:
                status = lookup(row.worker, row.execution_id)
            except Exception:  # noqa: BLE001 — fail-open
                status = None
            if status is None:
                suffix = "liveness: unverified"
            elif status == "live" or status == "":
                suffix = "liveness: live"
            else:
                suffix = f"liveness: terminal({status})"
        lines.append(f"{row.raw_line} {suffix}")
    text = "\n".join(lines)
    if max_bytes is None:
        return text
    return cap_utf8_for_hop(text, max_bytes, label="inflight")


_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")


def _bullet_ident(line: str, fallback: str) -> str:
    """Identify a bullet row from its leading token, not embedded ``K<n>`` text."""
    stripped = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line)
    km = re.match(r"^K(\d+)\b", stripped)
    if km:
        return f"K{km.group(1)}"
    num = re.match(r"^\s*(\d+)\.\s+", line)
    if num:
        return num.group(1)
    return fallback


def _split_rules_bullets(
    rules_body: str,
) -> tuple[str, str, list[tuple[str | None, str]]]:
    """Return heading, intro before the first bullet, and bullets newest-first."""
    lines = (rules_body or "").splitlines()
    heading = ""
    intro_lines: list[str] = []
    bullets: list[tuple[str | None, str]] = []
    current: list[str] = []
    current_k: str | None = None

    def flush() -> None:
        nonlocal current, current_k
        if current:
            text = "\n".join(current).strip()
            if text:
                bullets.append((current_k, text))
        current = []
        current_k = None

    for index, line in enumerate(lines):
        if index == 0 and line.startswith("#"):
            heading = line
            continue
        if _BULLET_LINE_RE.match(line):
            flush()
            current_k = _bullet_ident(line, "")
            current_k = current_k or None
            current = [line]
        elif current:
            current.append(line)
        else:
            intro_lines.append(line)
    flush()
    return heading, "\n".join(intro_lines).strip(), bullets


def _rules_truncation_flag(dropped_oldest_first: list[str], uri: str) -> str:
    return f"[rules truncated: {', '.join(dropped_oldest_first)} — read {uri}]"


def _dropped_suffix_oldest_first(
    rows: list[tuple[str, str]], drop_start: int
) -> list[str]:
    """Dropped row ids with the bottom-of-file (oldest) name first in the flag."""
    suffix = [ident for ident, _ in rows[drop_start:]]
    return list(reversed(suffix))


def _render_rules_body_len(
    *,
    heading_line: str,
    rows: list[tuple[str, str]],
    accept_count: int,
    drop_start: int,
    uri: str,
) -> int:
    parts: list[str] = []
    if heading_line:
        parts.append(heading_line)
    for index in range(accept_count):
        parts.append(rows[index][1])
    if drop_start < len(rows):
        flag = _rules_truncation_flag(
            _dropped_suffix_oldest_first(rows, drop_start),
            uri,
        )
        parts.append(flag)
    return _utf8_len("\n".join(parts))


def render_house_rules_block(
    *,
    lane: str,
    inline_rules: str | None,
    house_rules_text: str | None = None,
    read_cortex_uri: Callable[[str], str | None] | None = None,
) -> tuple[str, bool]:
    """Render HOUSE RULES. Each row is whole or dropped; drops are an oldest suffix."""
    uri = f"cortex://notes/system/threads/{lane}-house-rules.md"
    body = house_rules_text
    read_error: str | None = None
    if body is None and read_cortex_uri is not None:
        try:
            body = read_cortex_uri(uri)
        except (OSError, UnicodeError) as exc:
            body = None
            read_error = type(exc).__name__
    if body is None or not str(body).strip():
        body = inline_rules or ""
    if not body.strip():
        if read_error:
            flag = (
                f"[rules_file_unreadable: {read_error} — rendered inline "
                f"HOUSE RULES; read {uri}]"
            )
            return flag, False
        return "", False
    heading_line, intro, bullets = _split_rules_bullets(body)
    rows: list[tuple[str, str]] = []
    for index, (k_id, text) in enumerate(bullets):
        rows.append((k_id or f"bullet-{index + 1}", text))

    unreadable_line = (
        f"[rules_file_unreadable: {read_error} — rendered inline "
        f"HOUSE RULES; read {uri}]"
        if read_error
        else ""
    )
    budget = RULES_BUDGET_B - (_utf8_len(unreadable_line) + 1 if unreadable_line else 0)
    intro_included = False
    intro_dropped = False
    if intro:
        intro_parts: list[str] = []
        if heading_line:
            intro_parts.append(heading_line)
        intro_parts.append(intro)
        if _utf8_len("\n".join(intro_parts)) <= budget:
            intro_included = True
        else:
            intro_dropped = True

    accept_count = 0
    drop_start = len(rows)
    for index, (_ident, text) in enumerate(rows):
        row_len = _utf8_len(text)
        if row_len > budget:
            drop_start = index
            break
        parts: list[str] = []
        if heading_line:
            parts.append(heading_line)
        if intro_included and intro:
            parts.append(intro)
        for prior in range(accept_count):
            parts.append(rows[prior][1])
        parts.append(text)
        if _utf8_len("\n".join(parts)) > budget:
            drop_start = index
            break
        accept_count += 1
    else:
        drop_start = len(rows)

    while drop_start < len(rows):
        total = _render_rules_body_len(
            heading_line=heading_line,
            rows=rows,
            accept_count=accept_count,
            drop_start=drop_start,
            uri=uri,
        )
        if intro_included and intro:
            total += _utf8_len(intro) + 1
        if total <= budget:
            break
        if accept_count == 0:
            break
        accept_count -= 1
        drop_start = accept_count

    def _assemble() -> tuple[str, bool]:
        assembled: list[str] = []
        if heading_line:
            assembled.append(heading_line)
        if intro_included and intro:
            assembled.append(intro)
        for index in range(accept_count):
            assembled.append(rows[index][1])
        truncated = intro_dropped or drop_start < len(rows)
        if truncated:
            dropped: list[str] = []
            if intro_dropped:
                dropped.append("intro")
            if drop_start < len(rows):
                dropped.extend(_dropped_suffix_oldest_first(rows, drop_start))
            assembled.append(_rules_truncation_flag(dropped, uri))
        if unreadable_line:
            assembled.append(unreadable_line)
        return "\n".join(assembled), truncated

    # The reservation loop misses two assemblies: intro kept with accept_count
    # 0 (flag never reserved) and intro_dropped with no row drops (rows already
    # fill the budget). Shrink until the bytes that will be returned fit.
    while True:
        text, truncated = _assemble()
        if _utf8_len(text) <= RULES_BUDGET_B:
            return text, truncated
        if accept_count > 0:
            accept_count -= 1
            drop_start = accept_count
            continue
        if intro_included and intro:
            intro_included = False
            intro_dropped = True
            continue
        return text, truncated


def render_checkpoint_block(
    sidecar: str,
    *,
    lane: str,
    inline_house_rules: str | None,
    path_exists: Callable[[str], bool] | None = None,
    superseded_registration_id: str | None = None,
    hop_inline: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Render the checkpoint block callers inject into a hop prompt.

    ``sidecar`` is the standing-handoff body. The returned text always uses one
    ``## CURRENT LEG`` wrapper; a source heading in ``__preamble__`` is dropped
    at parse time so ``checkpoint_stale`` and ``budget_exceeded`` flags cannot
    leave a second ATX line. Metadata carries budget, identity, and flags.
    """
    uri = standing_handoff_uri(lane)
    meta: dict[str, Any] = {
        "budget": "ok",
        "identity": "unknown",
        "flags": [],
    }
    current = _extract_current_leg_block(sidecar)
    if current:
        sections = _parse_h3_sections(current)
    else:
        legacy = _legacy_checkpoint_body(sidecar, house_rules_body=inline_house_rules)
        sections = {"__legacy__": legacy} if legacy else {}

    if current:
        header = sections.get("__preamble__", "")
    else:
        header = _legacy_header_text(sidecar)
    meta["leg_header"] = header

    reg_match = _REGISTRATION_RE.search(header or "")
    checkpoint_reg = reg_match.group(1) if reg_match else None
    superseded = (superseded_registration_id or "").strip() or None
    if checkpoint_reg and superseded:
        if checkpoint_reg == superseded:
            meta["identity"] = "match"
        else:
            meta["identity"] = "stale"
            stale_line = (
                f"checkpoint_stale: written by {checkpoint_reg} not predecessor "
                f"{superseded}"
            )
            meta["flags"].append(stale_line)
    elif checkpoint_reg or superseded:
        meta["identity"] = "unknown"

    archived = _ARCHIVED_PREV_RE.search(header or "")
    if archived:
        path = archived.group(1)
        exists_check = path_exists
        missing = False
        if exists_check is not None:
            try:
                missing = not exists_check(path)
            except Exception as exc:  # noqa: BLE001 — unreadable ≠ absent
                meta["flags"].append(
                    f"[render_failed archive_path: {type(exc).__name__} — read {path}]"
                )
                missing = False
        if missing:
            meta["flags"].append("archive_missing")

    if current:
        render_sections = {
            k: v
            for k, v in sections.items()
            if "HOUSE RULES" not in k and not (hop_inline and k == "IN FLIGHT")
        }
        inline_cap = CHECKPOINT_HOP_INLINE_B if hop_inline else CHECKPOINT_BUDGET_B
        body, flags, exceeded = _apply_checkpoint_budget(
            render_sections, uri=uri, max_budget=inline_cap
        )
        meta["flags"].extend(flags)
        if exceeded:
            meta["budget"] = "exceeded"
    elif sections.get("__legacy__"):
        legacy = sections["__legacy__"]
        size = _utf8_len(legacy)
        if size > CHECKPOINT_BUDGET_B:
            meta["budget"] = "exceeded"
            flag = (
                f"[budget_exceeded checkpoint {size}/{CHECKPOINT_BUDGET_B}"
                f" — read {uri}]"
            )
            meta["flags"].append(flag)
            body = _truncate_utf8(legacy, CHECKPOINT_BUDGET_B) + "\n" + flag
        else:
            body = legacy
    else:
        body = ""

    inflight_section = sections.get("IN FLIGHT", "")
    meta["inflight_rows"] = parse_inflight_rows(inflight_section)

    prefix = "## CURRENT LEG (rendered checkpoint)\n"
    if meta["flags"]:
        body = "\n".join(meta["flags"]) + ("\n\n" + body if body else "")
    if body:
        return prefix + body, meta
    if meta["flags"]:
        return prefix + "\n".join(meta["flags"]), meta
    return "", meta


def render_pointers_block(
    *,
    checkpoint_preamble: str,
    rules_text: str,
    max_bytes: int | None = None,
) -> str:
    leg_nums = [
        int(m.group(1)) for m in _LEG_NUM_RE.finditer(checkpoint_preamble or "")
    ]
    k_ids = sorted({int(m.group(1)) for m in _K_BULLET_RE.finditer(rules_text or "")})
    leg_part = (
        f"LEG {min(leg_nums)}…{max(leg_nums)}" if leg_nums else "prior LEG blocks"
    )
    k_part = f"K{min(k_ids)}–K{max(k_ids)}" if k_ids else "K-row rulings"
    block = (
        f"- Prior LEG blocks ({leg_part}): "
        f"`cortex://notes/system/threads/<lane>-standing-handoff.md` and the house "
        "lanes listed above — still-open work is re-derived into the live leg; "
        "closed legs have no pending act.\n"
        f"- K-row rulings ({k_part}): runbook Appendix § Rulings (K-rows) — current "
        "binds are restated in step 5 R lines and in the hard refusals (K22 "
        "opus-5.5-extra, no fable).\n"
        "- Conductor trajectories (2026-09-30): runbook Appendix § Conductor "
        "trajectories — C1 routes conductor lanes to the Impresario house; "
        "`contract=conductor` is inline in hard refusals.\n"
        "- Journal entries: `cortex://notes/system/maestro/journal.md` — narrative; "
        "first act 4 reads its head.\n"
        "- Roster detail (bots, hop history): "
        "`cortex://notes/system/operational/maestro-sidekick-roster.md` — lane ids "
        "used at render are named in the running-now line.\n"
        "- Wake/send_later text: runbook step 8 — one-shot wakes only; the refusal "
        "against re-arming send_later stays inline."
    )
    if max_bytes is None:
        return block
    return cap_utf8_for_hop(block, max_bytes, label="pointers")


def runbook_refuse_steps_bytes(body: str | None) -> int | None:
    """UTF-8 length of ``## Refuse`` plus ``## Steps``, or None when unreadable."""
    if body is None:
        return None
    try:
        excerpt = extract_sections(body, ("Refuse", "Steps"))
    except ValueError:
        return None
    return _utf8_len(excerpt)


def maestro_runbook_act2_line() -> str:
    """Act-2 body for the hop template (ordinal ``2.`` lives on the template line)."""
    body, _err = load_maestro_runbook()
    size = runbook_refuse_steps_bytes(body)
    if size is None:
        return (
            "`md_read` cortex://notes/runbooks/maestro-loop.md sections "
            "§ Refuse and § Steps only (size unknown)."
        )
    return (
        "`md_read` cortex://notes/runbooks/maestro-loop.md sections "
        f"§ Refuse and § Steps only (runbook {size} bytes)."
    )


def measure_checkpoint_budget_bytes(
    sidecar: str,
    *,
    inline_house_rules: str | None = None,
) -> int | None:
    """UTF-8 bytes of the checkpoint subject to the 8 KB cap (excludes IN FLIGHT)."""
    current = _extract_current_leg_block(sidecar)
    if not current:
        legacy = _legacy_checkpoint_body(sidecar, house_rules_body=inline_house_rules)
        return _utf8_len(legacy) if legacy else None
    sections = _parse_h3_sections(current)
    working = {k: v for k, v in sections.items() if "HOUSE RULES" not in k}
    return _checkpoint_budget_body_len(working)


def render_budgets_table_block(
    *,
    lane: str,
    checkpoint_capped_bytes: int | None = None,
    house_rules_bytes: int | None = None,
    runbook_bytes: int | None = None,
    journal_head_bytes: int | None = None,
    preamble_bytes: int | None = None,
) -> str:
    """Markdown budgets table (plan §3) with live byte counts when supplied."""

    def _cell(value: int | None, cap: int | None = None) -> str:
        if value is None:
            return "—"
        if cap is not None:
            return f"{value}/{cap}"
        return str(value)

    handoff_uri = standing_handoff_uri(lane)
    rules_uri = f"cortex://notes/system/threads/{lane}-house-rules.md"
    lines = [
        "## Budgets",
        "",
        "Budgets are UTF-8 bytes. Enforcement is at render (truncate-and-flag); "
        "writers stay under budget.",
        "",
        "| Surface | Budget | Live bytes |",
        "|---|---|---|",
        (
            f"| `{lane}-standing-handoff.md` checkpoint (excl. HOUSE RULES; IN FLIGHT "
            f"uncapped) | {CHECKPOINT_BUDGET_B} | {_cell(checkpoint_capped_bytes, CHECKPOINT_BUDGET_B)} |"
        ),
        (
            f"| `{lane}-house-rules.md` | {RULES_BUDGET_B} | "
            f"{_cell(house_rules_bytes, RULES_BUDGET_B)} |"
        ),
        (
            f"| `maestro-loop.md` (§ Refuse + § Steps) | {RUNBOOK_BUDGET_B} | "
            f"{_cell(runbook_bytes, RUNBOOK_BUDGET_B)} |"
        ),
        (
            f"| `maestro/journal.md` head (arrival read) | {JOURNAL_HEAD_BUDGET_B} | "
            f"{_cell(journal_head_bytes, JOURNAL_HEAD_BUDGET_B)} |"
        ),
        (
            f"| hop preamble (soft; IN FLIGHT may push over) | {PREAMBLE_SOFT_CAP_B} | "
            f"{_cell(preamble_bytes, PREAMBLE_SOFT_CAP_B)} |"
        ),
        "",
        f"Standing handoff URI: {handoff_uri} · rules URI: {rules_uri}",
    ]
    return "\n".join(lines)


def assess_render_budget_and_identity(
    sidecar: str | None,
    *,
    lane: str,
    superseded_registration_id: str | None = None,
    inline_house_rules: str | None = None,
    house_rules_text: str | None = None,
    read_cortex_uri: Callable[[str], str | None] | None = None,
    path_exists: Callable[[str], bool] | None = None,
) -> tuple[str, str]:
    """Return (budget, identity) for assess_standing_handoff extension."""
    if not sidecar:
        return "ok", "unknown"
    _checkpoint, meta = render_checkpoint_block(
        sidecar,
        lane=lane,
        inline_house_rules=inline_house_rules,
        path_exists=path_exists,
        superseded_registration_id=superseded_registration_id,
    )
    rules, rules_exceeded = render_house_rules_block(
        lane=lane,
        inline_rules=inline_house_rules,
        house_rules_text=house_rules_text,
        read_cortex_uri=read_cortex_uri,
    )
    budget = meta.get("budget", "ok")
    if rules_exceeded:
        budget = "exceeded"
    return budget, str(meta.get("identity", "unknown"))


def measure_preamble_bytes(rendered: str) -> int:
    """UTF-8 bytes from the start of the hop body through ``## Your first acts``."""
    marker = "## Your first acts"
    head = (
        rendered.split(marker, 1)[0] if marker in (rendered or "") else (rendered or "")
    )
    return len(head.encode("utf-8"))


def compute_preamble_over_inflight_flag(
    staged_preamble: str,
    inflight_row_count: int,
) -> str | None:
    """Flag when the measured preamble exceeds the soft cap because of IN FLIGHT rows."""
    size = measure_preamble_bytes(staged_preamble)
    if size <= PREAMBLE_SOFT_CAP_B:
        return None
    if inflight_row_count <= 0:
        return None
    return f"[preamble over budget by IN FLIGHT: {inflight_row_count} rows]"


__all__ = [
    "CHECKPOINT_BUDGET_B",
    "CHECKPOINT_HOP_INLINE_B",
    "HOP_INFLIGHT_CAP_B",
    "HOP_LESSONS_CAP_B",
    "HOP_LIVE_LEG_CAP_B",
    "HOP_POINTERS_CAP_B",
    "HOP_REGISTER_CAP_B",
    "HOP_RENDER_MAX_B",
    "HOP_RUNNING_NOW_CAP_B",
    "JOURNAL_HEAD_BUDGET_B",
    "PREAMBLE_SOFT_CAP_B",
    "RULES_BUDGET_B",
    "RUNBOOK_BUDGET_B",
    "InflightRow",
    "assess_render_budget_and_identity",
    "cap_utf8_for_hop",
    "compute_preamble_over_inflight_flag",
    "current_leg_has_inflight_section",
    "extract_inflight_work_keys",
    "hop_boundary_turn_from_header",
    "hop_house_rules_pointer",
    "inflight_section_from_sidecar",
    "maestro_runbook_act2_line",
    "measure_checkpoint_budget_bytes",
    "measure_preamble_bytes",
    "parse_inflight_rows",
    "render_budgets_table_block",
    "render_checkpoint_block",
    "render_house_rules_block",
    "render_inflight_block",
    "render_pointers_block",
    "runbook_refuse_steps_bytes",
]
