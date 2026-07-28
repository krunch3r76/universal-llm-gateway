"""Format posture-stack vision digest JSON for cortex_brief boot cards."""

from __future__ import annotations

from typing import Any

from .vision_digest import VisionDigest

_SECTION_HEADING = "## Vision digest"
_MUST_NOT_MAX = 80
_FALSIFIER_MAX = 100


def _pillar_rows(digest: VisionDigest | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(digest, VisionDigest):
        return [p.model_dump() for p in digest.pillars]
    pillars = digest.get("pillars")
    return list(pillars) if isinstance(pillars, list) else []


def _digest_fields(digest: VisionDigest | dict[str, Any]) -> tuple[str, str, str]:
    if isinstance(digest, VisionDigest):
        return digest.map_sha256, digest.map_uri, digest.source
    return (
        str(digest.get("map_sha256") or ""),
        str(digest.get("map_uri") or ""),
        str(digest.get("source") or ""),
    )


def _truncate(text: str, limit: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def format_boot_card_md(digest: VisionDigest | dict[str, Any]) -> str:
    """Render a compact ``## Vision digest`` section for the briefing card."""
    map_sha256, map_uri, source = _digest_fields(digest)
    lines: list[str] = [
        _SECTION_HEADING,
        f"**map_sha256:** `{map_sha256}` · **source:** {source}",
        f"**map:** `{map_uri}`",
    ]
    for pillar in _pillar_rows(digest):
        pid = pillar.get("id", "?")
        law = str(pillar.get("law_verbatim") or "").strip()
        if not law:
            continue
        lines.append(f"\n**{pid}** — {law}")
        must_not = pillar.get("must_not_redecide") or []
        if must_not:
            joined = "; ".join(str(item) for item in must_not)
            lines.append(f"- must not re-decide: {_truncate(joined, _MUST_NOT_MAX)}")
        falsifier = str(pillar.get("falsifier") or "").strip()
        if falsifier:
            lines.append(f"- falsifier: {_truncate(falsifier, _FALSIFIER_MAX)}")
    return "\n".join(lines)
