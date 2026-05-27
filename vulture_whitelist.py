"""Vulture whitelist — false positives from dynamic dispatch and framework callbacks.

Vulture cannot detect usage via getattr(), FastAPI route decorators, event
handler registrations, or __init__.py re-exports. List confirmed false
positives here. Run: vulture {dir} vulture_whitelist.py --min-confidence 80
"""

# Protocol stub parameters (BatchRouterProtocol.route_batch_dict)
batch_data  # noqa: F821

# __aexit__ signature (transport_lifecycle.ProxyTransportLifecycle)
exc_val  # noqa: F821
exc_tb  # noqa: F821

# Mock hydrate callbacks matching real signature (test_frontier_dispatch.py)
transcript_id  # noqa: F821
