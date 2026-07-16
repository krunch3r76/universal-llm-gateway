#!/usr/bin/env python3
"""Host CLI for MCP-dark restart-window visibility.

Reads the durable SOT at ``~/.gateway/restart-intents.db`` with zero MCP dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.model_manager.ui.controller.restart_intent_store import (  # noqa: E402
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.restart_window_store import (  # noqa: E402
    window_status_view,
)


def _dump(*, pretty: bool) -> int:
    store = RestartIntentStore.instance()
    now = datetime.now(UTC)
    store.sweep_expired_windows(now=now)
    payload = {
        "restart_windows": {
            "open": [
                window_status_view(w, now=now) for w in store.active_windows()
            ]
        }
    }
    if pretty:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump open operator restart windows from ~/.gateway/restart-intents.db"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON on stdout (default output format)",
    )
    args = parser.parse_args(argv)
    return _dump(pretty=args.json)


if __name__ == "__main__":
    sys.exit(main())
