"""Open-items reconciliation: shared matching + server-side resolution index.

``reconcile`` is pure (stdlib only) so both the cortex boot path (mcp-server,
over HTTP-fetched data) and the control tower aggregation (cortex-api,
server-side) import the same matching logic. ``resolution_index`` is the
server-side SQL source (cortex tables) and is imported only where a DB
connection is available.
"""
