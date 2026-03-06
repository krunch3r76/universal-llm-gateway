"""Model selection: role configs, unified endpoint selection."""

from __future__ import annotations

import sys
from typing import Any

import httpx
import yaml

from .constants import _ROLES_PATH, STARGATE_URL


def load_roles() -> dict[str, Any]:
    """Load role prompt templates and requirement configs from the companion YAML file."""
    return yaml.safe_load(_ROLES_PATH.read_text())


def split_role_config(
    roles_data: dict[str, Any],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Split YAML payload into role prompts and role requirements configs."""
    role_prompts: dict[str, str] = {}
    role_requirements: dict[str, dict[str, Any]] = {}
    for key, value in roles_data.items():
        if key.endswith("_requirements"):
            if isinstance(value, dict):
                role_requirements[key[: -len("_requirements")]] = value
            continue
        if isinstance(value, str):
            role_prompts[key] = value
    return role_prompts, role_requirements


def select_models_for_role(
    role: str,
    role_requirements: dict[str, dict[str, Any]],
    stargate_url: str = STARGATE_URL,
    count: int = 2,
) -> list[str] | None:
    """Select models for a role via the unified Stargate endpoint.

    Sends a single POST /v1/models/select with the role's requirements.
    Returns model IDs if the endpoint returns results, None otherwise.
    """
    req_config = role_requirements.get(role)
    if req_config is None:
        print(
            f"No requirements config for role '{role}', skipping selection",
            file=sys.stderr,
        )
        return None

    payload = {**req_config, "count": count}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{stargate_url.rstrip('/')}/v1/models/select",
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        selection_path = data.get("selection_path", "unknown")
        model_ids = [m["id"] for m in data.get("models", []) if isinstance(m, dict)]

        if model_ids:
            print(
                f"Selection path: {selection_path} ({', '.join(model_ids)})",
                file=sys.stderr,
            )
            return model_ids
        print(
            f"Selection path: {selection_path} returned no models for '{role}'",
            file=sys.stderr,
        )
    except httpx.HTTPStatusError as exc:
        print(
            f"Selection: unified endpoint HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}",
            file=sys.stderr,
        )
    except httpx.RequestError as exc:
        print(
            f"Selection: unified endpoint network error: {exc}",
            file=sys.stderr,
        )
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).exception(
            "Selection: unified endpoint unexpected error for role '%s'", role
        )
        print(
            f"Selection: unified endpoint unexpected error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    return None


def warn_local_model_for_role(
    role: str,
    model_ids: list[str],
    role_requirements: dict[str, dict[str, Any]],
) -> None:
    """Warn when --models includes local models for a role configured as cloud-only."""
    req_config = role_requirements.get(role)
    if not req_config:
        return
    source = req_config.get("source", "any")
    if source != "cloud":
        return
    local_models = [m for m in model_ids if "/" not in m]
    if not local_models:
        return
    print(
        f"Warning: role '{role}' requires source=cloud, "
        f"but --models includes local: {', '.join(local_models)}",
        file=sys.stderr,
    )
