"""CLI entry point: python -m event_store serve --db X --sock Y."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from .server import run_service


def main() -> None:
    parser = argparse.ArgumentParser(prog="event_store")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the event service")
    serve.add_argument(
        "--db",
        default=os.environ.get("EVENT_DB_PATH", "~/.events/events.db"),
    )
    serve.add_argument(
        "--sock",
        default=os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        ),
    )
    serve.add_argument(
        "--query-sock",
        default=os.environ.get(
            "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
        ),
    )
    serve.add_argument(
        "--retention-days",
        type=int,
        default=int(os.environ.get("RETENTION_DAYS", "7")),
    )
    serve.add_argument(
        "--max-sessions",
        type=int,
        default=int(os.environ.get("MAX_SESSIONS", "2")),
    )
    serve.add_argument(
        "--tcp",
        action="store_true",
        default=os.environ.get("EVENT_TCP_ENABLED", "").lower() in ("1", "true"),
    )
    serve.add_argument(
        "--tcp-ingest-port",
        type=int,
        default=int(os.environ.get("EVENT_INGEST_TCP_PORT", "7101")),
    )
    serve.add_argument(
        "--tcp-query-port",
        type=int,
        default=int(os.environ.get("EVENT_QUERY_TCP_PORT", "7102")),
    )
    serve.add_argument(
        "--query-sock-mode",
        default=os.environ.get("QUERY_UDS_MODE", "660"),
        help="Octal file mode for query UDS socket (e.g. 660).",
    )
    serve.add_argument(
        "--memory",
        action="store_true",
        default=os.environ.get("EVENT_PERSIST", "true").lower() not in ("1", "true"),
        help="In-memory SQLite (no disk writes). Overrides --db.",
    )
    serve.add_argument(
        "--bridge-upstream",
        default=os.environ.get("EVENT_BRIDGE_UPSTREAM"),
        help="Forward scope=global events to this upstream ingest socket.",
    )
    serve.add_argument(
        "--bridge-origin",
        default=os.environ.get("EVENT_BRIDGE_ORIGIN"),
        help="Node identifier stamped on bridged events.",
    )
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
    query_sock_mode = int(str(args.query_sock_mode), 8)
    asyncio.run(
        run_service(
            db_path=db_path,
            ingest_sock=args.sock,
            query_sock=args.query_sock,
            retention_days=args.retention_days,
            max_sessions=args.max_sessions,
            persist=not args.memory,
            tcp_enabled=args.tcp,
            tcp_ingest_port=args.tcp_ingest_port,
            tcp_query_port=args.tcp_query_port,
            query_sock_mode=query_sock_mode,
            bridge_upstream_sock=args.bridge_upstream,
            bridge_origin_node=args.bridge_origin,
        )
    )


if __name__ == "__main__":
    main()
