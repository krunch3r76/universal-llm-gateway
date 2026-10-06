"""Fail-closed gates for conductor nested CDP prompt authoring (a:37183).

Ritual ``Use retrieval-before-authoring`` claims are not proof. Admit reads
artifacts: a ``retrieval_report:`` citation on the *author* prompt body, a
report sidecar with non-empty Queries / Yields / Choice-to-evidence plus a
``target:`` line bound to the prompt, and a chrome refuse when a SKEPTIC /
adversarial-spec author body opens as delivery or code-review.

a:37183 review A1–A4: arm on line-anchored author declarations only (not
merged skill/inline text or mid-sentence mentions); wire follow-up paste;
bind report ``target:``; ``Path.is_relative_to`` + casefold chrome.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from implement_admission.closeout_helpers import cortex_files_root

# Citation + binding lines the authoring seat must leave on the author body.
_RETRIEVAL_REPORT_LINE = re.compile(r"(?im)^retrieval_report:\s*(cortex://\S+)\s*$")
# Strict: column-0 ``target:`` + one token on the same line (no newline in gap).
_REPORT_TARGET_STRICT = re.compile(r"(?im)^target:[ \t]*(\S+)[ \t]*$")
# Loose: any line that looks like an indented/bulleted/bolded binding attempt.
_REPORT_TARGET_LOOSE = re.compile(
    r"(?im)^[ \t]*(?:[-*>][ \t]*)?\**target:(.*)$"
)
_BODY_TODO = re.compile(r"(?i)\btodo:([\w.-]+)\b")

# Human hint strings; kept in sync with ``body_target_tokens`` (see tests).
_BINDABLE_TARGET_HINTS: tuple[str, ...] = (
    "todo:<id> anywhere in author body",
    "friction:<n> anywhere in author body",
    "line-start gate_path=<X> (also bare <X>)",
    "line-start You are the G1|G2|G4|G6",
    "line-start Genre: adversarial spec → adversarial-spec",
    "line-start job=delivery-review → delivery-review",
)

# Spec-skeptic / G4 — line-anchored role declarations only (A1).
_SKEPTIC_SHAPE = re.compile(
    r"(?im)(?:"
    r"^gate_path\s*=\s*SKEPTIC\b|"
    r"^Genre:\s*adversarial\s+spec\b|"
    r"^You are the G4\b|"
    r"^spec-skeptic\b"
    r")"
)

# Conductor nested CDP width — line-anchored (A1); ``m`` keeps retrieval_report live.
_NESTED_WIDTH_SHAPE = re.compile(
    r"(?im)(?:"
    r"^gate_path\s*=\s*(?:SKEPTIC|SKETCH|ACTIVE)\b|"
    r"^Genre:\s*adversarial\s+spec\b|"
    r"^You are the G[1246]\b|"
    r"^job\s*=\s*delivery-review\b|"
    r"^retrieval_report:"
    r")"
)

_REQUIRED_REPORT_HEADINGS: tuple[str, ...] = (
    "## Queries",
    "## Yields",
    "## Choice-to-evidence",
)

_DELIVERY_CHROME_MARKERS: tuple[str, ...] = (
    "the packet carries the code under review",
    "do not reject because you cannot check out a commit",
    "do not reject because you cannot check out a commit, run pytest, or run quality_gate",
)

# Capture the ATX marker so a deeper heading (### under ##) is in-section, not a terminator (a:37914).
_HEADING_MARKERS = re.compile(r"(?m)^(#{1,6})\s+.+$")


class NestedCdpPromptGateError(ValueError):
    """Nested CDP prompt authoring gate failure (maps to HTTP 422)."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def is_skeptic_shaped(text: str) -> bool:
    """True when *author* body declares G4 / SKEPTIC / adversarial-spec on a line."""
    return bool(_SKEPTIC_SHAPE.search(text or ""))


def is_nested_width_shaped(text: str) -> bool:
    """True when *author* body declares a conductor nested CDP width seat."""
    return bool(_NESTED_WIDTH_SHAPE.search(text or ""))


def has_delivery_review_chrome(text: str) -> bool:
    """True when delivery/code-review reading charter chrome is present (casefold)."""
    folded = (text or "").casefold()
    return any(marker in folded for marker in _DELIVERY_CHROME_MARKERS)


def parse_retrieval_report_uri(text: str) -> str | None:
    """Return the first ``retrieval_report: cortex://…`` citation, else None."""
    match = _RETRIEVAL_REPORT_LINE.search(text or "")
    if match is None:
        return None
    return match.group(1).strip()


