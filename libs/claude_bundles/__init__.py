"""Claude.ai self-contained skill bundle generation."""

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'mcp', 'stargate')

