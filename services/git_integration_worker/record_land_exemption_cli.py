"""CLI for ``record_land_exemption``.

A cited ruling turn is ``<thread>#<turn>`` (digits, a hash, digits). The
issuer is required and stored on the ledger row. Unknown commits, a blank
or non-citation turn, and a blank issuer exit 2 and write nothing. Does
not move git refs and does not bind a verdict.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from services.git_integration_worker.master_review_owed import record_land_exemption

_RULING_TURN_RE = re.compile(r"^\d+#\d+$")


def main(argv: list[str] | None = None) -> int:
    """Record one land exemption and return a process status.

    Exit 0 when the ledger commit returns ``recorded``. Exit 2 when the
    ruling turn is not a citation, the issuer is blank, the commits do not
    resolve, or the ledger write fails. Prints the status string on stdout.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--tip", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--ruling-turn", required=True)
    parser.add_argument("--issued-by", required=True)
    args = parser.parse_args(argv)
    ruling = (args.ruling_turn or "").strip()
    issuer = (args.issued_by or "").strip()
    if not _RULING_TURN_RE.fullmatch(ruling):
        print("ruling_turn_not_citation")
        return 2
    if not issuer:
        print("issued_by_empty")
        return 2
    status = record_land_exemption(
        args.repo,
        args.tip,
        base_sha=args.base,
        exemption_ref=ruling,
        issued_by=issuer,
    )
    print(status)
    return 0 if status == "recorded" else 2


if __name__ == "__main__":
    sys.exit(main())