def _report_lines_outside_fences(report_body: str) -> list[str]:
    """Physical lines, skipping fenced code blocks."""
    lines: list[str] = []
    in_fence = False
    for line in (report_body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _first_target_binding(report_body: str) -> tuple[
    Literal["absent", "valid", "invalid"], str | None, str | None
]:
    """Classify the first target-like line (outside fences, before first ``##``).

    Returns (state, token_or_none, raw_line_for_errors).
    """
    before_heading = (report_body or "").split("##", 1)[0]
    for line in _report_lines_outside_fences(before_heading):
        loose = _REPORT_TARGET_LOOSE.match(line)
        if loose is None:
            continue
        strict = _REPORT_TARGET_STRICT.match(line)
        if strict is not None:
            return "valid", strict.group(1).strip(), line
        return "invalid", None, line
    return "absent", None, None


def parse_report_target(report_body: str) -> str | None:
    """Return the bound ``target:`` token when the first binding line is valid."""
    state, token, _raw = _first_target_binding(report_body)
    if state == "valid":
        return token
    return None


def has_report_target_line(report_body: str) -> bool:
    """True when a target-like line is present (valid or malformed)."""
    state, _, _ = _first_target_binding(report_body)
    return state != "absent"


def body_target_tokens(author_body: str) -> set[str]:
    """Tokens the report ``target:`` may bind to (todo ids + gate declarations)."""
    text = author_body or ""
    tokens: set[str] = set()
    for match in _BODY_TODO.finditer(text):
        tokens.add(f"todo:{match.group(1)}")
    for match in re.finditer(r"(?i)\bfriction:(\d+)\b", text):
        tokens.add(f"friction:{match.group(1)}")
    for match in re.finditer(r"(?im)^gate_path\s*=\s*(\w+)\b", text):
        tokens.add(f"gate_path={match.group(1).upper()}")
        tokens.add(match.group(1).upper())
    for match in re.finditer(r"(?im)^You are the (G[1246])\b", text):
        tokens.add(match.group(1).upper())
    if re.search(r"(?im)^Genre:\s*adversarial\s+spec\b", text):
        tokens.add("adversarial-spec")
    if re.search(r"(?im)^job\s*=\s*delivery-review\b", text):
        tokens.add("delivery-review")
    return tokens


def bindable_target_hints() -> tuple[str, ...]:
    """Generic bind hints aligned with ``body_target_tokens`` (a:38299 review)."""
    return _BINDABLE_TARGET_HINTS


def _truncate_line_repr(line: str, *, limit: int = 80) -> str:
    text = line if len(line) <= limit else line[: limit - 3] + "..."
    return repr(text)


def missing_report_sections(report_body: str) -> list[str]:
    """Return required heading labels absent from the report sidecar body."""
    body = report_body or ""
    return [h for h in _REQUIRED_REPORT_HEADINGS if h not in body]


def _section_body_after(text: str, heading: str) -> str | None:
    """Slice after *heading* up to the next ATX heading of the same or higher level.

    Sub-headings stay inside the section so per-scope tables under ``###`` are
    not treated as an empty required section (a:37914).
    """
    idx = text.find(heading)
    if idx < 0:
        return None
    level = len(heading) - len(heading.lstrip("#"))
    after = text[idx + len(heading) :]
    for match in _HEADING_MARKERS.finditer(after):
        if len(match.group(1)) <= level:
            return after[: match.start()]
    return after


def empty_report_sections(report_body: str) -> list[str]:
    """Return required headings whose section body has no non-blank content (A3).

    A section ends at the next ATX heading of the same or higher level, so a
    ``###`` under ``##`` stays in-section (a:37914).
    """
    text = report_body or ""
    empty: list[str] = []
    for heading in _REQUIRED_REPORT_HEADINGS:
        section = _section_body_after(text, heading)
        if section is None:
            continue
        if not section.strip():
            empty.append(heading)
    return empty


def resolve_cortex_uri(uri: str) -> Path:
    """Map ``cortex://`` URI to an on-disk path under CORTEX_FILES_ROOT."""
    raw = (uri or "").strip()
    if not raw.startswith("cortex://"):
        raise NestedCdpPromptGateError(
            f"retrieval_report must be cortex://, got {raw!r}",
            code="nested_cdp_retrieval_report_scheme",
        )
    rel = raw.removeprefix("cortex://").lstrip("/")
    root = cortex_files_root().resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise NestedCdpPromptGateError(
            f"retrieval_report escapes CORTEX_FILES_ROOT: {raw!r}",
            code="nested_cdp_retrieval_report_escape",
        )
    return path


def load_retrieval_report(uri: str) -> str:
    """Read report sidecar text; refuse when missing or empty."""
    path = resolve_cortex_uri(uri)
    if not path.is_file():
        raise NestedCdpPromptGateError(
            f"retrieval_report not found: {uri}",
            code="nested_cdp_retrieval_report_missing",
        )
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise NestedCdpPromptGateError(
            f"retrieval_report empty: {uri}",
            code="nested_cdp_retrieval_report_empty",
        )
    return text


def enforce_retrieval_report(author_body: str) -> str:
    """Require a cited, target-bound, non-empty retrieval report when width-shaped.

    Returns the cited URI when validation passes.
    """
    if not is_nested_width_shaped(author_body):
        return ""
    uri = parse_retrieval_report_uri(author_body)
    if not uri:
        raise NestedCdpPromptGateError(
            "conductor nested CDP width prompt requires "
            "`retrieval_report: cortex://…` citing queries + yields + "
            "choice-to-evidence (a:37183)",
            code="nested_cdp_retrieval_report_required",
        )
    report = load_retrieval_report(uri)
    missing = missing_report_sections(report)
    if missing:
        raise NestedCdpPromptGateError(
            f"retrieval_report {uri} missing sections: {', '.join(missing)}",
            code="nested_cdp_retrieval_report_incomplete",
        )
    empty = empty_report_sections(report)
    if empty:
        raise NestedCdpPromptGateError(
            f"retrieval_report {uri} empty sections: {', '.join(empty)}",
            code="nested_cdp_retrieval_report_empty_section",
        )
    state, target, raw_line = _first_target_binding(report)
    allowed = body_target_tokens(author_body)
    accepted = sorted(allowed)
    if state == "invalid":
        line_bit = _truncate_line_repr(raw_line or "")
        hint = "; ".join(bindable_target_hints())
        raise NestedCdpPromptGateError(
            f"retrieval_report {uri} malformed `target:` line {line_bit} "
            f"(column-0, one token, no indent/bullet); author body accepts "
            f"{accepted!r}; bind shapes: {hint} (a:38299)",
            code="nested_cdp_retrieval_report_target_invalid",
        )
    if state == "absent":
        raise NestedCdpPromptGateError(
            f"retrieval_report {uri} requires `target:` binding the prompt "
            "(todo:… / G4 / gate_path=… / adversarial-spec) (a:37183 A3)",
            code="nested_cdp_retrieval_report_target_required",
        )
    assert target is not None
    allowed_folded = {token.casefold() for token in allowed}
    if target.casefold() not in allowed_folded:
        if not allowed:
            raise NestedCdpPromptGateError(
                f"retrieval_report {uri} saw target token {target!r}; author body "
                "declares no bindable token; add todo:<id>/friction:<n> or a "
                "line-start gate declaration to the author body (a:38299)",
                code="nested_cdp_retrieval_report_target_mismatch",
            )
        raise NestedCdpPromptGateError(
            f"retrieval_report {uri} saw target token {target!r}; author body "
            f"accepts {accepted!r} (a:37183 A3)",
            code="nested_cdp_retrieval_report_target_mismatch",
        )
    return uri


def enforce_skeptic_chrome_refuse(*, author_body: str, body: str) -> None:
    """Refuse when a SKEPTIC *author* body carries delivery chrome in ``body``."""
    if is_skeptic_shaped(author_body) and has_delivery_review_chrome(body):
        raise NestedCdpPromptGateError(
            "SKEPTIC / adversarial-spec prompt must not open with "
            "delivery/code-review chrome "
            "('packet carries the code under review' / checkout-pytest waive) "
            "(a:37183)",
            code="nested_cdp_skeptic_chrome",
        )


def hop_prompts_missing_report_bundles(prompt_bodies: list[str]) -> list[int]:
    """Closeout helper: indices of nested-width bodies lacking a valid report.

    Does not raise — returns 0-based indices so conductor closeout can list them.
    """
    bad: list[int] = []
    for idx, body in enumerate(prompt_bodies):
        if not is_nested_width_shaped(body):
            continue
        try:
            enforce_retrieval_report(body)
        except NestedCdpPromptGateError:
            bad.append(idx)
    return bad


def enforce_nested_cdp_prompt_gates(
    *,
    body: str,
    author_body: str | None = None,
    purpose: str | None = None,
) -> None:
    """Run chrome + retrieval-report gates for CDP staging / follow-up admit.

    Shape detection uses ``author_body`` when provided (pre-skill-prepend text).
    Chrome scan uses the staged/pasted ``body`` (may include leftover charter).
    """
    del purpose  # body/author shape is the load-bearing signal (specimen a:37183)
    src = author_body if author_body is not None else body
    enforce_skeptic_chrome_refuse(author_body=src, body=body)
    if is_nested_width_shaped(src):
        enforce_retrieval_report(src)
