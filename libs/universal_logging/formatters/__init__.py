"""
Legacy formatters module — DEPRECATED.

All formatters have been replaced by JSON renderers.
Use universal_logging.renderers.JSONFormatter instead.

This module exists only to provide clear error messages for
code that still imports old formatters.
"""


def __getattr__(name: str):
    """Raise helpful error for removed formatters."""
    removed = {
        "EnhancedFormatter": "universal_logging.renderers.JSONFormatter",
        "ColoredFormatter": "universal_logging.renderers.JSONFormatter",
        "ColoredConsoleHandler": "logging.StreamHandler with JSONFormatter",
        "create_colored_logger": "universal_logging.get_logger()",
    }
    if name in removed:
        raise AttributeError(
            f"{name} has been removed. "
            f"Use {removed[name]} instead. "
            f"See libs/universal_logging/README_AI.md for migration guide."
        )
    raise AttributeError(
        f"module 'universal_logging.formatters' has no attribute '{name}'"
    )


__all__: list[str] = []
