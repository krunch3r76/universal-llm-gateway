#!/usr/bin/env python3
"""Refuse the retired GIW liaison doorbell schedule.

The four-hour Cowork paste lived in ``~/.gateway/trigger-schedule.sqlite`` and
kept firing after the house ticker was stopped. The ticker is the only house
wake. This script used to POST ``/api/v1/triggers``; it must not.
"""

from __future__ import annotations

import sys

_REFUSAL = """\
liaison-schedule-wake is retired. It must not POST /api/v1/triggers.

The house wake is the gear-3 ticker:
  scripts/liaison-tick.py --root R --loop --spawn-on-wake
Armed only by --set ready=true or --go-under.
A paused house stays quiet: --set ready=false, and the ticker unit stays disabled.
"""


def main() -> int:
    """Exit non-zero. Never schedule a trigger."""
    print(_REFUSAL, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
