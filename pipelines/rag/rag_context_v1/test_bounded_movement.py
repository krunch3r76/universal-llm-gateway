"""Bounded-movement order: permutation, cap, and no improving adjacent swap.

Covers both caller regimes of ``apply_bounded_movement``: cross-encoder
(every confidence ``high``, so the cap is ``max_movement``) and generative
(medium confidence caps movement at 2).
"""

from __future__ import annotations

import importlib.util
import random
import sys
import types
from pathlib import Path

_PARENT = Path(__file__).resolve().parent


def _load_scoring():
    pkg_name = "_bounded_movement_under_test"
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
apply_bounded_movement = _scoring.apply_bounded_movement


def _chunk(cid: str, score: float) -> dict:
    return {
        "content": cid,
        "source": f"{cid}.txt",
        "indexed_at": "t",
        "metadata": {},
        "content_hash": cid + "deadbeef",
        "score": score,
    }


def _fuse(priors: list[float], ce: list[float], prior_weight: float = 0.70) -> list[float]:
    """Current cross-encoder fusion, including the 1.0 prior floor."""
    max_prior = max(1.0, max(priors, default=0.0))
    max_ce = max(abs(s) for s in ce) or 1.0
    return [
        prior_weight * (p / max_prior) + (1 - prior_weight) * (c / max_ce)
        for p, c in zip(priors, ce)
    ]


def _run(
    finals: list[float],
    *,
    max_movement: int = 3,
    confidence: dict[str, str] | None = None,
) -> list[int]:
    chunks = [_chunk(f"{i:02d}aaaaaa", 0.0) for i in range(len(finals))]
    scores = {c["content_hash"][:8]: finals[i] for i, c in enumerate(chunks)}
    if confidence is None:
        confidence = {cid: "high" for cid in scores}
    ordered = apply_bounded_movement(chunks, scores, max_movement, confidence)
    by_id = {c["content_hash"][:8]: i for i, c in enumerate(chunks)}
    return [by_id[c["content_hash"][:8]] for c in ordered]


def _caps(n: int, max_movement: int, confidence: dict[int, str]) -> list[int]:
    caps: list[int] = []
    for i in range(n):
        conf = confidence.get(i, "high")
        caps.append(max_movement if conf == "high" else min(max_movement, 2))
    return caps


def _assert_properties(
    order: list[int],
    finals: list[float],
    caps: list[int],
) -> None:
    n = len(finals)
    assert sorted(order) == list(range(n))
    pos = {idx: p for p, idx in enumerate(order)}
    for i in range(n):
        assert abs(pos[i] - i) <= caps[i]
    for p in range(n - 1):
        left, right = order[p], order[p + 1]
        if finals[left] >= finals[right]:
            continue
        # An adjacent swap would improve score order. It must break a cap.
        swapped = dict(pos)
        swapped[left], swapped[right] = pos[right], pos[left]
        assert abs(swapped[left] - left) > caps[left] or abs(swapped[right] - right) > caps[right]


def test_synthetic_case_does_not_rank_low_final_first() -> None:
    """The brief's synthetic priors/ce must not put index 3 above index 4."""
    priors = [0.20, 0.19, 0.18, 0.17, 0.05, 0.04, 0.03, 0.02]
    ce = [0.001, 0.002, 0.003, 0.004, 0.60, 0.55, 0.50, 0.45]
    finals = _fuse(priors, ce)
    order = _run(finals)
    _assert_properties(order, finals, [3] * 8)
    assert order[0] != 3
    assert order.index(4) < order.index(3)
    # Best legal occupant of slot 0, then index 4 at its earliest legal slot.
    assert order == [0, 4, 5, 6, 1, 2, 3, 7]


def test_live_specimen_rs_6d478ae1a917() -> None:
    """Replay of rs-6d478ae1a917 finals: the lowest chunk must not take rank 1."""
    ids = [
        "01da2123",
        "6343dc19",
        "9357a0cd",
        "eae1fece",
        "f3fcc003",
        "831d8235",
        "30cac0c9",
        "ebd20d0f",
        "0982a30b",
        "e018ad11",
    ]
    finals = [0.1656, 0.2727, 0.2149, 0.2299, 0.0712, 0.2514, 0.3167, 0.165, 0.1112, 0.2324]
    chunks = []
    for cid, final in zip(ids, finals):
        chunks.append(
            {
                "content": cid,
                "source": f"{cid}.txt",
                "indexed_at": "t",
                "metadata": {},
                "content_hash": cid + "cafef00d",
                "score": 0.0,
            }
        )
        assert chunks[-1]["content_hash"][:8] == cid
    confidence = {cid: "high" for cid in ids}
    scores = {cid: final for cid, final in zip(ids, finals)}
    ordered = apply_bounded_movement(chunks, scores, 3, confidence)
    got = [c["content_hash"][:8] for c in ordered]
    assert got[0] != "f3fcc003"
    assert got[0] == "6343dc19"
    pos = {cid: i for i, cid in enumerate(got)}
    for i, cid in enumerate(ids):
        assert abs(pos[cid] - i) <= 3
    _assert_properties(
        [ids.index(cid) for cid in got],
        finals,
        [3] * 10,
    )


def test_cross_encoder_caller_all_high_confidence() -> None:
    """Cross-encoder passes confidence high for every candidate (cap = k)."""
    finals = [0.1, 0.2, 0.9, 0.3, 0.8]
    order = _run(finals, max_movement=3, confidence=None)
    _assert_properties(order, finals, [3] * 5)


def test_generative_caller_medium_confidence_caps_at_two() -> None:
    """Generative medium confidence must not move a chunk by 3."""
    finals = [0.01, 0.02, 0.03, 0.04, 0.95, 0.90]
    chunks = [_chunk(f"{i:02d}bbbbbb", 0.0) for i in range(len(finals))]
    scores = {c["content_hash"][:8]: finals[i] for i, c in enumerate(chunks)}
    confidence = {cid: "medium" for cid in scores}
    ordered = apply_bounded_movement(chunks, scores, 3, confidence)
    by_id = {c["content_hash"][:8]: i for i, c in enumerate(chunks)}
    order = [by_id[c["content_hash"][:8]] for c in ordered]
    _assert_properties(order, finals, [2] * 6)


def test_random_orders_are_cap_feasible_local_optima() -> None:
    rng = random.Random(20260930)
    for n in range(2, 9):
        for _ in range(30):
            finals = [rng.random() for _ in range(n)]
            conf_by_index = {
                i: rng.choice(["high", "medium"]) for i in range(n)
            }
            chunks = [_chunk(f"{i:02d}cccccc", 0.0) for i in range(n)]
            scores = {c["content_hash"][:8]: finals[i] for i, c in enumerate(chunks)}
            confidence = {
                c["content_hash"][:8]: conf_by_index[i] for i, c in enumerate(chunks)
            }
            ordered = apply_bounded_movement(chunks, scores, 3, confidence)
            by_id = {c["content_hash"][:8]: i for i, c in enumerate(chunks)}
            order = [by_id[c["content_hash"][:8]] for c in ordered]
            caps = [
                3 if conf_by_index[i] == "high" else 2 for i in range(n)
            ]
            _assert_properties(order, finals, caps)
