"""Target resolution for interlocutor read/steer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from chat_harvest.models import ClassifyRefuse, classify_chat_url

MAESTRO_COWORK_PARENT_THREAD = "12286"


@dataclass(frozen=True, slots=True)
class CoworkMaestroTarget:
    kind: Literal["cowork_maestro"] = "cowork_maestro"
    parent_thread: str = MAESTRO_COWORK_PARENT_THREAD


@dataclass(frozen=True, slots=True)
class CoworkUrlTarget:
    kind: Literal["cowork_url"] = "cowork_url"
    url: str = ""


@dataclass(frozen=True, slots=True)
class DisplayEightTarget:
    kind: Literal["display_eight"] = "display_eight"
    display: str = "8"
    app: str = "grok"
    agent_id: str = ""


ResolvedTarget = CoworkMaestroTarget | CoworkUrlTarget | DisplayEightTarget


def _normalize_maestro(target: str) -> CoworkMaestroTarget | None:
    t = target.strip().lower()
    if t in {"maestro", "cowork:12286", "12286", "house:12286"}:
        return CoworkMaestroTarget()
    return None


def _parse_display_target(target: str) -> DisplayEightTarget | ClassifyRefuse | None:
    raw = target.strip()
    if not raw.lower().startswith("display:"):
        return None
    rest = raw.split(":", 1)[1].strip()
    parts = [p for p in rest.split("/") if p]
    if not parts:
        return ClassifyRefuse(code="params_required", reason="display target needs :8/<agent_id>")
    disp = parts[0].lstrip(":")
    agent_id = parts[1] if len(parts) > 1 else ""
    if not agent_id:
        return ClassifyRefuse(
            code="agent_id_required",
            reason="display target requires agent_id (display:8/<agent_id>)",
        )
    app = parts[2] if len(parts) > 2 else "grok"
    return DisplayEightTarget(display=disp, app=app.replace("_", "-"), agent_id=agent_id)


def _parse_cowork_url(target: str) -> CoworkUrlTarget | None:
    if "://" not in target:
        return None
    parsed = urlparse(target.strip())
    host = (parsed.hostname or "").lower()
    if host == "claude.ai" and "/cowork/cse_" in (parsed.path or "").lower():
        return CoworkUrlTarget(url=target.strip())
    return None


def resolve_target(target: str) -> ResolvedTarget | ClassifyRefuse:
    """Map a facade target string to an adapter target."""
    if not (target or "").strip():
        return ClassifyRefuse(code="target_required", reason="target is required")

    maestro = _normalize_maestro(target)
    if maestro is not None:
        return maestro

    display = _parse_display_target(target)
    if display is not None:
        if isinstance(display, ClassifyRefuse):
            return display
        return display

    cowork_url = _parse_cowork_url(target)
    if cowork_url is not None:
        return cowork_url

    classified = classify_chat_url(target.strip())
    if isinstance(classified, ClassifyRefuse):
        if classified.code == "use_cse_session":
            return CoworkUrlTarget(url=target.strip())
        return classified

    return ClassifyRefuse(
        code="unsupported_target",
        reason=f"unsupported interlocutor target: {target!r}",
    )


def refuse_dict(refuse: ClassifyRefuse) -> dict:
    return refuse.model_dump()


__all__ = [
    "MAESTRO_COWORK_PARENT_THREAD",
    "CoworkMaestroTarget",
    "CoworkUrlTarget",
    "DisplayEightTarget",
    "ResolvedTarget",
    "refuse_dict",
    "resolve_target",
]
