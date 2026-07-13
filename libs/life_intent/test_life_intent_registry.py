"""Registry load and fail-closed validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from life_intent.registry import load_registry

_VOCAB_PATH = Path(__file__).resolve().parents[2] / "config/cortex/life_intent_v1.yaml"


def test_registry_loads_head_vocab() -> None:
    reg = load_registry()
    assert reg.context_id == "cortex.life-intent/v1"
    assert set(reg.verbs.keys()) == {"investigate", "fix", "build", "change"}
    assert reg.verbs["investigate"].creates_work_item is False
    assert reg.verbs["fix"].creates_work_item is True
    assert "team_dispatch" in reg.refuse_list


def test_registry_lane_mapping() -> None:
    reg = load_registry()
    assert reg.verbs["investigate"].lane == "recon"
    assert reg.verbs["fix"].lane == "bug_recon"
    assert reg.verbs["build"].lane == "feature_recon"
    assert reg.verbs["change"].lane == "change_recon"


def test_registry_fail_closed_missing_verb_field(tmp_path: Path) -> None:
    data = yaml.safe_load(_VOCAB_PATH.read_text())
    del data["verbs"]["investigate"]["lane"]
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.dump(data))
    with pytest.raises(ValueError, match="lane"):
        load_registry(path)


def test_registry_fail_closed_missing_version(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("verbs: {}\nrefuse_list: [dispatch]\n")
    with pytest.raises(ValueError, match="version"):
        load_registry(path)


def test_render_verb_enum() -> None:
    reg = load_registry()
    assert reg.render_verb_enum() == ["build", "change", "fix", "investigate"]
