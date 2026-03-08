"""Model selection: role configs, unified endpoint selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import yaml

from .constants import _ROLES_PATH, STARGATE_URL


@dataclass(slots=True, kw_only=True)
class SelectionFailure(RuntimeError):  # noqa: N818
    """Structured model-selection failure with HTTP context.

    Raised instead of returning None so callers receive machine-readable
    failure metadata (status_code, failure_kind) for artifact recording.
    """

    role: str
    reason: str
    failure_kind: str
    status_code: int | None = None
    response_excerpt: str | None = None

    def __str__(self) -> str:
        base = f"selection failed for role '{self.role}': {self.reason}"
        if self.status_code is not None:
            base += f" (http {self.status_code})"
        return base


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
) -> tuple[list[str], str]:
    """Select models for a role via the unified Stargate endpoint.

    Sends a single POST /v1/models/select with the role's requirements.
    Returns (model_ids, selection_path) on success.
    Raises SelectionFailure on all error paths (config missing, HTTP error,
    network error, empty result).
    """
    req_config = role_requirements.get(role)
    if req_config is None:
        raise SelectionFailure(
            role=role,
            reason="missing role requirements",
            failure_kind="config_missing",
        )

    payload = {**req_config, "count": count}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{stargate_url.rstrip('/')}/v1/models/select",
                json=payload,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SelectionFailure(
            role=role,
            reason="unified selection endpoint returned error",
            failure_kind="http_error",
            status_code=exc.response.status_code,
            response_excerpt=exc.response.text[:500],
        ) from exc
    except httpx.RequestError as exc:
        raise SelectionFailure(
            role=role,
            reason=str(exc),
            failure_kind="network_error",
        ) from exc

    data = resp.json()
    selection_path = data.get("selection_path", "unknown")
    model_ids = [m["id"] for m in data.get("models", []) if isinstance(m, dict)]
    if not model_ids:
        raise SelectionFailure(
            role=role,
            reason=f"selection path '{selection_path}' returned no models",
            failure_kind="empty_result",
        )
    return model_ids, selection_path


def warn_local_model_for_role(
    role: str,
    model_ids: list[str],
    role_requirements: dict[str, dict[str, Any]],
) -> None:
    """Warn when --models includes local models for a role configured as cloud-only."""
    import sys

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
