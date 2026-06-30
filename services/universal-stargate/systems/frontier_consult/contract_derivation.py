"""F1 contract derivation for team handoff admission (Step 1 v2)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from agent_seat.profiles import load_roles
from agent_seat.registry import normalize_agent_slug

from .executor_resolution import (
    derive_recommended_executor,  # noqa: F401 — packet surface
)
from .handoff import _resolve_packet_file

# F6 — explicit map; do not substring-match "implement" on lane names.
_DISPATCH_LANE_TO_CONTRACT: dict[str, str] = {
    "cursor-implement": "implement",
    "cursor-mechanical": "implement",
    "web-spec": "consult",
    "web-implement-packet": "consult",
    "operator-gate": "consult",
    "none": "consult",
}

_CONTRACT_YAML = re.compile(
    r"^contract:\s*(implement|consult)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_CONTRACT_MARKDOWN = re.compile(
    r"^\*\*Contract:\*\*\s*(?:bound\s+)?(implement|consult)\b",
    re.MULTILINE | re.IGNORECASE,
)


class CortexReader(Protocol):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


def contract_from_dispatch_lane(lane: str) -> str | None:
    """Map a dispatch_lane value to consult|implement, or None if unknown."""
    return _DISPATCH_LANE_TO_CONTRACT.get(lane.strip())


def _contract_from_packet_text(text: str) -> str | None:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            match = _CONTRACT_YAML.search(fm)
            if match:
                return match.group(1).lower()
    match = _CONTRACT_MARKDOWN.search(text)
    if match:
        return match.group(1).lower()
    return None


def contract_from_role(role: str) -> tuple[str, str] | None:
    canonical = normalize_agent_slug(role)
    role_profile = load_roles().get(canonical)
    if role_profile is not None and role_profile.default_contract is not None:
        return role_profile.default_contract, "role_default"
    return None


def derive_contract(
    *,
    explicit_contract: str | None = None,
    source_ref: str | None,
    packet_path: str | None,
    role: str | None,
    cortex: CortexReader,
    workspaces_root: Path,
) -> tuple[str, str]:
    """Derive handoff contract per F1 order + role fallback for roster-only handoffs.

    Order:
      0. explicit ``contract`` request param (MCP / route body)
      1. ``source_ref`` → entity ``dispatch_lane`` via explicit map
      2. packet front-matter ``contract:`` (YAML or ``**Contract:**`` line)
      3. roster ``role`` → ``role_default`` (legacy path without grounded source)
      4. default ``consult``
    """
    if explicit_contract is not None:
        return explicit_contract, "explicit_param"

    if source_ref:
        try:
            entity = cortex.entity_get(source_ref, intent="full")
        except Exception:
            entity = None
        if entity:
            lane = (entity.get("attributes") or {}).get("dispatch_lane")
            if isinstance(lane, str):
                mapped = contract_from_dispatch_lane(lane)
                if mapped is not None:
                    return mapped, "source_ref_dispatch_lane"

    if packet_path:
        candidate = _resolve_packet_file(workspaces_root.resolve(), packet_path)
        if candidate is not None:
            text = candidate.read_text(encoding="utf-8", errors="replace")
            from_packet = _contract_from_packet_text(text)
            if from_packet is not None:
                return from_packet, "packet_frontmatter"

    if role:
        from_role = contract_from_role(role)
        if from_role is not None:
            return from_role

    return "consult", "default"
