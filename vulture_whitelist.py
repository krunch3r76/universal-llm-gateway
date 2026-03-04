"""Vulture whitelist — false positives from dynamic dispatch and framework callbacks.

Vulture cannot detect usage via getattr(), FastAPI route decorators, event
handler registrations, or __init__.py re-exports. List confirmed false
positives here. Run: vulture {dir} vulture_whitelist.py --min-confidence 80
"""
