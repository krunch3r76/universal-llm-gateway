"""Fast knob display parity for list-active-cursor-sdk-dispatches."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE = Path(__file__).resolve().parent / "list-active-cursor-sdk-dispatches.py"
_spec = importlib.util.spec_from_file_location("list_active_cursor_sdk_dispatches", _MODULE)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

_fast_from_rec = _mod._fast_from_rec
_fast_token = _mod._fast_token
_effort_from_rec = _mod._effort_from_rec
_effort_token = _mod._effort_token


def test_fast_from_model_knobs_true() -> None:
    assert _fast_from_rec({"model_knobs": {"fast": "true"}}) is True
    assert _fast_token(True) == " fast"


def test_fast_from_model_knobs_false() -> None:
    assert _fast_from_rec({"model_knobs": {"fast": "false"}}) is False
    assert _fast_token(False) == " fast=no"


def test_fast_absent_no_suffix() -> None:
    assert _fast_from_rec({}) is None
    assert _fast_from_rec({"model_knobs": {}}) is None
    assert _fast_token(None) == ""


def test_fast_wire_slug_without_knob_unknown() -> None:
    rec = {"model": "composer-2.5-fast", "resolved_model": "composer-2.5-fast"}
    assert _fast_from_rec(rec) is None
    assert _fast_token(_fast_from_rec(rec)) == ""


def test_fast_prefers_model_knobs_over_requested() -> None:
    rec = {
        "model_knobs": {"fast": "true"},
        "model_knobs_requested": {"fast": "false"},
    }
    assert _fast_from_rec(rec) is True


def test_fast_falls_back_to_model_knobs_requested() -> None:
    assert _fast_from_rec({"model_knobs_requested": {"fast": "false"}}) is False


def test_fast_ignores_non_bool_strings() -> None:
    assert _fast_from_rec({"model_knobs": {"fast": "1"}}) is None
    assert _fast_from_rec({"model_knobs": {"fast": True}}) is None


def test_effort_from_model_knobs() -> None:
    assert _effort_from_rec({"model_knobs": {"effort": "high"}}) == "high"
    assert _effort_token("high") == " effort=high"


def test_effort_absent_no_suffix() -> None:
    assert _effort_from_rec({}) is None
    assert _effort_token(None) == ""


def test_effort_falls_back_to_model_knobs_requested() -> None:
    assert _effort_from_rec({"model_knobs_requested": {"effort": "low"}}) == "low"
