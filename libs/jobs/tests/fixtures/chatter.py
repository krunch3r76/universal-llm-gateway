#!/usr/bin/env python3
"""Fixture child that prints once a second, then exits 0.

Used to show a run can outlive several idle windows when output continues.
The loop count is three times a two-second idle budget.
"""

import time

for _ in range(6):
    print("tick", flush=True)
    time.sleep(1)
