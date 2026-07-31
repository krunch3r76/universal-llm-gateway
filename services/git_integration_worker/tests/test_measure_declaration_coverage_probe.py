"""Unit tests for attribution §7 falsifier 1 prospective probe."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.measure_declaration_coverage_probe import (
    ground_truth_paths,
    measure,
    snapshot_dir,
)

pytestmark = pytest.mark.offline


def test_toy_ground_truth_and_declaration_sets(tmp_path: Path) -> None:
    pre = tmp_path / "pre"
    post = tmp_path / "post"
    pre.mkdir()
    post.mkdir()
    (pre / "a.txt").write_text("one\n", encoding="utf-8")
    (pre / "b.txt").write_text("same\n", encoding="utf-8")
    (post / "a.txt").write_text("two\n", encoding="utf-8")
    (post / "b.txt").write_text("same\n", encoding="utf-8")
    (post / "c.txt").write_text("new\n", encoding="utf-8")

    g = ground_truth_paths(snapshot_dir(pre), snapshot_dir(post))
    assert g == {"a.txt", "c.txt"}

    stats = measure(ground_truth=g, declared={"a.txt", "d.txt"})
    assert stats["|G|"] == 2
    assert stats["|D|"] == 2
    assert stats["|G\\D|"] == 1
    assert stats["|D\\G|"] == 1


def test_empty_ground_truth_exits_nonzero_via_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.measure_declaration_coverage_probe import main

    pre = tmp_path / "pre.json"
    post = tmp_path / "post.json"
    pre.write_text(json.dumps({"x": "1"}), encoding="utf-8")
    post.write_text(json.dumps({"x": "1"}), encoding="utf-8")
    code = main(["--pre-hashes", str(pre), "--post-hashes", str(post)])
    assert code == 2
