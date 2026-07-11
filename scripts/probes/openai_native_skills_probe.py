#!/usr/bin/env python3
"""Live probe: OpenAI native skills mount via team_dispatch skills=.

Exercises AC9 — hosted shell reads mounted SKILL.md and the model reply
quotes skill content. Requires live Stargate + OPENAI credentials.

Usage:
  python scripts/probes/openai_native_skills_probe.py --role reviewer
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "libs"))

from transport_utils import DEFAULT_STARGATE_URL, make_sync_client  # noqa: E402


def _poll_result(client, execution_id: str, *, timeout_s: int = 600) -> dict:
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = client.get(f"/api/v1/pipelines/executions/{execution_id}")
        resp.raise_for_status()
        payload = resp.json()
        status = str(payload.get("status") or "")
        if status in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(2)
    raise TimeoutError(f"execution {execution_id} did not complete within {timeout_s}s")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="reviewer")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_NATIVE_SKILLS_PROBE_MODEL", "openai/gpt-5.5"),
    )
    parser.add_argument(
        "--stargate-url",
        default=os.environ.get("STARGATE_URL", DEFAULT_STARGATE_URL),
    )
    args = parser.parse_args()

    prompt = (
        "Use the mounted advisor-timing skill inside the hosted shell. "
        "Read its SKILL.md, then reply with a one-sentence summary quoting the "
        "mandatory preflight before any team_dispatch."
    )
    body = {
        "op": "generate",
        "role": args.role,
        "dispatch_thread_id": f"probe-skills-{uuid.uuid4().hex[:12]}",
        "model": args.model,
        "contract": "light-bounded",
        "skills": ["advisor-timing"],
        "messages": [{"role": "user", "content": prompt}],
        "mcp": False,
    }

    with make_sync_client(args.stargate_url, timeout=120.0) as client:
        resp = client.post("/api/v1/team/dispatch", json=body)
        print("dispatch_status", resp.status_code)
        envelope = resp.json()
        print(json.dumps(envelope, indent=2)[:4000])
        if resp.status_code >= 400:
            return 1
        execution_id = str(envelope.get("execution_id") or "")
        if not execution_id:
            print("missing execution_id", file=sys.stderr)
            return 1
        result = _poll_result(client, execution_id)
        print("final_status", result.get("status"))
        output = json.dumps(result, indent=2)
        print(output[:12000])
        text_blob = output.lower()
        ok = "seat slug" in text_blob or "seat slugs" in text_blob
        if "shell_call" not in text_blob and "server_tool_calls" not in text_blob:
            print("WARN: no shell_call/server_tool_calls markers in execution output")
        return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
