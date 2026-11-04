"""G3 S1/S5 — hop-admit 422 normalization and probe-failed sidecar."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from closeout_memo.models import NEXT_RE
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    maybe_write_hop_admit_probe_failed_sidecar,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_watchdog import (
    _admit_error_permanent,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    merge_hop_admit_error,
    merge_hop_patch,
)

_PROBE_FAILED = (
    "worker thread '22119' may still hold a live dispatch; "
    "GIW occupancy probe failed — refuse second top-level generate"
)
_LIVE_HOLDER = (
    "worker thread '22119' already holds live dispatch "
    "'b548d643a1d1-eabd9b96'; refuse second top-level generate 'hop-admit-x'"
)


def test_probe_failed_422_normalizes_retryable_true_not_permanent_after_one() -> None:
    merged = merge_hop_admit_error(
        None,
        {
            "last_error": _PROBE_FAILED,
            "last_status_code": 422,
        },
    )
    assert merged["retryable"] is True
    assert merged["attempts"] == 1
    record_json = merge_hop_patch("", {"hop_admit_error": merged})
    assert _admit_error_permanent({"record_json": record_json}) is False


def test_live_holder_422_overrides_prior_retryable_true_on_merge() -> None:
    prior = merge_hop_admit_error(
        None,
        {"last_error": _PROBE_FAILED, "last_status_code": 422},
    )
    assert prior["retryable"] is True
    merged = merge_hop_admit_error(
        prior,
        {"last_error": _LIVE_HOLDER, "last_status_code": 422},
    )
    assert merged["retryable"] is False
    assert merged["attempts"] == 2


def test_occupancy_probe_http_marker_is_retryable() -> None:
    err = (
        "worker thread '22144' occupancy probe HTTP 503; "
        "refuse second top-level generate"
    )
    merged = merge_hop_admit_error(None, {"last_error": err, "last_status_code": 422})
    assert merged["retryable"] is True


def test_write_repo_sidecar_uses_hop_admit_prefix_and_leaves_memo_sidecar_ref(
    tmp_path: Path,
) -> None:
    conductor_id = "9e95cbd2a1d1-eabd9b96"
    existing_sidecar = f"workspaces://repo/.cursor/closeout/{conductor_id}.md"
    row = {
        "dispatch_id": conductor_id,
        "source_repo": str(tmp_path),
        "record_json": json.dumps(
            {
                "memo_closeout": {
                    "sidecar_ref": existing_sidecar,
                    "turn": "latest",
                }
            }
        ),
    }
    last_error = _PROBE_FAILED
    with patch(
        "services.git_integration_worker.cursor_sdk_deliverables.write_repo_sidecar"
    ) as write_mock:
        maybe_write_hop_admit_probe_failed_sidecar(
            row,
            last_error=last_error,
            status_code=422,
        )
    write_mock.assert_called_once()
    repo, sidecar_id, body = write_mock.call_args[0]
    assert repo == tmp_path
    assert sidecar_id == f"hop-admit-{conductor_id}"
    assert sidecar_id != conductor_id
    assert "422" in body
    assert "GIW occupancy probe failed" in body or "occupancy probe" in body
    memo = json.loads(row["record_json"])["memo_closeout"]
    assert memo["sidecar_ref"] == existing_sidecar
    assert NEXT_RE.fullmatch("none")
