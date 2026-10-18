"""Resolve operator gateway and data directories without trusting dispatch HOME."""

from __future__ import annotations

import os
import pwd
from pathlib import Path


def passwd_home(*, strict: bool = False) -> Path:
    """Return the passwd home directory for the current uid."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        if strict:
            raise
        return Path.home()


def resolve_gateway_dir(
    override: str | Path | None = None,
    *,
    env_var: str | None = "GATEWAY_DIR",
) -> Path:
    """Resolve ``<home>/.gateway`` using override, env, then passwd home."""
    if override is not None:
        if isinstance(override, Path):
            if str(override):
                return override
        elif str(override).strip():
            return Path(str(override).strip())
    if env_var is not None:
        env = os.environ.get(env_var, "").strip()
        if env:
            return Path(env)
    return passwd_home() / ".gateway"


def resolve_gateway_data_dir(override: str | Path | None = None) -> Path:
    """Resolve DATA_DIR when set, otherwise the gateway dir (passwd, no GATEWAY_DIR env)."""
    if override is not None:
        if isinstance(override, Path):
            if str(override):
                return override
        elif str(override).strip():
            return Path(str(override).strip())
    data = os.environ.get("DATA_DIR", "").strip()
    if data:
        return Path(data).expanduser()
    return resolve_gateway_dir(env_var=None)
