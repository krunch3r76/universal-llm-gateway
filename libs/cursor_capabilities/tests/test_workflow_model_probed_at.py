"""Admit-gate drift: workflow cursor defaults must carry a real probe stamp."""

from __future__ import annotations

from pathlib import Path

import yaml
from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    canonical_cursor_bare_id,
    fold_cursor_bare_id,
)

_ROUTE_POLICY = (
    Path(__file__).resolve().parents[3] / "config" / "routing" / "route_policy.yaml"
)


def test_workflow_cursor_models_have_probed_at() -> None:
    """Unpatched card: every ``workflows.*.model`` cursor id has ``probed_at``."""
    policy = yaml.safe_load(_ROUTE_POLICY.read_text(encoding="utf-8"))
    workflows = policy["workflows"]
    missing: list[str] = []
    for spec in workflows.values():
        model = spec.get("model") if isinstance(spec, dict) else None
        if not isinstance(model, str) or not model.startswith("cursor/"):
            continue
        bare = model.removeprefix("cursor/")
        cap = CURSOR_MODEL_CAPABILITIES.get(bare)
        if cap is None or cap.probed_at is None:
            missing.append(model)
    assert missing == []


def test_claude_opus_5_5_card_is_stamped() -> None:
    cap = CURSOR_MODEL_CAPABILITIES["claude-opus-5-5"]
    assert cap.probed_at is not None


def test_opus_5_5_aliases_fold_to_card_id() -> None:
    assert fold_cursor_bare_id("opus-5-5") == "claude-opus-5-5"
    assert fold_cursor_bare_id("opus-5.5") == "claude-opus-5-5"
    assert canonical_cursor_bare_id("cursor/opus-5-5") == "claude-opus-5-5"
    assert canonical_cursor_bare_id("cursor/claude-opus-5-5") == "claude-opus-5-5"
