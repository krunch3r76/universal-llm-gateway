"""Uniform param-contract tests (todo:fs-dispatch-param-contract-audit).

validate_op_params is the single chokepoint that generalizes the assertion-21250
fix: every confusable selector an op does NOT consume must 422 instead of being
silently ignored, and every destructive-default selector must be required.
"""

from __future__ import annotations

import pytest

from tools.filesystem._fs_dispatch import (
    CONTRACT_PARAMS,
    OP_CONSUMES,
    OP_REQUIRES,
    validate_op_params,
)

_DEFAULTS: dict[str, object] = {
    "target": "",
    "section": "",
    "line": 0,
    "heading": "",
    "level": 0,
    "position": "",
    "all_occurrences": False,
}

# A non-default value per contract param, for the "provided" assertions.
_PROVIDED: dict[str, object] = {
    "target": "x",
    "section": "Heading",
    "line": 3,
    "heading": "New",
    "level": 2,
    "position": "after",
    "all_occurrences": True,
}


def _values(**overrides: object) -> dict[str, object]:
    return {**_DEFAULTS, **overrides}


def test_unknown_op_is_not_policed() -> None:
    """Unknown ops are left to the dispatcher (returns None, not an error)."""
    assert validate_op_params("definitely_not_an_op", _values(target="x")) is None


@pytest.mark.parametrize("op", sorted(OP_CONSUMES))
def test_default_values_always_pass_consumption_check(op: str) -> None:
    """All-default selectors never trip the unconsumed-param rule."""
    err = validate_op_params(op, _values())
    # Only ops with required selectors may error here (missing required).
    if op not in OP_REQUIRES:
        assert err is None, f"{op} rejected all-default values: {err}"


@pytest.mark.parametrize("op", sorted(OP_CONSUMES))
def test_unconsumed_meaningful_param_rejected(op: str) -> None:
    """Any confusable param the op does not consume must 422 self-describingly."""
    consumes = OP_CONSUMES[op]
    required = OP_REQUIRES.get(op, frozenset())
    for param in CONTRACT_PARAMS:
        if param in consumes:
            continue
        values = _values(**{param: _PROVIDED[param]})
        # Satisfy any required selectors so we isolate the unconsumed-param path.
        for req in required:
            values[req] = _PROVIDED[req]
        err = validate_op_params(op, values)
        assert err is not None and param in err["error"], (
            f"op={op!r} silently accepted unconsumed param {param!r}: {err}"
        )


@pytest.mark.parametrize(
    ("op", "param"),
    sorted((op, p) for op, ps in OP_REQUIRES.items() for p in ps),
)
def test_destructive_selector_required(op: str, param: str) -> None:
    """Destructive-default selectors must 422 when left at their default."""
    values = _values()
    for req in OP_REQUIRES[op]:
        if req != param:
            values[req] = _PROVIDED[req]
    err = validate_op_params(op, values)
    assert err is not None and param in err["error"], (
        f"op={op!r} accepted missing required selector {param!r}: {err}"
    )


def test_valid_calls_pass() -> None:
    """Representative well-formed calls produce no contract error."""
    assert validate_op_params("replace", _values(target="old")) is None
    assert validate_op_params("md_replace", _values(section="Intro")) is None
    assert validate_op_params("md_read", _values(section="Intro")) is None
    assert validate_op_params("md_read", _values()) is None  # empty == full doc
    assert validate_op_params("move", _values(target="b.md")) is None
    assert (
        validate_op_params("md_insert", _values(heading="New", level=2, position="end"))
        is None
    )


def test_md_target_still_rejected() -> None:
    """The original 21250 case: target on a markdown op is rejected."""
    err = validate_op_params("md_replace", _values(section="Intro", target="old"))
    assert err is not None and "target" in err["error"]
