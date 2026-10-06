#!/usr/bin/env python3
"""postToolUse hook: one pending steer as ``additional_context``.

The cursor-sdk local executor maps that field onto
``HookAdditionalContext`` for the tool result the model reads. MCP tool
names (``MCP:``) return an empty object so the stdio bridge still delivers.

In-flight kill switch: if ``~/.gateway/steer-native-hook.disabled`` exists
(parent is ``DATA_DIR`` when set, same root as ``steer-spool/``), this
process writes ``{}`` and does not consume the spool row. ``ULG_STEER_NATIVE_HOOK``
is read only when the dispatch HOME is set up, so a running agent keeps
the hook until this sentinel is present.

Fail open: import errors, lock contention, and handler exceptions write
``{}`` so the tool result still reaches the model.
"""

from __future__ import annotations

import sys


def main() -> None:
    try:
        import json
        from pathlib import Path

        _repo_root = Path(__file__).resolve().parent.parent
        if str(_repo_root) not in sys.path:
            sys.path.insert(0, str(_repo_root))

        from scripts.mcp_bridge_steer_inject import (  # noqa: E402
            native_hook_kill_sentinel_path,
            native_tool_steer_hook_response,
        )

        if native_hook_kill_sentinel_path().is_file():
            sys.stdout.write("{}")
            return

        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        response = native_tool_steer_hook_response(payload)
        sys.stdout.write(json.dumps(response if isinstance(response, dict) else {}))
    except Exception:
        try:
            sys.stdout.write("{}")
        except Exception:
            return


if __name__ == "__main__":
    main()
