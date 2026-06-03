#!/usr/bin/env python3
"""Apply ratified reconstruct disposition manifest (thread 1210).

Loads per-bucket assertion ids from manifest JSON; re-snapshots live non-fenced
Bucket-G staged rows; applies ONLY manifest ∩ live. Confidence never written (1172-C).
Default dry-run; --live PATCHes via cortex HTTP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from reconstruct_constants import MARKER  # noqa: E402
from reconstruct_disposition_agm_dismiss import (  # noqa: E402
    assert_not_staged_only_filter,
)
from reconstruct_disposition_fenced_review import (  # noqa: E402
    BUCKET_G_FENCED_SQL,
    is_fenced,
)
from reconstruct_disposition_nonfenced_bucket_g import (  # noqa: E402
    is_non_fenced_target,
)
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

BUCKETS = ("graduate", "keep_staged_flag", "dismiss_reject_marker", "supersede")
STAGED_MARKER_COUNT_SQL = """
SELECT COUNT(*) FROM assertions
WHERE superseded_by IS NULL
  AND review_status = 'staged'
  AND reviewer = ?
"""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in BUCKETS:
        if key not in data.get("dispositions", {}):
            raise ValueError(f"manifest missing dispositions.{key}")
    return data


def load_live_nonfenced(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(BUCKET_G_FENCED_SQL, (MARKER,)).fetchall()]
    return [
        r
        for r in rows
        if not is_fenced(str(r["entity_id"]))
        and is_non_fenced_target(str(r["entity_id"]))
    ]


def bucket_manifest_ids(data: dict[str, Any]) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for key in BUCKETS:
        ids = data["dispositions"][key].get("assertion_ids") or []
        out[key] = {int(i) for i in ids}
    return out


def staged_marker_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute(STAGED_MARKER_COUNT_SQL, (MARKER,)).fetchone()[0])


def now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def patch_graduate(client: Any, aid: int, review_notes: str) -> None:
    body = {
        "review_status": "committed",
        "reviewer": MARKER,
        "reviewed_at": now_iso(),
        "review_notes": review_notes,
    }
    r = client.patch(f"/assertions/{aid}", json=body)
    r.raise_for_status()


def patch_keep_staged(client: Any, aid: int, review_notes: str) -> None:
    body = {
        "reviewer": MARKER,
        "reviewed_at": now_iso(),
        "review_notes": review_notes,
    }
    r = client.patch(f"/assertions/{aid}", json=body)
    r.raise_for_status()


def run(
    *,
    db_path: Path,
    manifest_path: Path,
    live: bool,
    expect_sha256: str | None,
) -> dict[str, Any]:
    manifest_sha = sha256_file(manifest_path)
    if expect_sha256 and manifest_sha != expect_sha256:
        raise SystemExit(
            f"manifest sha256 mismatch: got {manifest_sha}, expected {expect_sha256}"
        )

    data = load_manifest(manifest_path)
    manifest_by_bucket = bucket_manifest_ids(data)
    grad_note = str(data["dispositions"]["graduate"]["review_note"])
    keep_note = str(data["dispositions"]["keep_staged_flag"]["review_note"])

    conn = sqlite3.connect(db_path)
    staged_before = staged_marker_count(conn)
    live_rows = load_live_nonfenced(conn)
    live_ids = {int(r["id"]) for r in live_rows}
    assert_not_staged_only_filter(conn, len(live_rows))

    manifest_union = set().union(*manifest_by_bucket.values())
    drift = {
        "manifest_only": sorted(manifest_union - live_ids),
        "live_only": sorted(live_ids - manifest_union),
        "live_snapshot_count": len(live_ids),
        "manifest_union_count": len(manifest_union),
    }

    apply_sets: dict[str, list[int]] = {}
    would_apply: dict[str, int] = {}
    for key in BUCKETS:
        intersect = sorted(manifest_by_bucket[key] & live_ids)
        apply_sets[key] = intersect
        would_apply[key] = len(intersect)

    applied: dict[str, int] = {k: 0 for k in BUCKETS}
    errors: list[dict[str, Any]] = []

    if live:
        to_grad = apply_sets["graduate"]
        to_keep = apply_sets["keep_staged_flag"]
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=120.0) as client:
            for aid in to_grad:
                try:
                    patch_graduate(client, aid, grad_note)
                    applied["graduate"] += 1
                except Exception as exc:
                    errors.append(
                        {"bucket": "graduate", "assertion_id": aid, "error": str(exc)}
                    )
            for aid in to_keep:
                try:
                    patch_keep_staged(client, aid, keep_note)
                    applied["keep_staged_flag"] += 1
                except Exception as exc:
                    errors.append(
                        {
                            "bucket": "keep_staged_flag",
                            "assertion_id": aid,
                            "error": str(exc),
                        }
                    )

    staged_after = staged_marker_count(conn) if live else staged_before
    conn.close()

    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "live": live,
        "would_apply": would_apply,
        "applied": applied if live else {k: 0 for k in BUCKETS},
        "drift": drift,
        "errors": errors,
        "staged_marker_count_before": staged_before,
        "staged_marker_count_after": staged_after if live else None,
        "staged_marker_delta": (staged_after - staged_before) if live else None,
        "project_boe19p_11150_in_keep_intersection": 11150
        in apply_sets["keep_staged_flag"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply reconstruct disposition manifest (1210 step-2)"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".cortex" / "cortex.db"),
    )
    parser.add_argument(
        "--expect-sha256", default=None, help="Abort if manifest hash differs"
    )
    parser.add_argument(
        "--live", action="store_true", help="PATCH cortex (default dry-run)"
    )
    args = parser.parse_args()

    summary = run(
        db_path=Path(args.db),
        manifest_path=args.manifest,
        live=args.live,
        expect_sha256=args.expect_sha256,
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
