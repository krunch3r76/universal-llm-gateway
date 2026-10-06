"""Prompt-turn anchor for anchored CDP harvest (marker + pre-send match count)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

def _has_alnum(text: str) -> bool:
    return any(ch.isalnum() for ch in text)
_UNIQUE_LINE_RE = re.compile(r"#\d+-unique:\s*\S+")

USER_TURN_SELECTORS: tuple[str, ...] = (
    '[data-testid="user-message"]',
    '[data-testid="human-turn"]',
    'div[class*="font-user"]',
)


def typed_prompt_body(prompt: str) -> str:
    """Body text actually typed after sealed skill channels are peeled."""
    from claude_bundles.cowork_skill_delivery import parse_cdp_sealed_skill_channels

    _attach, _inline, rest = parse_cdp_sealed_skill_channels(prompt)
    return rest


def anchor_marker(prompt: str) -> str:
    """Marker substring matched in committed user turns for this prompt."""
    from claude_bundles.composer_submit import verification_marker

    marker = verification_marker(typed_prompt_body(prompt))
    if not marker or not _has_alnum(marker):
        raise ValueError("anchor marker has no letters or digits")
    return marker


def unique_anchor(prompt: str) -> "ReplyAnchor":
    """Anchor for a prompt whose first line is ``#N-unique:…``."""
    match = _UNIQUE_LINE_RE.search(prompt or "")
    if not match:
        raise ValueError("prompt lacks #N-unique: line for unique_anchor")
    return ReplyAnchor(marker=match.group(0), prior_matches=0)


@dataclass(frozen=True)
class ReplyAnchor:
    marker: str
    prior_matches: int = 0

    def js_args(self) -> dict[str, object]:
        return {
            "anchorMarker": self.marker,
            "priorMatches": self.prior_matches,
            "userSelectors": list(USER_TURN_SELECTORS),
        }

    def after(self, pre_state: dict) -> ReplyAnchor:
        prior = int(pre_state.get("anchor_matches") or 0)
        return replace(self, prior_matches=prior)
