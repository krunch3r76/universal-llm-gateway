"""Fail-closed gates for conductor nested CDP prompt authoring (a:37183).

Ritual ``Use retrieval-before-authoring`` claims are not proof. Admit reads
artifacts: a ``retrieval_report:`` citation on the prompt body, a report
sidecar with Queries / Yields / Choice-to-evidence, and a chrome refuse when
a SKEPTIC / adversarial-spec body opens as delivery or code-review.
"""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

# Citation line the authoring seat must leave on the fired prompt body.
_RETRIEVAL_REPORT_LINE = re.compile(
    r"(?im)^retrieval_report:\s*(cortex://\S+)\s*$"
)

# Spec-skeptic / G4 shape — delivery-review chrome is forbidden here.
_SKEPTIC_SHAPE = re.compile(
    r"(?is)(?:"
    r"\bG4\s+skeptic\b|"
    r"\bgate_path\s*=\s*SKEPTIC\b|"
    r"Genre:\s*adversarial\s+spec|"
    r"\bspec-skeptic\b|"
    r"\bYou are the G4\b"
    r")"
)

# Conductor nested CDP width seats that owe a retrieval report sidecar.
_NESTED_WIDTH_SHAPE = re.compile(
    r"(?is)(?:"
    r"\bG[1246]\s+(?:skeptic|sketcher|frame|review)\b|"
    r"\bgate_path\s*=\s*(?:SKEPTIC|SKETCH|ACTIVE)\b|"
    r"Genre:\s*adversarial\s+spec|"
    r"\bjob\s*=\s*delivery-review\b|"
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
    "Do not REJECT because you cannot check out a commit",
    "Do not REJECT because you cannot check out a commit, run pytest, or run quality_gate",
)


class NestedCdpPromptGateError(ValueError):
    """Nested CDP prompt authoring gate failure (maps to HTTP 422)."""

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def is_skeptic_shaped(text: str) -> bool:
    """True when body is a G4 / SKEPTIC / adversarial-spec prompt."""
    return bool(_SKEPTIC_SHAPE.search(text or ""))


def is_nested_width_shaped(text: str) -> bool:
    """True when body is a conductor nested CDP width prompt (or cites a report)."""
    return bool(_NESTED_WIDTH_SHAPE.search(text or ""))


def has_delivery_review_chrome(text: str) -> bool:
    """True when delivery/code-review reading charter chrome is present."""
    body = text or ""
    return any(marker in body for marker in _DELIVERY_CHROME_MARKERS)


def parse_retrieval_report_uri(text: str) -> str | None:
    """Return the first ``retrieval_report: cortex://…`` citation, else None."""
    match = _RETRIEVAL_REPORT_LINE.search(text or "")
    if match is None:
        return None
    return match.group(1).strip()


def missing_report_sections(report_body: str) -> list[str]:
    """Return required heading labels absent from the report sidecar body."""
    body = report_body or ""
    return [h for h in _REQUIRED_REPORT_HEADINGS if h not in body]


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
    if not str(path).startswith(str(root)):
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


def enforce_retrieval_report(text: str) -> str:
    """Require a cited, complete retrieval report when nested-width shaped.

    Returns the cited URI when validation passes.
    """
    if not is_nested_width_shaped(text):
        return ""
    uri = parse_retrieval_report_uri(text)
    if not uri:
        raise NestedCdpPromptGateError(
            "conductor nested CDP width prompt requires "
            "`retrieval_report: cortex://…` citing queries + yields + "
            "choice-to-evidence (a:37183)",
            code="nested_cdp_retrieval_report_required",
        )
    body = load_retrieval_report(uri)
    missing = missing_report_sections(body)
    if missing:
        raise NestedCdpPromptGateError(
            f"retrieval_report {uri} missing sections: {', '.join(missing)}",
            code="nested_cdp_retrieval_report_incomplete",
        )
    return uri


def enforce_skeptic_chrome_refuse(text: str) -> None:
    """Refuse when a SKEPTIC / adversarial-spec prompt carries delivery chrome."""
    if is_skeptic_shaped(text) and has_delivery_review_chrome(text):
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


def enforce_nested_cdp_prompt_gates(*, body: str, purpose: str | None = None) -> None:
    """Run chrome + retrieval-report gates for CDP staging admit.

    ``purpose`` is reserved for callers; chrome refuse is body-shaped (a
    purpose=review delivery charter on a skeptic body is still refuse).
    """
    del purpose  # body shape is the load-bearing signal (specimen a:37183)
    enforce_skeptic_chrome_refuse(body)
    if is_nested_width_shaped(body):
        enforce_retrieval_report(body)
