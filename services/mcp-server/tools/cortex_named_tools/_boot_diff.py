"""Diff two inspect-mode boot results by artifact content.

Inline content (briefing_card) is compared against canonicalized text where
ISO-8601 timestamps are masked. The operational_context artifact is
compared via sha256/bytes metadata from injected_artifacts — its inline
emission was retired (file-on-disk is the contract for both LIVE and
INSPECT). All other non-inline artifacts likewise use sha256/bytes
because raw text is not present in inspect responses for those entries.
"""

from __future__ import annotations

import re
from typing import Any

_ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{0,2})?"
)


def _canonicalize_for_diff(text: str | None) -> str:
    """Mask time-volatile substrings so semantic-equal content compares equal."""
    if not text:
        return ""
    return _ISO_TIMESTAMP_RE.sub("<TIMESTAMP>", text)


def _build_boot_diff(
    primary: dict[str, Any], secondary: dict[str, Any]
) -> dict[str, Any]:
    """Compare two inspect-mode boot payloads and return artifact-level deltas."""
    primary_artifacts = {a["name"]: a for a in primary.get("injected_artifacts", [])}
    secondary_artifacts = {
        a["name"]: a for a in secondary.get("injected_artifacts", [])
    }

    only_in_primary = sorted(set(primary_artifacts) - set(secondary_artifacts))
    only_in_secondary = sorted(set(secondary_artifacts) - set(primary_artifacts))

    primary_card = _canonicalize_for_diff(primary.get("briefing_card"))
    secondary_card = _canonicalize_for_diff(secondary.get("briefing_card"))

    deltas: list[dict[str, Any]] = []
    if primary_card != secondary_card:
        deltas.append(
            {
                "name": "briefing_card",
                "kind": "inline_canonical_text",
                "primary_canonical_bytes": len(primary_card.encode("utf-8")),
                "secondary_canonical_bytes": len(secondary_card.encode("utf-8")),
            }
        )

    # operational_context falls into the sha256_mismatch path along with the
    # other non-inline artifacts; its inline emission was retired alongside
    # the boot bandwidth trim.
    inline_names = {"briefing_card"}
    shared_non_inline = sorted(
        (set(primary_artifacts) & set(secondary_artifacts)) - inline_names
    )
    for name in shared_non_inline:
        primary_artifact = primary_artifacts[name]
        secondary_artifact = secondary_artifacts[name]
        if primary_artifact["sha256"] != secondary_artifact["sha256"]:
            deltas.append(
                {
                    "name": name,
                    "kind": "sha256_mismatch",
                    "primary_bytes": primary_artifact["bytes"],
                    "secondary_bytes": secondary_artifact["bytes"],
                    "primary_sha256": primary_artifact["sha256"][:12],
                    "secondary_sha256": secondary_artifact["sha256"][:12],
                }
            )

    return {
        "artifacts_only_in_primary": only_in_primary,
        "artifacts_only_in_secondary": only_in_secondary,
        "artifacts_with_delta": deltas,
        "primary_total_bytes": sum(
            a.get("bytes", 0) for a in primary_artifacts.values()
        ),
        "secondary_total_bytes": sum(
            a.get("bytes", 0) for a in secondary_artifacts.values()
        ),
    }
