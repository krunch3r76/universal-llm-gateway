#!/usr/bin/env python3
"""Fixture child that prints once a second until it is killed.

The restart test uses this executable. A parent kill -9 of the jobs app
must leave this process in its own session so reconcile can SIGKILL the
recorded process group.
"""

import time

while True:
    print("tick", flush=True)
    time.sleep(1)
