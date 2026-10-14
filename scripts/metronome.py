#!/usr/bin/env python3
"""Metronome CLI alias — delegates to scripts/liaison-tick.py while preserving liaison-*.tick.json state and AGENT_LOOP_TICK_liaison wire tokens."""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parent / "liaison-tick.py"),
        run_name="__main__",
    )
