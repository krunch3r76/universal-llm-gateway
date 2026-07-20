"""Review-surface classification for light-bounded generate-lane packet AC observer."""

from __future__ import annotations

import re
from typing import Literal

ReviewSurface = Literal["sidecar", "source"]

SELF_CHECK_NON_AUTHORITY_LINE = (
    "Executor Self-check PASS is evidence to inspect, not completion authority."
)

_DEFAULT_NEGATIVE_SPACE = (
    "Per new/changed param or branch: where invalid + does the "
    "spec reject it there with a test?"
)

_PLANNING_NEGATIVE_SPACE = (
    "Sidecar-scoped packet: per AC, cite sidecar section/quote + sha256; "
    "source/tests N/A. Where would this AC fail against the sidecar bind?"
)

_SOURCE_FOOTER = (
    "## Packet AC observer (advisory)\n"
    "Verify each packet acceptance criterion and Self-check claim against the "
    "resulting source and tests.\n"
    f"{SELF_CHECK_NON_AUTHORITY_LINE}\n"
    "Report PASS or FAIL per packet AC with file evidence paths; mark sources "
    "missing or unverifiable explicitly.\n"
    "Treat the staged draft and reasoning trace as the primary implementation "
    "surface; request packet/source reference when unavailable."
)

_PLANNING_FOOTER = (
    "## Packet AC observer (advisory)\n"
    "Verify each packet acceptance criterion against the durable sidecar "
    "(URI + sha256 read-back).\n"
    "Mark source: N/A and tests: N/A when acceptance surface is sidecar-only; "
    "do not FAIL for missing diffs.\n"
    f"{SELF_CHECK_NON_AUTHORITY_LINE}\n"
    "Report PASS or FAIL per packet AC with sidecar evidence paths.\n"
    "Treat the staged draft and reasoning trace as the primary review "
    "surface; request sidecar/packet reference when unavailable."
)

FOOTER_BY_SURFACE: dict[ReviewSurface, str] = {
    "source": _SOURCE_FOOTER,
    "sidecar": _PLANNING_FOOTER,
}

NEGATIVE_SPACE_BY_SURFACE: dict[ReviewSurface, str] = {
    "source": _DEFAULT_NEGATIVE_SPACE,
    "sidecar": _PLANNING_NEGATIVE_SPACE,
}

_ACCEPTANCE_SECTIONS = ("scope", "task_guidance", "output_format", "mcp_capabilities")
_DURABLE_URI = re.compile(r"(?:cortex|workspaces)://[^\s<>\"'`]+")
_HALT_MARKERS = (
    "stop after",
    "¬ implement",
    "not implement",
    "stage-a only",
    "stage-a complete",
    "stop —",
    "stop -",
    "halt-without-implement",
    "halts before implement",
    "¬ stage-b",
    "no implement",
)


def _extract_section(packet_text: str, tag: str) -> str:
    pattern = rf"<{tag}>(.*?)</{tag}>"
    match = re.search(pattern, packet_text, re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else ""


def _collect_files_expected_paths(packet_text: str) -> list[str]:
    paths: list[str] = []
    for section in _ACCEPTANCE_SECTIONS:
        body = _extract_section(packet_text, section)
        if not body:
            continue
        paths.extend(re.findall(r"`([^`]+)`", body))
        for line in body.splitlines():
            lowered = line.lower()
            if "files_expected" not in lowered and "files expected" not in lowered:
                continue
            paths.extend(re.findall(r"`([^`]+)`", line))
            paths.extend(
                re.findall(
                    r"(?:cortex|workspaces)://[^\s,;|`\"']+",
                    line,
                    flags=re.IGNORECASE,
                )
            )
            paths.extend(
                re.findall(
                    r"(?:^|[\s,;`-])((?:services|libs)/[^\s,;|`\"']+)",
                    line,
                    flags=re.IGNORECASE,
                )
            )
    return paths


def _is_production_ship_path(path: str) -> bool:
    normalized = path.strip()
    if not normalized:
        return False
    if re.search(r"workspaces://[^/]+/(?:services|libs)/", normalized, re.IGNORECASE):
        return True
    if re.search(r"(?:^|[/\\])services/", normalized):
        return True
    if re.search(r"(?:^|[/\\])libs/", normalized):
        return True
    return False


def _has_durable_deliverable_and_halt(packet_text: str) -> bool:
    combined = "\n".join(
        _extract_section(packet_text, section) for section in _ACCEPTANCE_SECTIONS[1:]
    )
    if not _DURABLE_URI.search(combined):
        return False
    lowered = combined.lower()
    return any(marker in lowered for marker in _HALT_MARKERS)


def packet_has_production_files_expected(packet_text: str | None) -> bool:
    """True when files_expected includes a production ship path under services/libs."""
    if not packet_text or not packet_text.strip():
        return False
    paths = _collect_files_expected_paths(packet_text)
    return any(_is_production_ship_path(path) for path in paths)


def has_durable_deliverable_and_halt(packet_text: str | None) -> bool:
    """True when acceptance sections cite a durable URI and explicit HALT markers."""
    if not packet_text or not packet_text.strip():
        return False
    return _has_durable_deliverable_and_halt(packet_text)


def classify_review_surface(packet_text: str) -> ReviewSurface:
    """Classify packet acceptance surface for generate-lane AC observer rubric."""
    if not packet_text or not packet_text.strip():
        return "source"

    for path in _collect_files_expected_paths(packet_text):
        if _is_production_ship_path(path):
            return "source"

    if _has_durable_deliverable_and_halt(packet_text):
        return "sidecar"

    return "source"
