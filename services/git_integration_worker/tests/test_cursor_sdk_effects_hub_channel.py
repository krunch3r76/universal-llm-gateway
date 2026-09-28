"""a:36739 — hub-land paths must flow into closeout effects[]."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    merge_landed_hub_effects_paths,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)


def _init_repo(path: Path) -> str:
    subprocess.run(["git", "init", "-b", "master"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    return (
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


def test_merge_landed_hub_effects_paths_appends_commit_paths(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_repo(hub)
    rel = "services/hub_land.py"
    (hub / rel).parent.mkdir(parents=True, exist_ok=True)
    (hub / rel).write_text("x=1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(hub), "add", rel], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(hub), "commit", "-m", "land"],
        check=True,
        capture_output=True,
    )
    tip = subprocess.run(
        ["git", "-C", str(hub), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    merged = merge_landed_hub_effects_paths(
        (),
        hub_repo=hub,
        head_sha=tip,
        landed=True,
    )
    assert rel in merged


def test_closeout_effects_include_hub_land_when_repo_change_set_empty(
    tmp_path: Path,
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    _init_repo(hub)
    rel = "libs/effects_channel.py"
    (hub / rel).parent.mkdir(parents=True, exist_ok=True)
    (hub / rel).write_text("y=2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(hub), "add", rel], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(hub), "commit", "-m", "hub land"],
        check=True,
        capture_output=True,
    )
    tip = subprocess.run(
        ["git", "-C", str(hub), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    body = build_implement_closeout_body(
        dispatch_id="effects-hub-channel",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=10,
            tool_call_count=1,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("effects-hub-channel"),
        result_bytes=4,
        thread_id="13146",
        work_item_ref="todo:effects-channel",
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        hub_repo=hub,
        head_sha=tip,
        landed=True,
    )
    payload = json.loads(body)
    assert rel in payload["effects"]
    assert len(payload["effects"]) >= 1
