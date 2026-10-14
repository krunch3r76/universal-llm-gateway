"""Bounded standing-handoff + checkpoint render for operator-proxy hops."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_bundles.maestro_runbook_load import load_maestro_runbook
from claude_bundles.runbook_excerpt import extract_sections
from hop_handoff.standing_handoff import standing_handoff_uri

_KB = 1024
CHECKPOINT_BUDGET_B = 8 * _KB
RULES_BUDGET_B = 8 * _KB
RULES_BULLET_CAP_B = 600
SECTION_OWED_CAP_B = 1536
SECTION_OPEN_CAP_B = 600
SECTION_LAST_CAP_B = 1536
SECTION_FIRST_ACTS_CAP_B = 300
PREAMBLE_SOFT_CAP_B = 30 * _KB

_TRUNCATABLE_SECTIONS = ("LAST LEG", "OWED / NEXT", "OPEN FOR KAYWAN")

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CURRENT_LEG_RE = re.compile(r"^##\s+CURRENT LEG\b", re.MULTILINE)
_REGISTRATION_RE = re.compile(
    r"registration_id\s+(\S+)",
    re.IGNORECASE,
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
    for heading, _body, level in _heading_sections(rest)[1:]:
        if level <= 2 and not heading.startswith("CURRENT LEG"):
            break
    # Re-scan: body until next ## at level 2 excluding CURRENT LEG children
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
    preamble_lines: list[str] = []
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    for line in lines:
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            current_key = m.group(1).strip()
            sections[current_key] = [line]
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


def _legacy_checkpoint_body(sidecar: str, *, house_rules_body: str | None) -> str:
    """Non-schema sidecar: everything except HOUSE RULES headings."""
    if not sidecar:
        return ""
    parts: list[str] = []
    for heading, body, level in _heading_sections(sidecar):
        if level <= 2 and "HOUSE RULES" in heading:
            continue
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

    exceeded = _checkpoint_budget_body_len(working) > CHECKPOINT_BUDGET_B
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

    while _checkpoint_budget_body_len(working) > CHECKPOINT_BUDGET_B:
        exceeded = True
        trimmed = False
        for name in _TRUNCATABLE_SECTIONS:
            sec = working.get(name, "")
            if sec and _utf8_len(sec) > 80:
                working[name] = _truncate_utf8(sec, max(80, _utf8_len(sec) // 2))
                trimmed = True
                break
        if not trimmed:
            break

    no_inflight = {k: v for k, v in working.items() if k != "IN FLIGHT"}
    tail = _assemble_checkpoint(no_inflight)
    tail_len = _utf8_len(tail)
    if tail_len > CHECKPOINT_BUDGET_B:
        exceeded = True
        flag = (
            f"[budget_exceeded checkpoint {tail_len}/{CHECKPOINT_BUDGET_B} — read {uri}]"
        )
        if flag not in flags:
            flags.append(flag)
        tail = _truncate_utf8(tail, CHECKPOINT_BUDGET_B) + "\n" + flag

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
    return "\n".join(lines)


def _split_rules_bullets(rules_body: str) -> list[tuple[str | None, str]]:
    """Return (K-id or None, bullet text) bottom-to-top order for truncation."""
    lines = (rules_body or "").splitlines()
    if not lines:
        return []
    heading = lines[0] if lines[0].startswith("#") else ""
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

    for line in lines[1:] if heading else lines:
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            flush()
            km = _K_BULLET_RE.search(line)
            current_k = f"K{km.group(1)}" if km else None
            current = [line]
        elif current:
            current.append(line)
        elif heading and not bullets:
            heading = heading + "\n" + line
    flush()
    if heading and not bullets:
        return [(None, heading)]
    return bullets


def render_house_rules_block(
    *,
    lane: str,
    inline_rules: str | None,
    house_rules_text: str | None = None,
    read_cortex_uri: Callable[[str], str | None] | None = None,
) -> tuple[str, bool]:
    uri = f"cortex://notes/system/threads/{lane}-house-rules.md"
    body = house_rules_text
    if body is None and read_cortex_uri is not None:
        try:
            body = read_cortex_uri(uri)
        except OSError:
            body = None
    if body is None or not str(body).strip():
        body = inline_rules or ""
    if not body.strip():
        return "", False
    bullets = _split_rules_bullets(body)
    kept: list[str] = []
    truncated_ids: list[str] = []
    total = 0
    heading_line = ""
    if body.lstrip().startswith("#"):
        first_line = body.splitlines()[0]
        heading_line = first_line
        total += _utf8_len(first_line + "\n")

    # Keep from top (newest first after heading); truncate oldest (bottom)
    bullet_entries: list[tuple[str, str, str | None]] = []
    for k_id, text in bullets:
        bullet_entries.append((k_id or f"bullet-{len(bullet_entries)+1}", text, k_id))

    assembled: list[str] = []
    if heading_line:
        assembled.append(heading_line)
    size = _utf8_len("\n".join(assembled))
    exceeded = False
    for ident, text, k_id in bullet_entries:
        bsize = _utf8_len(text)
        if bsize > RULES_BULLET_CAP_B:
            text = _truncate_utf8(text, RULES_BULLET_CAP_B)
            bsize = _utf8_len(text)
        if size + bsize + 1 > RULES_BUDGET_B:
            exceeded = True
            truncated_ids.append(k_id or ident)
            continue
        assembled.append(text)
        size += bsize + 1

    if truncated_ids:
        # oldest first in flag = bottom-most truncated first
        rev = list(reversed(truncated_ids))
        flag = f"[rules truncated: {', '.join(rev)} — read {uri}]"
        assembled.append(flag)
    return "\n".join(assembled), exceeded


def render_checkpoint_block(
    sidecar: str,
    *,
    lane: str,
    inline_house_rules: str | None,
    path_exists: Callable[[str], bool] | None = None,
    superseded_registration_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render ## CURRENT LEG checkpoint block and metadata flags."""
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

    preamble_text = sections.get("__preamble__", "") or sections.get("__legacy__", "")
    if not preamble_text and current:
        preamble_text = current.splitlines()[0] if current else ""

    reg_match = _REGISTRATION_RE.search(preamble_text or sidecar or "")
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

    archived = _ARCHIVED_PREV_RE.search(preamble_text or "")
    if archived:
        path = archived.group(1)
        exists_check = path_exists
        missing = False
        if exists_check is not None:
            try:
                missing = not exists_check(path)
            except Exception:  # noqa: BLE001
                missing = True
        if missing:
            meta["flags"].append("archive_missing")

    if current:
        render_sections = {k: v for k, v in sections.items() if k != "HOUSE RULES"}
        body, flags, exceeded = _apply_checkpoint_budget(render_sections, uri=uri)
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
) -> str:
    leg_nums = [int(m.group(1)) for m in _LEG_NUM_RE.finditer(checkpoint_preamble or "")]
    k_ids = sorted({int(m.group(1)) for m in _K_BULLET_RE.finditer(rules_text or "")})
    leg_part = (
        f"LEG {min(leg_nums)}…{max(leg_nums)}"
        if leg_nums
        else "prior LEG blocks"
    )
    k_part = (
        f"K{min(k_ids)}–K{max(k_ids)}"
        if k_ids
        else "K-row rulings"
    )
    return (
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


def maestro_runbook_act2_line() -> str:
    body, err = load_maestro_runbook()
    if not body:
        size_note = "size unknown" if err else "size unknown"
        return (
            "2. `md_read` cortex://notes/runbooks/maestro-loop.md sections "
            f"§ Refuse and § Steps only ({size_note})."
        )
    try:
        excerpt = extract_sections(body, ("Refuse", "Steps"))
    except ValueError:
        excerpt = ""
    size = _utf8_len(body)
    return (
        "2. `md_read` cortex://notes/runbooks/maestro-loop.md sections "
        f"§ Refuse and § Steps only (runbook {size} bytes)."
    )


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


def compute_preamble_over_inflight_flag(
    staged_preamble: str,
    inflight_row_count: int,
) -> str | None:
    size = _utf8_len(staged_preamble)
    if size <= PREAMBLE_SOFT_CAP_B:
        return None
    if inflight_row_count <= 0:
        return None
    return f"[preamble over budget by IN FLIGHT: {inflight_row_count} rows]"


__all__ = [
    "CHECKPOINT_BUDGET_B",
    "PREAMBLE_SOFT_CAP_B",
    "InflightRow",
    "assess_render_budget_and_identity",
    "compute_preamble_over_inflight_flag",
    "maestro_runbook_act2_line",
    "parse_inflight_rows",
    "render_checkpoint_block",
    "render_house_rules_block",
    "render_inflight_block",
    "render_pointers_block",
]
