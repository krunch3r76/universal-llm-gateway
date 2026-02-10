"""
Command implementations for model-manager CLI.

Commands are lazy-loaded by cli.py to avoid importing optional dependencies
(e.g., HuggingFace tools) unless actually needed.

Available commands:
    - check_resources: System resource diagnostics
    - catalog: discover, generate, list, info (catalog operations)
    - verify: HuggingFace verification
    - promote: Promote to verified registry
    - download: Download models
    - download_catalog: Download from catalog
    - local_catalog: export, init, remove, update, validate
    - measure: VRAM/RAM profiling
    - lint: V2 compliance checking
    - stats: Catalog statistics
"""

# NOTE: No eager imports here - cli.py uses lazy loading via importlib.
# This prevents optional dependencies from being required for all commands.
