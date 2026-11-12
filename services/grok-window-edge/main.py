#!/usr/bin/env python3
"""Start the grok-window-edge lease loop.

    services/grok-window-edge/main.py --kinds ping

Not a manage service and not auto-started: it is an operator/user unit, so
nothing here registers a slug or relaunches anything. Its only outbound HTTP
is to Stargate, resolved by ``transport_utils``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
for _extra in (_REPO / "libs", Path(__file__).resolve().parent):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the edge's command line."""
    parser = argparse.ArgumentParser(description="grok-window-edge lease loop")
    parser.add_argument(
        "--kinds",
        nargs="+",
        default=None,
        help="Edge-op kinds this process serves (default: ping)",
    )
    parser.add_argument(
        "--lease-wait",
        type=float,
        default=None,
        help="Long-poll hold in seconds (default: 60)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the lease loop until the process is stopped."""
    args = _parse_args(argv)

    from grok_window_edge import EdgeLoop, HttpEdgeClient
    from grok_window_edge import config as edge_config

    kinds = tuple(args.kinds) if args.kinds else edge_config.edge_kinds()
    lease_wait_s = (
        args.lease_wait
        if args.lease_wait is not None
        else edge_config.DEFAULT_LEASE_WAIT_SECONDS
    )
    loop = EdgeLoop(
        HttpEdgeClient(),
        kinds=kinds,
        lease_wait_s=lease_wait_s,
    )
    asyncio.run(loop.run_forever())


if __name__ == "__main__":
    main()
