#!/usr/bin/env python3
"""
Migrate stargate configs to unified schema.

Usage:
    python scripts/migrate-config-unified.py config/stargate_config.io.yaml

Checks:
1. Removes ${VAR:-default} syntax (fail-fast only)
2. Moves gateway.url to federation.local_gateway (Remote mode)
3. Validates Master has no gateway.url
4. Outputs to .unified.yaml for review
"""

import re
import sys
from pathlib import Path

import yaml


def remove_env_defaults(value: str) -> str:
    """Replace ${VAR:-default} with ${VAR} (fail-fast)."""
    # Pattern: ${VAR:-default} -> ${VAR}
    return re.sub(r'\$\{([^:}]+):-[^}]*\}', r'${\1}', value)


def walk_and_fix_env_vars(obj):
    """Recursively fix env var syntax in config."""
    if isinstance(obj, str):
        return remove_env_defaults(obj)
    elif isinstance(obj, dict):
        return {k: walk_and_fix_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [walk_and_fix_env_vars(item) for item in obj]
    return obj


def migrate_remote_gateway(config: dict) -> list[str]:
    """Migrate Remote mode: move gateway.url to federation.local_gateway."""
    warnings = []

    federation = config.get("federation", {})
    if federation.get("mode") != "remote":
        return warnings

    gateway = config.get("gateway", {})
    local_gw = federation.get("local_gateway", {})

    # Move URL/socket from gateway to local_gateway
    gw_url = gateway.get("url")
    gw_socket = gateway.get("socket_path")

    if gw_url and "unix://" in str(gw_url):
        # Unix socket URL -> socket_path
        socket_path = gw_url.replace("unix://", "")
        if not local_gw.get("socket_path"):
            local_gw["socket_path"] = socket_path
            warnings.append("Moved gateway.url to federation.local_gateway.socket_path")
        gateway["url"] = None
    elif gw_url:
        if not local_gw.get("url"):
            local_gw["url"] = gw_url
            warnings.append("Moved gateway.url to federation.local_gateway.url")
        gateway["url"] = None

    if gw_socket and not local_gw.get("socket_path"):
        local_gw["socket_path"] = gw_socket
        warnings.append("Moved gateway.socket_path to federation.local_gateway.socket_path")
        gateway["socket_path"] = None

    federation["local_gateway"] = local_gw
    config["federation"] = federation
    config["gateway"] = gateway

    return warnings


def validate_master_no_gateway(config: dict) -> list[str]:
    """Validate Master mode has no direct Gateway access."""
    errors = []

    federation = config.get("federation", {})
    if federation.get("mode") != "master":
        return errors

    gateway = config.get("gateway", {})
    url = gateway.get("url")
    socket_path = gateway.get("socket_path")

    if url and str(url).strip() and str(url).lower() != "null":
        errors.append(f"Master mode cannot have gateway.url={url!r}")
    if socket_path and str(socket_path).strip():
        errors.append(f"Master mode cannot have gateway.socket_path={socket_path!r}")

    return errors


def migrate_config(config_path: Path) -> tuple[dict, list[str], list[str]]:
    """
    Migrate a config file.

    Returns:
        (migrated_config, warnings, errors)
    """
    with config_path.open() as f:
        config = yaml.safe_load(f)

    warnings = []
    errors = []

    # Step 1: Fix env var syntax
    config = walk_and_fix_env_vars(config)

    # Step 2: Migrate Remote gateway config
    warnings.extend(migrate_remote_gateway(config))

    # Step 3: Validate Master config
    errors.extend(validate_master_no_gateway(config))

    return config, warnings, errors


def main():
    if len(sys.argv) != 2:
        print("Usage: migrate-config-unified.py <config.yaml>")
        sys.exit(1)

    config_path = Path(sys.argv[1])
    if not config_path.exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    config, warnings, errors = migrate_config(config_path)

    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  ⚠️  {w}")

    if errors:
        print("Errors (must fix manually):")
        for e in errors:
            print(f"  ❌ {e}")
        sys.exit(1)

    # Write output
    output_path = config_path.with_suffix(".unified.yaml")
    with output_path.open("w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"✅ Migrated: {config_path} → {output_path}")
    print("Review the output and rename when ready.")


if __name__ == "__main__":
    main()
