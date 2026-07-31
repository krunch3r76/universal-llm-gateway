"""Table-driven tests for the executor_recommendation object builder."""

from __future__ import annotations

from cursor_capabilities import DESCRIPTOR_VERSION
from dispatch_knob_policy import build_executor_recommendation


def test_mechanical_recommended_on_opus_target() -> None:
    obj = build_executor_recommendation(
        contract="light-bounded",
        target_surface="claude-cursor",
        target_model="claude-opus-4-8",
    )
    assert obj["schema_version"] == "1"
    assert obj["advisory"] is True
    assert obj["status"] == "recommended"
    assert obj["knobs"] == {
        "model": "composer-2.5",
        "thinking": "false",
        "effort": "low",
    }
    assert obj["validation"]["status"] == "valid"
    assert obj["validation"]["descriptor"] == f"libs/cursor_capabilities@{DESCRIPTOR_VERSION}"
    assert obj["override_allowed"] is True


def test_fork_e_axes_independent() -> None:
    # {effort: low} must NOT imply thinking; both axes present independently.
    obj = build_executor_recommendation(
        contract="pure-mechanical",
        target_surface="claude-cursor",
        target_model="claude-opus-4-8",
    )
    knobs = obj["knobs"]
    assert "effort" in knobs and "thinking" in knobs
    assert knobs["effort"] == "low"
    assert knobs["thinking"] == "false"
    # Independent keys, not derived from one another.
    assert knobs["thinking"] is not None
    assert knobs["effort"] is not None


def test_none_for_implement_contract() -> None:
    obj = build_executor_recommendation(
        contract="implement",
        target_surface="claude-cursor",
        target_model="claude-opus-4-8",
    )
    assert obj["status"] == "none"
    assert obj["knobs"] == {"model": None, "thinking": None, "effort": None}
    assert obj["validation"]["status"] == "skipped"
    assert obj["validation"]["warnings"] == ["no_recommendation_to_validate"]


def test_partial_when_target_lacks_axes() -> None:
    # composer-2.5's descriptor supports only `fast`; effort/thinking unsupported.
    obj = build_executor_recommendation(
        contract="light-bounded",
        target_surface="cursor-sdk",
        target_model="composer-2.5",
    )
    assert obj["status"] == "partial"
    # Policy intent preserved at top level...
    assert obj["knobs"]["effort"] == "low"
    assert obj["knobs"]["thinking"] == "false"
    # ...resolved axes nulled, clamp-not-silent.
    assert obj["validation"]["status"] == "partial"
    assert obj["validation"]["normalized_knobs"]["effort"] is None
    assert obj["validation"]["normalized_knobs"]["thinking"] is None
    assert obj["validation"]["warnings"]  # non-empty


def test_container_always_present() -> None:
    for contract in ("light-bounded", "pure-mechanical", "implement", "consult"):
        obj = build_executor_recommendation(
            contract=contract,
            target_surface="claude-cursor",
            target_model="claude-opus-4-8",
        )
        assert obj["schema_version"] == "1"
        assert obj["status"] in {"recommended", "none", "partial"}
        assert "knobs" in obj and set(obj["knobs"]) == {"model", "thinking", "effort"}
