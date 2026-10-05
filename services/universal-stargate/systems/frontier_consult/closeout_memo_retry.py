"""Start the closeout-memo delivery retry loop inside the Stargate process.

The combined CDP-owed + overdue file from the spec is split: this module
starts only the pipeline retry sweep. CDP producer and the overdue watch
are slice 2.
"""

from __future__ import annotations


def start_closeout_memo_retry_sweep() -> None:
    """Arm the 30s retry sweep once. Safe to call from lifespan and admit."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pipelines.closeout_memo.v1.handlers.sweep import start_retry_sweep

    start_retry_sweep()
