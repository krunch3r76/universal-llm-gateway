"""Single-step handler for maestro-landed-live slice 1."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, override

from maestro_landed.manage_client import ManageClient
from maestro_landed.restart_plan import compute_dry_run
from maestro_landed.serving_map import load_serving_map
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def git_porcelain(repo_root: Path | None = None) -> str:
    root = repo_root or Path(__file__).resolve().parents[4]
    proc = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout or ""


class MaestroLandedLiveDryRunHandler(BaseHandler):
    step_type = "maestro_landed_live_dry_run_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        del step
        opts = getattr(context, "options", {}) or {}
        mode = str(opts.get("mode") or "dry_run")
        if mode != "dry_run":
            err = {"ok": False, "error": f"slice 1 supports mode=dry_run only (got {mode!r})"}
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])

        land_sha = opts.get("land_sha")
        touched = opts.get("touched_paths") or opts.get("files_modified")
        if not land_sha or not isinstance(touched, list) or not touched:
            err = {
                "ok": False,
                "error": "missing pipeline_options: land_sha, touched_paths (non-empty list)",
            }
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])

        propagation = opts.get("propagation")
        porcelain_raw = opts.get("porcelain_raw")
        if porcelain_raw is None:
            porcelain_raw = git_porcelain()

        serving_map = load_serving_map()
        manage = ManageClient()
        result = compute_dry_run(
            land_sha=str(land_sha),
            touched_paths=[str(p) for p in touched],
            porcelain_raw=str(porcelain_raw),
            serving_map=serving_map,
            manage=manage,
            propagation=propagation if isinstance(propagation, list) else None,
            source_pins_ok=opts.get("source_pins_ok"),
        )
        payload = result.to_json()
        payload["DISPOSITION"] = result.disposition_draft
        return StepOutput(raw=json.dumps(payload, indent=2), json=payload)
