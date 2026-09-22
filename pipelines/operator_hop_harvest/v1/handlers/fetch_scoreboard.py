"""Read scoreboard markdown from cortex share backing store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json


def _read_cortex_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("cortex://"):
        return "", ""
    rel = uri[len("cortex://") :]
    path = Path("/mnt/torus/mcp-data/files") / rel
    if not path.is_file():
        return "", ""
    body = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body, digest


class OperatorHopHarvestFetchScoreboardHandler(BaseHandler):
    step_type = "operator_hop_harvest_fetch_scoreboard_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        ledger = step_output_json(outputs, "fetch_ledger")
        uri = str(ledger.get("scoreboard_uri") or "")
        body, sha = _read_cortex_uri(uri)
        out = {
            "scoreboard_body": body,
            "scoreboard_sha256": sha,
            "scoreboard_uri": uri,
        }
        return StepOutput(raw=json.dumps(out), json=out)
