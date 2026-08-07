#!/usr/bin/env python3
"""CLI for CDP port registry — register / deregister / list / reclaim."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles import cdp_registry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="Acquire a fresh (port, profile) lane")
    p_reg.add_argument("--holder", required=True)
    p_reg.add_argument("--purpose", default=None)
    p_reg.add_argument(
        "--no-launch",
        action="store_true",
        help="Allocate only (no Chrome launch) — tests / dry alloc",
    )

    p_dereg = sub.add_parser("deregister", help="Mark lane released")
    p_dereg.add_argument("--registration-id", required=True)
    p_dereg.add_argument("--kill", action="store_true")

    sub.add_parser("list", help="List active registrations")

    p_hygiene = sub.add_parser(
        "hygiene-reclaim",
        help="Reclaim released/orphaned_retry lanes and sweep orphan profiles",
    )
    p_hygiene.add_argument(
        "--yes",
        action="store_true",
        help="Required confirm (destructive to released tombstones)",
    )
    p_hygiene.add_argument(
        "--no-stale-active",
        action="store_true",
        help="Skip stale-active reap (conjunctive TTL+dead PID+port down)",
    )
    p_hygiene.add_argument(
        "--no-orphan-sweep",
        action="store_true",
        help="Skip orphan profile-dir sweep",
    )

    args = parser.parse_args(argv)

    if args.cmd == "register":
        reg = cdp_registry.register_lane(
            holder=args.holder,
            purpose=args.purpose,
            launch=not args.no_launch,
        )
        print(f"registration_id={reg.registration_id}")
        print(f"cdp_url={reg.cdp_url}")
        print(f"port={reg.port}")
        print(f"profile_suffix={reg.profile_suffix}")
        print(f"profile={reg.profile}")
        if reg.display:
            print(f"display={reg.display}")
        return 0

    if args.cmd == "deregister":
        cdp_registry.deregister_lane(args.registration_id, kill=args.kill)
        print(f"deregistered registration_id={args.registration_id} kill={args.kill}")
        return 0

    if args.cmd == "list":
        from claude_bundles import cdp_orphans

        print(json.dumps(cdp_orphans.list_surface_payload(), indent=2))
        return 0

    if args.cmd == "hygiene-reclaim":
        if not args.yes:
            print("pass --yes to reclaim released ports", file=sys.stderr)
            return 2
        result = cdp_registry.hygiene_reclaim_extended(
            include_stale_active=not args.no_stale_active,
            include_orphan_sweep=not args.no_orphan_sweep,
        )
        print(
            json.dumps(
                {
                    "reclaimed_ports": result.reclaimed_ports,
                    "removed_profiles": result.removed_profiles,
                },
                indent=2,
            )
        )
        return 0

    parser.error(f"unknown cmd {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
