#!/usr/bin/env python3
"""Retired. This helper posted batches to the code-review virtual model.

A code review that leaves the tab is
team_dispatch(op=generate, model=cdp/opus-5.5, purpose=review, contract=none).
"""

from __future__ import annotations

import sys

from consult_lib.pipeline import CODE_REVIEW_PIPELINE_RETIRED


def main() -> int:
    """Refuse. The code-review pipeline submitter is retired."""
    print(CODE_REVIEW_PIPELINE_RETIRED, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
