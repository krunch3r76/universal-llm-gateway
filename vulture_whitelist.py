"""Vulture whitelist — false positives from dynamic dispatch and framework callbacks.

Vulture cannot detect usage via getattr(), FastAPI route decorators, event
handler registrations, or __init__.py re-exports. List confirmed false
positives here. Run: vulture {dir} vulture_whitelist.py --min-confidence 80
"""

# Protocol stub parameters (BatchRouterProtocol.route_batch_dict)
batch_data  # noqa: F821

# __aexit__ / __exit__ signatures (transport_lifecycle + file_locker)
exc_type  # noqa: F821
exc_val  # noqa: F821
exc_tb  # noqa: F821

# yaml.SafeDumper.increase_indent override keeps parent signature
indentless  # noqa: F821

# ResourceTracker.get_current_process_resources — API-compat unused baseline
baseline_ram_mb  # noqa: F821

# TYPE_CHECKING annotation-only import (streams.py StreamHandlers)
StreamEntry  # noqa: F821

# /v1/models helpers — include_all_variants kept for API/backward compat
include_all_variants  # noqa: F821

# Mock hydrate callbacks matching real signature (test_frontier_dispatch.py)
transcript_id  # noqa: F821
