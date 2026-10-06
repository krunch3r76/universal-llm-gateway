#!/usr/bin/env python3
"""postToolUse hook: one pending steer as ``additional_context``.

The cursor-sdk local executor maps that field onto
``HookAdditionalContext`` for the tool result the model reads. MCP tool
names (``MCP:``) return an empty object so the stdio bridge still delivers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.mcp_bridge_steer_inject import (  # noqa: E402
    native_tool_steer_hook_response,
)


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        response = native_tool_steer_hook_response(payload)
    except Exception:
        response = {}
    sys.stdout.write(json.dumps(response))


if __name__ == "__main__":
    main()
