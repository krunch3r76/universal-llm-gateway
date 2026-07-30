"""Deploy identity helpers — process-start code SHA for proof-of-live probes."""

from deploy_identity.code_version import resolve_code_version
from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url

# Harvest mints one sync_restart row per consumer when this shared lib lands.
CONSUMERS: tuple[str, ...] = ("git_integration_worker", "mcp")

__all__ = ["CONSUMERS", "resolve_code_version", "resolve_mcp_health_probe_url"]
