"""Packet text, answer extraction, and closeout injection for the second read.

The reflex leg is bought for the manager's benefit, not the executor's, so the
packet asks the three questions a remote manager cannot answer from a §2 table it
did not watch being written: is the evidence load-bearing, what is most likely
wrong, and what is missing. Everything here is pure so the wording stays testable.
"""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.section2_fields import (
    section2_field_names,
)

SECOND_READ_BEGIN = "SECOND_READ_BEGIN"
SECOND_READ_END = "SECOND_READ_END"

# The reflex reads a closeout, not a repo — a long tail of executor prose buys
# nothing and the packet is the only place this lane's cost is actually bounded.
_MAX_CLOSEOUT_CHARS = 6000
_MAX_DIRECTIVE_CHARS = 2000
MAX_SECOND_READ_CHARS = 1200

_SENTINEL_RE = re.compile(
    rf"{SECOND_READ_BEGIN}(.*?){SECOND_READ_END}",
    re.DOTALL,
)
# Status tokens a delegate read may never confer on itself; see P3.1 provenance.
_RESERVED_STATUS_RE = re.compile(
    r"(?i)\b(ratified|verified|approved|signed.?off|certified)\b"
)

_ENVELOPE_FIELD_NAMES = (
    *section2_field_names(),
    "ac verdict",
    "open_forks",
    "TYPE",
    "deviations",
    "deployment_state",
)
_FIELD_ALTERNATION = "|".join(re.escape(name) for name in _ENVELOPE_FIELD_NAMES)
# A line the relay's extractors would read as an authored field: a plain or bold
# `field:` line, a markdown table row, or an ATX heading naming a field.
_ENVELOPE_FIELD_LINE_RE = re.compile(
    rf"(?im)^[ \t]*(?:\|.*"
    rf"|#{{1,6}}[ \t]*(?:\*\*)?(?:{_FIELD_ALTERNATION})\b.*"
    rf"|(?:\*\*)?(?:{_FIELD_ALTERNATION})(?:\*\*)?[ \t]*[:=].*)$"
)
# Scanners that key on a bare token anywhere in the body rather than at a line
# start — notably the auth-gate rule reading `ac_verdict: AC1=fail`.
_ENVELOPE_FIELD_TOKEN_RE = re.compile(r"(?i)\b(ac_verdict|ac verdict)\b(?=[ \t]*[:=])")


def _clamp(text: str | None, limit: int) -> str:
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return f"{body[:limit].rstrip()}\n…[clamped at {limit} chars]"


def build_reflex_packet(
    *,
    directive_body: str | None,
    closeout_body: str | None,
    executor_model: str,
    contract: str,
    executor_dispatch_id: str,
) -> str:
    """Compose the read-only second-read prompt for the nested reflex dispatch."""
    return "\n".join(
        [
            "Nested cursor-sdk SECOND READ commissioned by cursor-auto.",
            f"contract=light-bounded reading={contract} "
            f"executor={executor_model} executor_dispatch={executor_dispatch_id}",
            "",
            "## Boundary (binding)",
            "READ ONLY. Do not edit, create, delete, stage, or commit any file.",
            "Do not run tests, builds, restarts, or any mutating command.",
            "Do not dispatch further work. Reading named files is permitted and",
            "expected; verifying a claim against the file it cites is the point.",
            "",
            "## What was asked of the executor",
            _clamp(directive_body, _MAX_DIRECTIVE_CHARS) or "(no directive captured)",
            "",
            "## What the executor reported",
            _clamp(closeout_body, _MAX_CLOSEOUT_CHARS) or "(no closeout captured)",
            "",
            "## Your task",
            "Answer exactly three questions for the manager who will read this",
            "instead of the full transcript. Be specific and name files or lines;",
            "a generic caution is worth nothing here.",
            "",
            "1. EVIDENCE — does the reported evidence actually support the claimed",
            "   outcome? Say plainly if a claim is asserted without a checkable",
            "   artifact behind it.",
            "2. LIKELIEST ERROR — the single most probable thing the executor got",
            "   wrong or skipped. Exactly one; rank if you see several.",
            "3. MISSING — the one thing the manager needs that this closeout does",
            "   not contain.",
            "",
            "If the closeout is sound, say so in one line — a clean read is a",
            "useful result and must not be padded into a false concern.",
            "",
            "## Output (binding)",
            f"Emit your entire answer between {SECOND_READ_BEGIN} and",
            f"{SECOND_READ_END} sentinels on their own lines, under "
            f"{MAX_SECOND_READ_CHARS} characters total, no preamble.",
            "You are an advisory reader, not a ratifier: never write 'ratified',",
            "'verified', 'approved', or 'signed off' about this work.",
            "",
            f"{SECOND_READ_BEGIN}",
            "1. EVIDENCE — …",
            "2. LIKELIEST ERROR — …",
            "3. MISSING — …",
            f"{SECOND_READ_END}",
        ]
    )


def parse_second_read(body: str | None) -> str | None:
    """Extract the sentinel-delimited answer from a reflex closeout body."""
    text = (body or "").strip()
    if not text:
        return None
    matches = _SENTINEL_RE.findall(text)
    # Take the last match: the packet echoes a template skeleton, so an answer
    # that repeats the sentinels lands after the instructions it was given.
    for candidate in reversed(matches):
        answer = candidate.strip()
        if answer and "1. EVIDENCE — …" not in answer:
            return _clamp(answer, MAX_SECOND_READ_CHARS)
    return None


def scrub_reserved_status(text: str) -> str:
    """Neutralize ratification-class words a delegate read has no authority to use."""
    return _RESERVED_STATUS_RE.sub("assessed", text)


def scrub_envelope_fields(text: str) -> str:
    """Defuse §2 field tokens so the advisory cannot be read as the closeout.

    The block is appended to the body that later feeds ``resolve_relay_status``,
    ``tag_gate_class_for_payload``, and the auth-gate classifier, and every one of
    those scans the whole body rather than a header region. A second read that
    quotes the executor — "your ac_verdict: AC1=fail is unsupported" — would
    otherwise flip the envelope status or burn auth-gate budget on a clean
    mission. Quoting the field names keeps the prose readable while making them
    invisible to extractors that key on line starts and bare tokens.
    """
    defused = _ENVELOPE_FIELD_LINE_RE.sub(r"> \g<0>", text)
    return _ENVELOPE_FIELD_TOKEN_RE.sub(lambda m: f"`{m.group(1)}`", defused)


def inject_second_read_block(
    relay_body: str,
    *,
    text: str,
    model: str,
    reflex_dispatch_id: str,
    reason: str,
) -> str:
    """Append the provenance-stamped second-read block to a relay closeout body."""
    answer = scrub_envelope_fields(scrub_reserved_status(_clamp(text, MAX_SECOND_READ_CHARS)))
    if not answer:
        return relay_body
    block = "\n".join(
        [
            "",
            "## SECOND READ (advisory — not a ratification)",
            f"second_read(by={model}, ref={reflex_dispatch_id}, trigger={reason})",
            "",
            answer,
        ]
    )
    return f"{relay_body.rstrip()}\n{block}\n"


__all__ = [
    "MAX_SECOND_READ_CHARS",
    "SECOND_READ_BEGIN",
    "SECOND_READ_END",
    "build_reflex_packet",
    "inject_second_read_block",
    "scrub_envelope_fields",
    "parse_second_read",
    "scrub_reserved_status",
]
