"""Load CDP Cowork project UUID bindings from ``config/cdp/projects.yaml``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
