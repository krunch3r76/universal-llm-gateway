"""Call-time resolution of git-integration-worker HTTP base URLs."""

from __future__ import annotations

import os


def git_integration_worker_base_url() -> str:
    """Return the GIW HTTP origin from the environment on each call.

    ``GIT_INTEGRATION_WORKER_URL`` wins when set to a non-empty value (after
    strip). Otherwise build ``http://{host}:{port}`` from
    ``GIT_INTEGRATION_WORKER_HOST`` (default ``127.0.0.1``) and
    ``GIT_INTEGRATION_WORKER_PORT`` (default ``8091``).

    Raises:
        ValueError: When the URL is unset and ``GIT_INTEGRATION_WORKER_PORT`` is
            not an integer.
    """
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL")
    if explicit is not None and explicit.strip():
        return explicit.strip().rstrip("/")
    host = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1").strip()
    port_raw = os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091").strip()
    try:
        port = int(port_raw or "8091")
    except ValueError as exc:
        raise ValueError(
            f"GIT_INTEGRATION_WORKER_PORT must be an integer, got {port_raw!r}"
        ) from exc
    return f"http://{host}:{port}"


def giw_api_url(path: str) -> str:
    """Join the GIW base URL with *path* using exactly one slash."""
    base = git_integration_worker_base_url()
    return f"{base}/{path.lstrip('/')}"
