"""Build the posture-stack vision digest from the foundation MAP."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

MAP_URI = (
    "cortex://notes/system/threads/4917-posture-stack-foundation/"
    "fable-foundation-map.md"
)
_MAP_REL = MAP_URI.removeprefix("cortex://")

_PILLAR_HEADER = re.compile(r"^### Pillar (\d+)\b", re.MULTILINE)
_LAW_LINE = re.compile(r"^\*\*Law:\*\*\s*(.+)$", re.MULTILINE)
_MUST_NOT = re.compile(
    r"^- \*\*Child arcs must not re-decide:\*\*\s*(.+)$", re.MULTILINE
)
_FALSIFIER = re.compile(r"^- \*\*Falsifier if ignored:\*\*\s*(.+)$", re.MULTILINE)
_SOT_LINE = re.compile(r"^- \*\*SoT:\*\*\s*(.+)$", re.MULTILINE)
_URI_IN_TEXT = re.compile(r"(?:cortex|https?)://[^\s`>,;)]+")


class VisionPillar(BaseModel):
    """One pillar row from the foundation MAP."""

    id: str = Field(..., description="Stable pillar id, e.g. pillar-1.")
    law_verbatim: str
    must_not_redecide: list[str] = Field(default_factory=list)
    falsifier: str
    sot_uris: list[str] = Field(default_factory=list)


class VisionDigest(BaseModel):
    """Served projection of the posture-stack MAP."""

    pillars: list[VisionPillar]
    map_uri: str
    map_sha256: str
    generated_at: datetime
    stale: bool
    source: str


def resolve_map_path(files_root: Path, rel: str = _MAP_REL) -> Path:
    """Resolve MAP path under *files_root*; raise ValueError on escape."""
    root = files_root.resolve()
    abs_path = (files_root / rel).resolve()
    try:
        abs_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"map path {rel!r} resolves outside CORTEX_FILES_ROOT"
        ) from exc
    return abs_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _split_clause_list(raw: str) -> list[str]:
    """Split a MAP bullet into clauses on `; ` (bind: must_not_redecide[])."""
    parts = [part.strip() for part in raw.split(";")]
    return [part for part in parts if part]


def _extract_sot_uris(section: str) -> list[str]:
    match = _SOT_LINE.search(section)
    if not match:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for uri in _URI_IN_TEXT.findall(match.group(1)):
        if uri not in seen:
            seen.add(uri)
            ordered.append(uri)
    return ordered


def _section_before_amendment(section: str) -> str:
    marker = section.find("\n**Amendment")
    if marker == -1:
        marker = section.find("\n\n**Amendment")
    return section if marker == -1 else section[:marker]


def parse_map_pillars(text: str) -> list[VisionPillar]:
    """Parse four pillars from MAP markdown."""
    matches = list(_PILLAR_HEADER.finditer(text))
    if len(matches) < 4:
        raise ValueError(f"expected at least 4 pillars, found {len(matches)}")

    pillars: list[VisionPillar] = []
    for index, match in enumerate(matches[:4]):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = _section_before_amendment(text[start:end])

        law_match = _LAW_LINE.search(section)
        if law_match is None:
            raise ValueError(f"missing **Law:** for pillar {match.group(1)}")

        must_not_match = _MUST_NOT.search(section)
        falsifier_match = _FALSIFIER.search(section)
        pillar_num = match.group(1)
        pillars.append(
            VisionPillar(
                id=f"pillar-{pillar_num}",
                law_verbatim=law_match.group(1).strip(),
                must_not_redecide=(
                    _split_clause_list(must_not_match.group(1))
                    if must_not_match
                    else []
                ),
                falsifier=(
                    falsifier_match.group(1).strip() if falsifier_match else ""
                ),
                sot_uris=_extract_sot_uris(section),
            )
        )
    return pillars


def build_vision_digest(files_root: Path) -> VisionDigest:
    """Read the live MAP and return the typed vision digest."""
    map_path = resolve_map_path(files_root)
    if not map_path.is_file():
        raise FileNotFoundError(f"MAP not found: {MAP_URI}")

    raw = map_path.read_bytes()
    text = raw.decode("utf-8")
    return VisionDigest(
        pillars=parse_map_pillars(text),
        map_uri=MAP_URI,
        map_sha256=_sha256_file(map_path),
        generated_at=datetime.now(UTC),
        stale=False,
        source="live",
    )
