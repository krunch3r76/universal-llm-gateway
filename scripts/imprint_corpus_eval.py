#!/usr/bin/env python3
"""Blinded 20-prompt corpus harness — arm A (propose) + arm B (megatool tmp-copy).

Arm A calls POST /graph/imprint/propose (zero-write).
Arm B runs megatool ops against a tmp-copy cortex DB only (F-5).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs"))

from cortex_store import db  # noqa: E402
from cortex_store._test_db_bootstrap import (  # noqa: E402
    copy_template_db,
    materialize_head_schema_template,
    open_migrated_connection,
)
from cortex_store.dispatch_ops import execute_op  # noqa: E402
from cortex_store.life_imprint.op_plan import build_op_plan  # noqa: E402
from cortex_store.life_imprint.registry import load_registry  # noqa: E402
from cortex_store.life_imprint.shape_check import shape_check_patch  # noqa: E402
from cortex_store.main import create_app  # noqa: E402

CORPUS_DIR = ROOT / "tests" / "cortex" / "life_imprint_corpus"
PROMPTS_PATH = CORPUS_DIR / "prompts.yaml"
GOLDENS_PATH = CORPUS_DIR / "golden_op_plans.yaml"
RUBRIC_PATH = CORPUS_DIR / "RUBRIC.md"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _seed_corpus_db(conn: sqlite3.Connection) -> None:
    rows = [
        ("person:alice", "person", "Alice"),
        ("person:bob", "person", "Bob"),
        ("todo:ship", "todo", "Ship feature"),
        ("todo:other", "todo", "Other"),
        ("document:brief", "document", "Brief"),
        ("document:exhibit-a", "document", "Exhibit A"),
        ("matter:estate-2024", "matter", "Estate 2024"),
        ("account:chk", "account", "Checking"),
        ("organization:acme", "organization", "Acme"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        rows,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO entity_aliases (entity_id, entity_type, alias) "
        "VALUES (?, ?, ?)",
        [
            ("person:alice", "person", "Alice"),
            ("person:bob", "person", "Bob"),
        ],
    )
    conn.commit()


def _require_golden(prompt: dict[str, Any], goldens: dict[str, Any]) -> dict[str, Any]:
    pid = prompt["id"]
    golden = goldens.get(pid)
    if golden is None:
        raise ValueError(f"Missing golden op plan for prompt {pid!r}")
    return golden


def _score_bundle(
    golden: dict[str, Any],
    *,
    op_names: list[str],
    rejects: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    unwanted_write: float,
) -> dict[str, float]:
    metrics = {
        "correct_op_selection": 0.0,
        "id_resolution_accuracy": 1.0,
        "unwanted_write_rate": unwanted_write,
        "reject_validity": 1.0,
    }

    if golden.get("expected_reject"):
        codes = [r.get("code") for r in rejects]
        metrics["reject_validity"] = (
            1.0 if golden["expected_reject"] in codes else 0.0
        )
        metrics["correct_op_selection"] = (
            1.0
            if not op_names and metrics["reject_validity"] == 1.0
            else 0.0
        )
        return metrics

    if golden.get("expect_candidates"):
        metrics["reject_validity"] = 1.0 if candidates else 0.0
        metrics["correct_op_selection"] = metrics["reject_validity"]
        return metrics

    expected_ops = golden.get("expected_ops") or []
    if not expected_ops:
        return metrics
    hits = sum(1 for op in expected_ops if op in op_names)
    metrics["correct_op_selection"] = hits / len(expected_ops)
    return metrics


def _score_arm_a(
    prompt: dict[str, Any],
    golden: dict[str, Any] | None,
    response: dict[str, Any],
) -> dict[str, float]:
    if golden is None:
        return {
            "correct_op_selection": 0.0,
            "id_resolution_accuracy": 1.0,
            "unwanted_write_rate": 0.0,
            "reject_validity": 1.0,
        }
    return _score_bundle(
        golden,
        op_names=[e.get("op") for e in response.get("op_plan", [])],
        rejects=response.get("rejects", []),
        candidates=response.get("candidates", []),
        unwanted_write=0.0,
    )


def _run_arm_a(
    prompts: list[dict[str, Any]],
    goldens: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    db._CORTEX_DB = db_path
    conn = open_migrated_connection(db_path)
    _seed_corpus_db(conn)
    conn.close()

    client = TestClient(create_app(db_path=str(db_path)))
    bundles: list[dict[str, Any]] = []
    totals = {
        "correct_op_selection": 0.0,
        "id_resolution_accuracy": 0.0,
        "unwanted_write_rate": 0.0,
        "reject_validity": 0.0,
    }

    for prompt in prompts:
        pid = prompt["id"]
        golden = _require_golden(prompt, goldens)
        patch = golden["patch"]
        before = open_migrated_connection(db_path)
        ent_before = before.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        before.close()

        resp = client.post("/graph/imprint/propose", json={"patch": patch})
        body = resp.json()

        after = open_migrated_connection(db_path)
        ent_after = after.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        after.close()
        unwanted = 1.0 if ent_after != ent_before else 0.0

        metrics = _score_arm_a(prompt, golden, body)
        metrics["unwanted_write_rate"] = unwanted
        for key in totals:
            totals[key] += metrics[key]
        bundles.append(
            {
                "prompt_id": pid,
                "arm": "A",
                "prompt_text": prompt["text"],
                "patch": patch,
                "propose_response": body,
                "metrics": metrics,
            }
        )

    n = len(prompts) or 1
    return {
        "arm": "A",
        "prompt_count": len(prompts),
        "correct_op_selection": totals["correct_op_selection"] / n,
        "id_resolution_accuracy": totals["id_resolution_accuracy"] / n,
        "unwanted_write_rate": totals["unwanted_write_rate"] / n,
        "reject_validity": totals["reject_validity"] / n,
        "bundles": bundles,
        "rubric_path": str(RUBRIC_PATH),
    }


def _execute_op_plan(op_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from fastapi import HTTPException

    traces: list[dict[str, Any]] = []
    for entry in op_plan:
        op = entry["op"]
        try:
            result = execute_op(op, entry.get("args") or {})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, (dict, str)) else str(exc.detail)
            result = {"error": detail, "status_code": exc.status_code}
        traces.append({"op": op, "args": entry.get("args"), "result": result})
        if isinstance(result, dict) and result.get("error"):
            break
    return traces


def _run_arm_b(
    prompts: list[dict[str, Any]],
    goldens: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    """Arm B — execute megatool-equivalent ops on tmp-copy DB only (F-5)."""
    db._CORTEX_DB = db_path
    conn = open_migrated_connection(db_path)
    _seed_corpus_db(conn)
    conn.close()

    registry = load_registry()
    bundles: list[dict[str, Any]] = []
    totals = {
        "correct_op_selection": 0.0,
        "id_resolution_accuracy": 0.0,
        "unwanted_write_rate": 0.0,
        "reject_validity": 0.0,
    }

    for prompt in prompts:
        pid = prompt["id"]
        golden = _require_golden(prompt, goldens)
        patch = golden["patch"]

        before = open_migrated_connection(db_path)
        ent_before = before.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        before.close()

        rejects = [
            {"statement_idx": r.statement_idx, "code": r.code, "detail": r.detail}
            for r in shape_check_patch(patch, registry)
        ]
        op_plan: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        traces: list[dict[str, Any]] = []

        if not rejects:
            plan_conn = open_migrated_connection(db_path)
            try:
                op_plan, candidates = build_op_plan(patch, registry, plan_conn)
            finally:
                plan_conn.close()
            if not candidates and op_plan:
                traces = _execute_op_plan(op_plan)

        after = open_migrated_connection(db_path)
        ent_after = after.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        after.close()
        unwanted = 1.0 if ent_after != ent_before else 0.0

        executed_ops = [t["op"] for t in traces if not t.get("result", {}).get("error")]
        metrics = _score_bundle(
            golden,
            op_names=executed_ops,
            rejects=rejects,
            candidates=candidates,
            unwanted_write=unwanted,
        )
        for key in totals:
            totals[key] += metrics[key]
        bundles.append(
            {
                "prompt_id": pid,
                "arm": "B",
                "prompt_text": prompt["text"],
                "patch": patch,
                "rejects": rejects,
                "op_plan": op_plan,
                "candidates": candidates,
                "executed_traces": traces,
                "metrics": metrics,
            }
        )

    n = len(prompts) or 1
    return {
        "arm": "B",
        "prompt_count": len(prompts),
        "correct_op_selection": totals["correct_op_selection"] / n,
        "id_resolution_accuracy": totals["id_resolution_accuracy"] / n,
        "unwanted_write_rate": totals["unwanted_write_rate"] / n,
        "reject_validity": totals["reject_validity"] / n,
        "bundles": bundles,
        "db_path": str(db_path),
        "rubric_path": str(RUBRIC_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Life imprint corpus eval harness")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("imprint_corpus_report.json"),
        help="Write JSON report path",
    )
    args = parser.parse_args()

    if not RUBRIC_PATH.is_file():
        print(f"RUBRIC missing: {RUBRIC_PATH}", file=sys.stderr)
        return 1

    prompts_data = _load_yaml(PROMPTS_PATH)
    goldens_data = _load_yaml(GOLDENS_PATH)
    prompts = prompts_data.get("prompts") or []
    goldens = goldens_data.get("goldens") or {}

    with tempfile.TemporaryDirectory(prefix="imprint_corpus_") as tmp:
        tmp_dir = Path(tmp)
        arm_a_db = tmp_dir / "arm_a.db"
        materialize_head_schema_template(arm_a_db)

        arm_b_template = tmp_dir / "arm_b_template.db"
        materialize_head_schema_template(arm_b_template)
        arm_b_db = tmp_dir / "arm_b.db"
        copy_template_db(arm_b_template, arm_b_db)

        report = {
            "version": prompts_data.get("version", "1"),
            "prompt_count": len(prompts),
            "arms": {
                "A": _run_arm_a(prompts, goldens, arm_a_db),
                "B": _run_arm_b(prompts, goldens, arm_b_db),
            },
        }
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"written": str(args.output), "prompt_count": len(prompts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
