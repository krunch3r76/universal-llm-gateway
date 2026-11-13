"""Decide container readiness from a ``docker ps`` Status string.

``docker ps`` Status looks like ``Up 5 minutes (healthy)``,
``Up 1 second (health: starting)``, ``Up 2 hours (unhealthy)`` or
``Restarting (0) 3 seconds ago``. Treating every ``Up ...`` as running let a
crash-looping or unhealthy container show as healthy in manage, so readiness
is decided from the health suffix, not the ``Up`` prefix.
"""

from __future__ import annotations

_NOT_READY_MARKERS = ("(unhealthy)", "(health: starting)")


def container_is_ready(docker_status: str) -> bool:
    """Return True only for an up container that is not unhealthy or still starting.

    A container with no healthcheck has no suffix and counts as ready; one
    with a healthcheck must have reported ``(healthy)``.
    """
    if not docker_status.startswith("Up"):
        return False
    return not any(marker in docker_status for marker in _NOT_READY_MARKERS)
