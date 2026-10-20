"""Binding-turn consult_ref / check_ref coverage for impresario bus posts."""

from __future__ import annotations

import re
from typing import Any

_BINDING_SUBJECT_RE = re.compile(
    r"^(RULING|VERDICT|DECISION|BIND|PLAN)\b|\bBOUND\b",
    re.I,
)
_EXCLUDED_SUBJECT_RES = (
    re.compile(r"^CHECKPOINT", re.I),
    re.compile(r"^DIGEST", re.I),
    re.compile(r"^FACT\|", re.I),
    re.compile(r"^PICKUP", re.I),
    re.compile(r"^UPDATE\|", re.I),
    re.compile(r"^ACK\|", re.I),
    re.compile(r"^NUDGE\|", re.I),
)
_CONSULT_RE = re.compile(
    r"^consult_ref:[ \t]*(?P<advisor>[a-z0-9][a-z0-9._/-]*)[ \t]+"
    r"(?P<ref>[A-Za-z0-9:#._/-]+)"
    r"(?:[ \t]+sha256:(?P<sha>[0-9a-f]{64}))?[ \t]*\r?$",
    re.M,
)
_CHECK_RE = re.compile(
    r"^check_ref:[ \t]*(?P<checker>cdp/opus-5\.5(?:-extra)?)[ \t]+"
    r"(?P<ref>[A-Za-z0-9:#._/-]+)[ \t]*\r?$",
    re.M,
)
_CHECK_ANY_RE = re.compile(
    r"^check_ref:[ \t]*(?P<checker>[a-z0-9][a-z0-9._/-]*)[ \t]+"
    r"(?P<ref>[A-Za-z0-9:#._/-]+)[ \t]*\r?$",
    re.M,
)
_EXCEPTION_RE = re.compile(
    r"^exception:[ \t]*(?P<name>divergent_ideation_degraded_18362)\b",
    re.M,
)
_JOB_RE = re.compile(r"^Job:[ \t]*\S.*$", re.M)
_EVIDENCE_RE = re.compile(
    r"^[-*][ \t]+\S+://\S+[ \t]+(?:sha256:|read_sha256=)[0-9a-f]{64}\b",
    re.M,
)
_TRANSCRIPT_RE = re.compile(
    r"^(?:>|\[\d{1,2}:\d{2}|###[ \t]*Turn\b|(?:user|assistant)[ \t]*:)",
    re.I | re.M,
)
_BINDING_FROM = frozenset({"cursor", "grok-bot-gc1", "stage-manager"})


def _turn_from(turn: dict[str, Any]) -> str:
    return str(turn.get("from") or turn.get("from_agent") or "").strip()


def _turn_subject(turn: dict[str, Any]) -> str:
    return str(turn.get("subject") or "")


def _from_is_binding(sender: str) -> bool:
    if sender in _BINDING_FROM:
        return True
    return sender.startswith("grok-bot-")


def is_binding_turn(turn: dict[str, Any], *, root_id: str) -> bool:
    """True when ``turn`` is an eligible binding post on ``root_id``."""
    thread = turn.get("thread")
    if thread is None or str(thread) != str(root_id):
        return False
    sender = _turn_from(turn)
    if not sender or not _from_is_binding(sender):
        return False
    subject = _turn_subject(turn)
    if subject is None:
        return False
    if any(pat.search(subject) for pat in _EXCLUDED_SUBJECT_RES):
        return False
    return bool(_BINDING_SUBJECT_RE.search(subject))


def parse_consult_record(body: str) -> dict[str, str] | None:
    """Parse consult/check/exception lines from a binding body."""
    if not _CONSULT_RE.search(body):
        return None
    out: dict[str, str] = {}
    consult = _CONSULT_RE.search(body)
    if consult:
        out["advisor"] = consult.group("advisor")
        out["consult_ref"] = consult.group("ref")
        sha = consult.group("sha")
        if sha:
            out["consult_sha"] = sha
    check = _CHECK_RE.search(body)
    if check:
        out["checker"] = check.group("checker")
        out["check_ref"] = check.group("ref")
    exc = _EXCEPTION_RE.search(body)
    if exc:
        out["exception"] = exc.group("name")
    return out or None


def _section_header(line: str) -> str | None:
    stripped = line.strip()
    low = stripped.lower()
    if low.startswith("job:"):
        return "job"
    if low.startswith("evidence:"):
        return "evidence"
    if low.startswith("constraints:"):
        return "constraints"
    if stripped.endswith(":"):
        return "other"
    return None


def packet_lean_ok(body: str) -> tuple[bool, str]:
    """Validate Job → Evidence (≤10 sha lines) → Constraints packet shape."""
    if _TRANSCRIPT_RE.search(body):
        return False, "transcript_paste"
    lines = body.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines) or not _JOB_RE.match(lines[idx]):
        return False, "missing_job"
    job_line_count = sum(1 for line in lines if line.strip().lower().startswith("job:"))
    if job_line_count > 1:
        return False, "multiple_job_lines"

    headers: list[str] = []
    evidence_lines: list[str] = []
    current: str | None = "job"
    for line in lines[idx + 1 :]:
        hdr = _section_header(line)
        if hdr:
            headers.append(hdr)
            current = hdr
            continue
        if current == "evidence" and line.strip():
            evidence_lines.append(line)
    if "evidence" not in headers or "constraints" not in headers:
        return False, "constraints_not_last"
    if headers[-1] != "constraints":
        return False, "constraints_not_last"
    if headers.index("constraints") < headers.index("evidence"):
        return False, "constraints_not_last"
    if len(evidence_lines) > 10:
        return False, "evidence_over_cap"
    for el in evidence_lines:
        if not _EVIDENCE_RE.match(el):
            return False, "evidence_missing_sha"
    return True, "ok"


def coverage_ok(turn: dict[str, Any], *, root_id: str) -> tuple[bool, str]:
    """Binding turn must cite Argon consult, Opus check, and lean packet shape."""
    if not is_binding_turn(turn, root_id=root_id):
        return False, "not_binding"
    body = str(turn.get("body") or "")
    if not packet_lean_ok(body)[0]:
        return False, "packet_not_lean"
    record = parse_consult_record(body)
    if not record or not record.get("consult_ref"):
        return False, "missing_consult_ref"
    advisor = record.get("advisor", "")
    author = _turn_from(turn)
    if advisor == author:
        return False, "advisor_equals_author"
    checker = record.get("checker", "")
    check_any = _CHECK_ANY_RE.search(body)
    if not check_any:
        return False, "missing_check_ref"
    any_checker = check_any.group("checker")
    if advisor and any_checker == advisor:
        return False, "advisor_equals_checker"
    if not checker or not checker.startswith("cdp/opus-5.5"):
        return False, "check_model_not_opus_5_5"
    return True, "ok"
