"""CLI entry point: python -m agent_bus_store serve --db X --sock Y."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from .server import run_service


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent_bus_store")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the agent bus")
    serve.add_argument(
        "--db",
        default=os.environ.get("AGENT_BUS_DB_PATH", "~/.agent-bus/messages.db"),
    )
    serve.add_argument(
        "--sock",
        default=os.environ.get(
            "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
        ),
    )
    serve.add_argument("--host", default=None, help="TCP host (optional)")
    serve.add_argument("--port", type=int, default=None, help="TCP port (optional)")
    args = parser.parse_args()

    if args.command != "serve":
        parser.print_help()
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    db_path = os.path.expanduser(args.db)
    asyncio.run(
        run_service(db_path=db_path, sock=args.sock, host=args.host, port=args.port)
    )


if __name__ == "__main__":
    main()
