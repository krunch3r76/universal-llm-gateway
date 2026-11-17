"""Cowork project bindings (``config/cdp/projects.yaml``) and per-dispatch selection."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import yaml

from .admission import FrontierEndpointError

_CONFIG_REL = Path("config/cdp/projects.yaml")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[4]


@lru_cache(maxsize=1)
def _load_bindings() -> dict[str, str]:
    path = _repo_root() / _CONFIG_REL
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    bindings = raw.get("cdp_project_bindings") or {}
    return {str(k): str(v) for k, v in bindings.items() if v}


def cdp_project_binding(key: str, *, request_id: str = "") -> str:
    """Resolve a named Cowork project UUID binding (e.g. ``life``)."""
    uuid = _load_bindings().get(key, "").strip()
    if not uuid:
        raise FrontierEndpointError(
            request_id=request_id,
            field="project_binding",
            reason=f"cdp_project_bindings[{key!r}] is not configured",
            status_code=422,
            code="cdp_project_binding_missing",
        )
    return uuid


@dataclass(frozen=True, slots=True)
class CdpDispatchProject:
    """Cowork Project chosen at admit for one CDP generate.

    At most one field is set. Both ``None`` leaves the satellite on its default
    Project name resolve.
    """

    project_uuid: str | None = None
    claude_project_name: str | None = None


def _stripped(value: str | None) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def resolve_cdp_dispatch_project(
    *,
    request_id: str,
    claude_surface: str | None,
    server_project_uuid: str | None = None,
    project_uuid: str | None = None,
    project_binding: str | None = None,
    claude_project_name: str | None = None,
) -> CdpDispatchProject:
    """Pick the Cowork Project for one CDP generate, before any admit side effect.

    Precedence: ``server_project_uuid`` (bound by the route, e.g. ``life_dispatch``)
    > ``project_uuid`` > ``project_binding`` > ``claude_project_name`` > nothing
    (the satellite resolves its default Project name). The first field present
    wins and lower fields are not read, so an unconfigured ``project_binding``
    under an explicit ``project_uuid`` does not refuse.

    The three caller fields are ignored on ``claude_surface="code"`` because
    claude.ai/code has no Project. Blank strings count as omitted.

    Raises ``FrontierEndpointError`` 422 ``cdp_project_uuid_invalid`` for a
    ``project_uuid`` that is not a UUID, and 422 ``cdp_project_binding_missing``
    for a ``project_binding`` key absent from ``config/cdp/projects.yaml``.
    """
    if server_project_uuid:
        return CdpDispatchProject(project_uuid=server_project_uuid)
    if (claude_surface or "").strip().lower() == "code":
        return CdpDispatchProject()
    explicit_uuid = _stripped(project_uuid)
    if explicit_uuid:
        try:
            return CdpDispatchProject(project_uuid=str(UUID(explicit_uuid)))
        except ValueError as exc:
            raise FrontierEndpointError(
                request_id=request_id,
                field="project_uuid",
                reason=f"project_uuid {explicit_uuid!r} is not a UUID",
                status_code=422,
                code="cdp_project_uuid_invalid",
            ) from exc
    binding_key = _stripped(project_binding)
    if binding_key:
        return CdpDispatchProject(
            project_uuid=cdp_project_binding(binding_key, request_id=request_id)
        )
    return CdpDispatchProject(claude_project_name=_stripped(claude_project_name))
