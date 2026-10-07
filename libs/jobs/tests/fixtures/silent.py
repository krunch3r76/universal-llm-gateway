#!/usr/bin/env python3
"""Fixture child that produces no output and ignores SIGTERM.

The idle-timeout test needs the ten-second grace after SIGTERM before
SIGKILL, so this process stays silent and does not die on the first signal.
"""

import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(3600)
