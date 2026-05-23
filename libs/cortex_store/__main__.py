"""CLI entry point: python -m cortex_store serve --db X --sock Y."""

from __future__ import annotations

import argparse
import asyncio
import os

from universal_logging import setup

from .server import run_service


def main() -> None:
    parser = argparse.ArgumentParser(prog="cortex_store")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the cortex API")
    serve.add_argument(
        "--db",
        default=os.environ.get("CORTEX_DB_PATH", "~/.cortex/cortex.db"),
    )
    serve.add_argument(
        "--sock",
        default=os.environ.get(
            "CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock"
        ),
    )
    serve.add_argument("--host", default=None, help="TCP host (optional)")
    serve.add_argument("--port", type=int, default=None, help="TCP port (optional)")
    args = parser.parse_args()

    if args.command != "serve":
        parser.print_help()
        return

    # CLI bootstrap: universal_logging auto-init (replaces stdlib basicConfig).
    setup()
    db_path = os.path.expanduser(args.db)
    asyncio.run(
        run_service(db_path=db_path, sock=args.sock, host=args.host, port=args.port)
    )


if __name__ == "__main__":
    main()
