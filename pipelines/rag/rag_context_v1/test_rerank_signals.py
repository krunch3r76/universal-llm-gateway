"""Rerank status vocabulary: small sets, window failures, and mode default."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PARENT = Path(__file__).resolve().parent


def _load_scoring():
    pkg_name = "_rerank_signals_under_test"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_PARENT)]
    pkg.__package__ = pkg_name
    sys.modules[pkg_name] = pkg
    for name in ("context_formatting", "rerank_scoring"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{name}", _PARENT / f"{name}.py"
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = pkg_name
        sys.modules[f"{pkg_name}.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{pkg_name}.rerank_scoring"]


_scoring = _load_scoring()


def test_default_mode_is_cross_encoder() -> None:
    assert _scoring.resolve_rerank_mode({}) == "cross_encoder"
    assert _scoring.resolve_rerank_mode({"rerank_mode": "generative"}) == "generative"


def test_cross_encoder_scores_a_small_set() -> None:
    assert _scoring.rerank_skip_status(True, 3, "cross_encoder") is None
    assert _scoring.rerank_skip_status(True, 1, "cross_encoder") is None
    assert _scoring.rerank_skip_status(True, 3, "generative") == "skipped_small_set"
    assert _scoring.rerank_skip_status(False, 10, "cross_encoder") == "disabled"
    assert _scoring.rerank_skip_status(True, 0, "cross_encoder") == "disabled"


def test_generative_window_failures_are_named() -> None:
    assert _scoring.generative_rerank_status(3, 0) == "ok"
    assert _scoring.generative_rerank_status(3, 1) == "partial_window_failures:1"
    assert _scoring.generative_rerank_status(3, 3) == "error"


def test_annotate_keeps_weak_match_and_names_the_basis() -> None:
    summary = _scoring.relevance_summary([])
    annotated = _scoring.annotate_relevance(
        summary,
        rerank_status="skipped_small_set",
        weak_match_basis="none",
    )
    assert annotated["weak_match"] is None
    assert annotated["weak_match_basis"] == "none"
    assert annotated["rerank_status"] == "skipped_small_set"
    assert summary.get("rerank_status") is None


def test_mismatch_basis_is_none_even_when_error_is_set() -> None:
    annotated = _scoring.annotate_relevance(
        {"chunks": [], "weak_match": None},
        rerank_status="fallback_score_count_mismatch",
        weak_match_basis="none",
        rerank_error="score_count_mismatch",
    )
    assert annotated["rerank_error"] == "score_count_mismatch"
    assert annotated["weak_match_basis"] == "none"
    assert annotated["weak_match"] is None
