"""Purpose-keyed CDP ``skills=`` floors (Fable leverage bind B2).

Caller ``skills=`` stays additive. Unknown / omitted purpose keeps the
judgment-only floor so existing callers do not grow an arch pair.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from claude_bundles.chat_model_match import normalize_picker_request

CDP_PURPOSE_PROFILES: dict[str, tuple[str, ...]] = {
    "ask": (
        "architecture-invariants",
        "ulg-architecture",
        "reasoning-posture",
        "hypothesize-simulate",
    ),
    "review": (
        "reasoning-posture",
        "consult-posture",
        "hypothesize-simulate",
    ),
    "produce": ("reasoning-posture",),
    "mission": (
        "cdp-operator-proxy",
        "lean-dispatch",
        "reasoning-posture",
        "hypothesize-simulate",
    ),
    "operator-proxy": (
        "cdp-operator-proxy",
        "lean-dispatch",
        "reasoning-posture",
        "hypothesize-simulate",
    ),
}

_DEFAULT_FLOOR: tuple[str, ...] = ("reasoning-posture",)

REVIEW_CONTRACT_SKILL_FLOOR = frozenset(
    {"code-review", "delivery-review", "check-review"}
)

_REVIEW_ARCH_PAIR: tuple[str, ...] = ("architecture-invariants", "ulg-architecture")
_PRODUCTION_ROOT = frozenset({"services", "libs"})
_REPO_PREFIX = re.compile(
    r"^(?:workspaces://)?universal-llm-gateway/",
    re.IGNORECASE,
)
_FILES_EXPECTED_HDR = re.compile(
    r"^\s*(?:[-*]|\d+\.)?\s*(?:\*\*)?[\"']?files_expected[\"']?"
    r"(?:\s*\([^)]*\))?\s*(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)
_LIST_CONTINUATION = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")
_PATH_UNDER_PRODUCTION = re.compile(
    r"(?:^|[\s,;`(])((?:services|libs)/[^\s,;|`\"']+)",
    re.IGNORECASE,
)
_NA_VALUES = frozenset({"n/a", "none", "n/a.", "none."})


def _normalize_ship_path(raw: str) -> str:
    p = str(raw).strip().replace("\\", "/")
    if not p:
        return ""
    p = _REPO_PREFIX.sub("", p)
    if "://" in p:
        _scheme, remainder = p.split("://", 1)
        if "/" in remainder:
            p = remainder.split("/", 1)[1]
        else:
            p = remainder
        p = _REPO_PREFIX.sub("", p)
    return p


def _paths_from_fragment(fragment: str) -> list[str]:
    text = fragment.strip()
    if not text:
        return []
    lowered = text.lower().rstrip(".")
    if lowered in _NA_VALUES or lowered.startswith(("n/a ", "none ")):
        return []
    paths: list[str] = []
    paths.extend(re.findall(r"`([^`]+)`", text))
    for chunk in re.split(r",\s*", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        paths.extend(_PATH_UNDER_PRODUCTION.findall(chunk))
    if not paths:
        paths.extend(_PATH_UNDER_PRODUCTION.findall(text))
    return paths


def service_dirs(files_expected: Iterable[str] | None) -> frozenset[str]:
    """First two segments for paths under ``services/`` or ``libs/``.

    ``services/mcp-server/foo`` → ``services/mcp-server``;
    ``services/x/tests/t.py`` → ``services/x`` (tests do not add a segment).
    """
    if not files_expected:
        return frozenset()
    out: set[str] = set()
    for raw in files_expected:
        p = _normalize_ship_path(str(raw))
        if not p:
            continue
        parts = [seg for seg in p.split("/") if seg]
        if len(parts) < 3:
            continue
        if parts[0].lower() in _PRODUCTION_ROOT:
            out.add(f"{parts[0]}/{parts[1]}")
    return frozenset(out)


def parse_files_expected_from_prompt(packet_text: str | None) -> list[str]:
    """Collect production paths from ``files_expected:`` lines in a packet body."""
    if not packet_text or not packet_text.strip():
        return []
    paths: list[str] = []
    in_block = False
    for line in packet_text.splitlines():
        header = _FILES_EXPECTED_HDR.match(line)
        if header:
            tail = header.group(1).strip()
            extracted = _paths_from_fragment(tail) if tail else []
            if extracted:
                paths.extend(extracted)
                in_block = False
            elif tail and tail.lower().rstrip(".") in _NA_VALUES:
                in_block = False
            else:
                in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if not stripped:
                in_block = False
                continue
            if _LIST_CONTINUATION.match(line):
                paths.extend(_paths_from_fragment(line))
            else:
                in_block = False
    return paths


def _merge_review_arch_floor(review_floor: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    merged: list[str] = []
    for slug in _REVIEW_ARCH_PAIR + review_floor:
        key = slug.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(slug)
    return tuple(merged)


def infer_cdp_purpose(purpose: str | None, model: str | None) -> str:
    """Explicit purpose wins; omitted + Sonnet → produce; else ask."""
    raw = (purpose or "").strip()
    if raw:
        return raw
    picker = normalize_picker_request(model or "").lower()
    if picker.startswith("sonnet"):
        return "produce"
    return "ask"


def profile_slugs_for_purpose(
    purpose: str | None,
    *,
    contract: str | None = None,
    files_expected: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Return the staging floor for a purpose tag.

    ``None`` / blank / unknown → judgment skill only, except review-class
    ``contract`` without ``purpose`` (K30 — ``leg.purpose`` stays unset).
    Multi-service ``files_expected`` on that path prepends the arch pair.
    """
    key = (purpose or "").strip().lower()
    if key:
        return CDP_PURPOSE_PROFILES.get(key, _DEFAULT_FLOOR)
    contract_key = (contract or "").strip().lower()
    if contract_key in REVIEW_CONTRACT_SKILL_FLOOR:
        review_floor = CDP_PURPOSE_PROFILES["review"]
        if len(service_dirs(files_expected)) > 1:
            return _merge_review_arch_floor(review_floor)
        return review_floor
    return _DEFAULT_FLOOR


def profile_slugs_for_session(session: str | None) -> tuple[str, ...]:
    """Chip floor from the ``session`` field.

    Omission is the judgment floor. A prompt that quotes ``purpose`` as
    ``operator-proxy`` is not a session and does not select
    ``cdp-operator-proxy``.
    """
    return profile_slugs_for_purpose(session)
