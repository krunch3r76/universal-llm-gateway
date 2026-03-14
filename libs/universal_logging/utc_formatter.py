"""
UTC-aware formatter for plain-text logging fallbacks.
"""

from __future__ import annotations

import logging
import time


class UTCFormatter(logging.Formatter):
    """Standard formatter that renders `asctime` in UTC."""

    # Force asctime conversion to UTC for all records.
    converter: object = time.gmtime
