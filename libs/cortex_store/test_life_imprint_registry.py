"""Registry load and fail-closed validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cortex_store.life_imprint.registry import load_registry, render_jsonld_context

_VOCAB_PATH = Path(__file__).resolve().parents[2] / "config/cortex/life_vocab_v1.yaml"


def test_registry_loads_head_vocab() -> None:
    reg = load_registry()
    assert reg.context_id == "cortex.life/v1"
    assert "child_of" in reg.predicates
    assert "related_to" not in reg.predicates
    assert reg.predicates["noted"].cortex_op == "assert"
    assert "delegate" in reg.refuse_list
    assert "dispatch" in reg.refuse_list


def test_registry_maps_aliases() -> None:
    reg = load_registry()
    assert reg.predicate_for_key("@type") is not None
    assert reg.predicate_for_key("@type").name == "a"


def test_registry_fail_closed_duplicate_alias(tmp_path: Path) -> None:
    data = yaml.safe_load(_VOCAB_PATH.read_text())
    data["predicates"]["typing_dup"] = {
        "class": "typing",
        "cortex_op": "entity_create",
        "aliases": ["@type"],
    }
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.dump(data))
    with pytest.raises(ValueError, match="duplicate predicate alias"):
        load_registry(path)


def test_registry_fail_closed_missing_version(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("predicates: {}\nrefuse_list: [entity_merge]\nentity_types: [todo]")
    with pytest.raises(ValueError, match="version"):
        load_registry(path)


def test_render_jsonld_context() -> None:
    reg = load_registry()
    ctx = render_jsonld_context(reg)
    assert ctx["@vocab"] == "cortex.life/v1"
    assert "child_of" in ctx
    assert "@type" in ctx
