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


def _score_arm_a(
    prompt: dict[str, Any],
    golden: dict[str, Any] | None,
    response: dict[str, Any],
) -> dict[str, float]:
    metrics = {
        "correct_op_selection": 0.0,
        "id_resolution_accuracy": 1.0,
        "unwanted_write_rate": 0.0,
        "reject_validity": 1.0,
    }
    if golden is None:
        return metrics

    if golden.get("expected_reject"):
        codes = [r.get("code") for r in response.get("rejects", [])]
        metrics["correct_op_selection"] = (
            1.0 if golden["expected_reject"] in codes else 0.0
        )
        metrics["reject_validity"] = metrics["correct_op_selection"]
        metrics["correct_op_selection"] = (
            1.0
            if not response.get("op_plan") and metrics["reject_validity"] == 1.0
            else 0.0
        )
        return metrics

    if golden.get("expect_candidates"):
        metrics["reject_validity"] = 1.0 if response.get("candidates") else 0.0
        metrics["correct_op_selection"] = metrics["reject_validity"]
        return metrics

    expected_ops = golden.get("expected_ops") or []
    actual_ops = [e.get("op") for e in response.get("op_plan", [])]
    if not expected_ops:
        return metrics
    hits = sum(1 for op in expected_ops if op in actual_ops)
    metrics["correct_op_selection"] = hits / len(expected_ops)
    return metrics


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
        golden = goldens.get(pid)
        patch = (golden or {}).get("patch") or {
            "@context": "cortex.life/v1",
            "@graph": [{"@id": "todo:ship", "noted": prompt["text"][:80]}],
        }
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


def _run_arm_b_stub(
    prompts: list[dict[str, Any]],
    tmp_dir: Path,
) -> dict[str, Any]:
    """Arm B tmp-copy bootstrap — megatool comparator scaffold (no live writes)."""
    template = tmp_dir / "template.db"
    materialize_head_schema_template(template)
    copy_path = tmp_dir / "arm_b.db"
    copy_template_db(template, copy_path)
    conn = open_migrated_connection(copy_path)
    _seed_corpus_db(conn)
    ent_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()

    return {
        "arm": "B",
        "prompt_count": len(prompts),
        "correct_op_selection": 0.0,
        "id_resolution_accuracy": 0.0,
        "unwanted_write_rate": 0.0,
        "reject_validity": 0.0,
        "db_path": str(copy_path),
        "entity_count": ent_count,
        "note": "Arm B megatool trace scoring deferred to lead blinded review",
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

        report = {
            "version": prompts_data.get("version", "1"),
            "prompt_count": len(prompts),
            "arms": {
                "A": _run_arm_a(prompts, goldens, arm_a_db),
                "B": _run_arm_b_stub(prompts, tmp_dir),
            },
        }
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"written": str(args.output), "prompt_count": len(prompts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
