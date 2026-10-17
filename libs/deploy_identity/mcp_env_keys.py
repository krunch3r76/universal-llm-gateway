"""Names-only compare of MCP compose passthrough keys to container Config.Env.

``build_mcp_env`` starts from the process environment, so its full key set is
never equal to a container's Config.Env (image defaults such as PATH). The
set that matters is the compose ``environment:`` names that ``build_mcp_env``
actually carries. A missing name means ``docker stop``/``start`` would keep
the create-time env. Extra container names are not a difference.

This module never prints env values. Inspect text is parsed for the name
before the first ``=`` and then discarded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_COMPOSE_REL = Path("docker") / "compose" / "mcp-server.yml"
_BROWSER_OVERRIDE_REL = Path("docker") / "compose" / "mcp-server-browser.override.yml"
_DIFFER_EXIT = 10


def environment_key_names(compose_file: Path) -> set[str]:
    """Return environment variable names declared on services in a compose file."""
    import yaml

    loaded = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return set()
    services = loaded.get("services") or {}
    if not isinstance(services, dict):
        return set()
    names: set[str] = set()
    for service in services.values():
        if not isinstance(service, dict):
            continue
        env = service.get("environment") or []
        if isinstance(env, dict):
            names.update(str(key) for key in env if str(key))
            continue
        if not isinstance(env, list):
            continue
        for item in env:
            raw = str(item)
            name = raw.split("=", 1)[0].strip()
            if name:
                names.add(name)
    return names


def passthrough_key_names(workspace_root: Path, *, browser: bool) -> set[str]:
    """Compose env names that ``build_mcp_env`` would pass. Values are dropped."""
    compose_keys = environment_key_names(workspace_root / _COMPOSE_REL)
    if browser:
        override = workspace_root / _BROWSER_OVERRIDE_REL
        if override.is_file():
            compose_keys |= environment_key_names(override)
    from scripts.model_manager.ui.controller.service_config import build_mcp_env

    env = build_mcp_env(workspace_root)
    return {key for key in compose_keys if key in env}


def config_env_key_names(inspect_text: str) -> set[str]:
    """Parse key names from ``docker inspect`` Config.Env lines. Values are dropped."""
    names: set[str] = set()
    for line in inspect_text.splitlines():
        if "=" not in line:
            continue
        name = line.split("=", 1)[0]
        if name:
            names.add(name)
    return names


def missing_passthrough_key_names(
    desired: set[str], container_names: set[str]
) -> list[str]:
    """Sorted desired names absent from the container. Extra container names are ignored."""
    return sorted(desired - container_names)


def _browser_flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare MCP passthrough env key names to container Config.Env names."
    )
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd())
    parser.add_argument("--compose-browser", default="false")
    parser.add_argument("--inspect-file", type=Path, default=None)
    parser.add_argument(
        "--print-names",
        action="store_true",
        help="Print passthrough key names, one per line.",
    )
    args = parser.parse_args(argv)
    browser = _browser_flag(str(args.compose_browser))
    try:
        desired = passthrough_key_names(args.workspace_root, browser=browser)
    except Exception as exc:
        print(
            f"ERROR: MCP env key-name read failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    if args.print_names:
        for name in sorted(desired):
            print(name)
        return 0
    if args.inspect_file is None:
        print("ERROR: --inspect-file is required", file=sys.stderr)
        return 1
    try:
        inspect_text = args.inspect_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        print("ERROR: could not read docker inspect Config.Env output", file=sys.stderr)
        return 1
    missing = missing_passthrough_key_names(desired, config_env_key_names(inspect_text))
    if missing:
        print("MCP container missing env key names: " + ",".join(missing))
        return _DIFFER_EXIT
    return 0


def main() -> None:
    """CLI entry. Exit 10 when a passthrough key name is absent from the container."""
    sys.exit(_cli())


if __name__ == "__main__":
    main()
