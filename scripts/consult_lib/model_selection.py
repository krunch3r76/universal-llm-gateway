"""Model selection: role configs, intelligence profiles, cloud-only filtering."""

from __future__ import annotations

import sys
from typing import Any

import httpx
import yaml

from .constants import _ROLES_PATH, STARGATE_URL


def load_roles() -> dict[str, Any]:
    """Load role prompt templates and select configs from the companion YAML file."""
    return yaml.safe_load(_ROLES_PATH.read_text())


def split_role_config(
    roles_data: dict[str, Any],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Split YAML payload into role prompts and role selection configs."""
    role_prompts: dict[str, str] = {}
    role_select: dict[str, dict[str, Any]] = {}
    for key, value in roles_data.items():
        if key.endswith("_select"):
            if isinstance(value, dict):
                role_select[key[: -len("_select")]] = value
            continue
        if isinstance(value, str):
            role_prompts[key] = value
    return role_prompts, role_select


def build_role_requirements(role: str, count: int) -> dict[str, Any] | None:
    """Build ModelRequirements payload for intelligence-profile-driven selection."""
    match role:
        case "architect":
            return {
                "task": "code_architecture",
                "min_score": "good",
                "count": count,
                "source": "cloud",
            }
        case "planner":
            return {
                "task": "planning",
                "min_score": "good",
                "count": count,
                "source": "cloud",
            }
        case "reviewer":
            return {
                "task": "code_review",
                "min_score": "good",
                "count": count,
                "source": "cloud",
            }
        case "researcher":
            return {
                "task": "research",
                "min_score": "good",
                "count": count,
                "source": "cloud",
            }
        case "modularizer":
            return {
                "task": "code_review",
                "min_score": "good",
                "count": count,
                "source": "any",
            }
        case "prompt_engineer":
            return {
                "task": "code_review",
                "min_score": "good",
                "count": count,
                "source": "cloud",
            }
        case _:
            return None


def select_models_via_profiles(
    role: str,
    *,
    stargate_url: str,
    count: int,
    timeout: float = 5.0,
) -> list[str] | None:
    """Query intelligence profile store for role-appropriate models.

    Returns ranked model IDs if the store is available and has matches.
    Returns None to signal "use fallback selection".
    """
    requirements = build_role_requirements(role, count)
    if requirements is None:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{stargate_url.rstrip('/')}/v1/models/query-profiles",
                json=requirements,
            )
        resp.raise_for_status()
        data = resp.json()
        models_raw = data.get("models", []) if isinstance(data, dict) else []
        if not isinstance(models_raw, list):
            return None
        model_ids = [
            m.get("id", "")
            for m in models_raw
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m.get("id")
        ]
        if model_ids:
            print(
                f"Selection path: intelligence-profiles ({', '.join(model_ids)})",
                file=sys.stderr,
            )
            return model_ids
        print(
            f"Selection path: intelligence-profiles returned no matches for '{role}'",
            file=sys.stderr,
        )
    except httpx.HTTPStatusError as exc:
        print(
            f"Selection path: intelligence-profiles HTTP error: {exc.response.status_code} {exc.response.text}",
            file=sys.stderr,
        )
    except httpx.RequestError as exc:
        print(
            f"Selection path: intelligence-profiles network error: {exc}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"Selection path: intelligence-profiles unexpected error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    return None


def select_models_for_role(
    role: str,
    role_select: dict[str, dict[str, Any]],
    stargate_url: str = STARGATE_URL,
    count: int = 2,
) -> list[str] | None:
    """Select models for a role using multi-tier selection.

    Selection tiers (highest priority first):
      1. Intelligence profile query via POST /v1/models/query-profiles
      2. Cloud proxy /api/select with role's selection criteria
      3. None — caller falls back to static DEFAULT_MODELS
    """
    profile_models = select_models_via_profiles(
        role, stargate_url=stargate_url, count=count
    )
    if profile_models:
        return profile_models

    select_config = role_select.get(role)
    if select_config is None:
        return None

    payload: dict[str, Any] = {"count": count}
    for field in (
        "tags",
        "exclude_tags",
        "min_context",
        "modality_contains",
        "providers",
    ):
        if field in select_config:
            payload[field] = select_config[field]

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(f"{stargate_url.rstrip('/')}/api/select", json=payload)
        resp.raise_for_status()
        models = [m["id"] for m in resp.json().get("models", [])]
        if models:
            print(
                f"Selection path: cloud-select ({', '.join(models)})", file=sys.stderr
            )
            return models
    except httpx.HTTPStatusError as exc:
        print(
            f"Selection path: cloud-select HTTP error: {exc.response.status_code} {exc.response.text}",
            file=sys.stderr,
        )
    except httpx.RequestError as exc:
        print(
            f"Selection path: cloud-select network error: {exc}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(
            f"Selection path: cloud-select unexpected error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    return None


def is_local_model_id(model_id: str) -> bool:
    """Heuristic: local models lack '/' (cloud IDs use provider/model format)."""
    return "/" not in model_id


def warn_local_model_for_role(
    role: str,
    model_ids: list[str],
    role_select: dict[str, dict[str, Any]],
) -> None:
    """Warn when --models includes local models for a role that excludes them."""
    select_config = role_select.get(role)
    if not select_config:
        return
    exclude_tags = select_config.get("exclude_tags", [])
    if not isinstance(exclude_tags, list) or "local" not in exclude_tags:
        return
    local_models = [m for m in model_ids if is_local_model_id(m)]
    if not local_models:
        return
    print(
        f"Warning: role '{role}' excludes local models in auto-selection, "
        f"but --models includes: {', '.join(local_models)}",
        file=sys.stderr,
    )


def fetch_available_model_ids(
    stargate_url: str,
    *,
    timeout: float = 5.0,
) -> set[str] | None:
    """Fetch currently exposed model IDs from Stargate /v1/models.

    Returns None on transport/parse failure.
    """
    url = f"{stargate_url.rstrip('/')}/v1/models"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return None
        return {
            m.get("id", "")
            for m in data
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        }
    except httpx.HTTPStatusError as exc:
        print(
            f"Failed to fetch available model IDs from Stargate (HTTP error: {exc.response.status_code} {exc.response.text})",
            file=sys.stderr,
        )
        return None
    except httpx.RequestError as exc:
        print(
            f"Failed to fetch available model IDs from Stargate (network error: {exc})",
            file=sys.stderr,
        )
        return None
    except Exception as exc:
        print(
            f"Failed to fetch available model IDs from Stargate (unexpected error: {type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return None
