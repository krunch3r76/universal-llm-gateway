"""Pytest plugin for every test under services/grok-window-edge.

Puts the service root on ``sys.path`` so ``grok_window_edge`` imports the way
it does from ``main.py``. The repo's root ``pytest.ini`` already supplies
``libs`` and ``services/universal-stargate``, which is what lets the
round-trip test drive the real Stargate service functions in-process.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SERVICE_ROOT = str(Path(__file__).resolve().parent)
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)
