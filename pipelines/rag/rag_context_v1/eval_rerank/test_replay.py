"""Hermetic check that the frozen replay still prefers the shipped order."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR.parent))

from eval_rerank.replay import (  # noqa: E402
    fuse_floor,
    fuse_minmax,
    fuse_no_floor,
    fuse_rrf,
    legacy_apply_bounded_movement,
    ndcg_at,
    position_greedy_movement,
    precision_at,
    prior_order,
)


def _load():
    return json.loads((_DIR / "fixture.json").read_text())


def _eligible(queries):
    return [
        q
        for q in queries
        if len(q["chunks"]) >= 2 and all(c["ce_score"] is not None for c in q["chunks"])
    ]


def _grades(query, order):
    by_id = {c["chunk_id"]: c for c in query["chunks"]}
    return [int(by_id[cid]["relevance"]) for cid in order]


def _move(query, mover, finals):
    prior = prior_order(query["chunks"])
    ids = [c["chunk_id"] for c in prior]
    return mover(ids, finals, 3, {cid: "high" for cid in ids})


def test_fixture_covers_scopes_and_grades() -> None:
    queries = _load()
    assert len(queries) >= 24
    scopes = {q["scope"] or "default" for q in queries}
    assert len(scopes) >= 5
    for query in queries:
        for chunk in query["chunks"]:
            assert chunk["relevance"] in (0, 1, 2)
            assert "@" not in chunk["excerpt"]


def test_position_greedy_beats_live_order_on_graded_replay() -> None:
    queries = _eligible(_load())
    live_ndcg, greedy_ndcg = [], []
    live_p3, greedy_p3 = [], []
    for query in queries:
        finals = {c["chunk_id"]: float(c["final_score"]) for c in query["chunks"]}
        live = [c["chunk_id"] for c in sorted(query["chunks"], key=lambda c: c["rank"])]
        greedy = _move(query, position_greedy_movement, finals)
        live_ndcg.append(ndcg_at(_grades(query, live), 5))
        greedy_ndcg.append(ndcg_at(_grades(query, greedy), 5))
        live_p3.append(precision_at(_grades(query, live), 3))
        greedy_p3.append(precision_at(_grades(query, greedy), 3))
    assert sum(greedy_ndcg) / len(greedy_ndcg) > sum(live_ndcg) / len(live_ndcg)
    assert sum(greedy_p3) / len(greedy_p3) >= sum(live_p3) / len(live_p3)


def test_current_fusion_beats_the_alternatives() -> None:
    queries = _eligible(_load())
    scores = {name: [] for name in ("floor", "no_floor", "minmax", "rrf")}
    fns = {
        "floor": fuse_floor,
        "no_floor": fuse_no_floor,
        "minmax": fuse_minmax,
        "rrf": fuse_rrf,
    }
    for query in queries:
        prior = prior_order(query["chunks"])
        priors = [float(c["prior_score"]) for c in prior]
        ce = [float(c["ce_score"]) for c in prior]
        ids = [c["chunk_id"] for c in prior]
        for name, fn in fns.items():
            fused = fn(priors, ce)
            finals = {cid: fused[i] for i, cid in enumerate(ids)}
            order = position_greedy_movement(ids, finals, 3, {cid: "high" for cid in ids})
            scores[name].append(ndcg_at(_grades(query, order), 5))
    floor = sum(scores["floor"]) / len(queries)
    for name in ("no_floor", "minmax", "rrf"):
        assert floor > sum(scores[name]) / len(queries)


def test_legacy_walker_is_not_the_shipped_order() -> None:
    """The copied cb67d209 walker still disagrees with position-greedy on this set."""
    queries = _eligible(_load())
    differ = 0
    for query in queries:
        finals = {c["chunk_id"]: float(c["final_score"]) for c in query["chunks"]}
        if _move(query, legacy_apply_bounded_movement, finals) != _move(
            query, position_greedy_movement, finals
        ):
            differ += 1
    assert differ > 0
