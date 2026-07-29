"""Deploy identity helpers — process-start code SHA for proof-of-live probes."""

from deploy_identity.code_version import resolve_code_version

# Harvest mints one sync_restart row per consumer when this shared lib lands.
CONSUMERS: tuple[str, ...] = ("git_integration_worker", "mcp")

__all__ = ["CONSUMERS", "resolve_code_version"]
