"""Two identities on ``/api/v1/edge/*``: the internal caller and the edge.

``admit`` and the caller-facing result reads use Stargate's existing
internal-caller auth, imported lazily so the substrate stays importable (and
testable) without booting the proxy.

The lease, event, result, release and directive resources use a per-edge
bearer instead. The edge presents ``X-Edge-Id`` plus its own token, and only
the token bound to that exact edge id is accepted, so one edge cannot write to
another's op. The edge holds no MCP token. Comparison is constant-time and no
branch of it logs, echoes or returns token material; a refusal says the same
thing whether the edge id is unknown or the token is wrong.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import Request

from . import config
from .model import CODE_IDENTITY_REFUSED, EdgeOpRefusedError

EDGE_ID_HEADER = "X-Edge-Id"

_REFUSAL_MESSAGE = "Edge identity not accepted for this resource."


async def require_internal_caller(request: Request) -> dict[str, Any]:
    """Authenticate an internal caller with Stargate's own API-key scheme."""
    from ..proxy.dependencies import get_auth_dependency

    return await get_auth_dependency(request)


async def require_edge_identity(request: Request) -> str:
    """Return the authenticated ``edge_id``, or refuse with 403.

    Raises:
        EdgeOpRefusedError: 403 ``edge_identity_refused`` when the header is
            missing, the bearer token is absent, or the pair does not match a
            configured ``EDGE_OPS_EDGE_TOKENS`` entry.
    """
    edge_id = (request.headers.get(EDGE_ID_HEADER) or "").strip()
    token = _bearer_token(request)
    expected = config.edge_token_map().get(edge_id) if edge_id else None
    if not edge_id or token is None or expected is None:
        raise EdgeOpRefusedError(403, CODE_IDENTITY_REFUSED, _REFUSAL_MESSAGE)
    if not secrets.compare_digest(token, expected):
        raise EdgeOpRefusedError(403, CODE_IDENTITY_REFUSED, _REFUSAL_MESSAGE)
    return edge_id


def _bearer_token(request: Request) -> str | None:
    """Extract the bearer credential, or ``None`` when there is not one."""
    header = request.headers.get("Authorization") or ""
    scheme, _, credentials = header.partition(" ")
    credentials = credentials.strip()
    if scheme.lower() != "bearer" or not credentials:
        return None
    return credentials
