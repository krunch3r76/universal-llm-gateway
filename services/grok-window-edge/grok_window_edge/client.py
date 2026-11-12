"""The edge's view of Stargate: four calls, all outbound from here.

``StargateEdgeClient`` is the seam. The loop depends on the protocol, not on
HTTP, so an in-process test can drive the real Stargate service functions
without a socket, and the shipped implementation is the only place that knows
about URLs and tokens.

``HttpEdgeClient`` raises ``EdgeTransportError`` on anything that is not a
success, including a Stargate restart. That is what the loop's backoff reacts
to; there is no retry anywhere else, by design. One status is carved out of
that rule: a 409 is the authority's verdict on this op rather than a fault to
wait out, so it raises ``EdgeConflictError`` and the loop drops the op instead
of backing off against a Stargate that answered perfectly well.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from . import config

_EDGE_BASE_PATH = "/api/v1/edge"


class EdgeTransportError(Exception):
    """Stargate was unreachable or answered with an error status."""


class EdgeConflictError(Exception):
    """Stargate refused this write on the op's state, not on transport.

    A 409 says the authority disagrees about this op — it is terminal, or the
    lease is no longer ours — and no amount of waiting changes that answer.
    Keeping it off ``EdgeTransportError`` is what stops the loop treating a
    settled refusal as an outage and backing off against a healthy Stargate.

    Args:
        message: What was refused.
        code: The ``{"error": {code}}`` value when the envelope carried one,
            so a caller can tell ``edge_op_terminal`` from
            ``edge_lease_mismatch`` or ``edge_lease_stale`` without matching on
            message text.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class StargateEdgeClient(Protocol):
    """What the lease loop needs from Stargate."""

    async def lease(
        self,
        *,
        kinds: tuple[str, ...],
        wait_s: float,
    ) -> dict[str, Any] | None: ...

    async def post_event(
        self,
        *,
        op_id: str,
        lease_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def post_result(
        self,
        *,
        op_id: str,
        lease_id: str,
        result: Any,
        outcome: str,
    ) -> dict[str, Any]: ...

    async def release(self, *, op_id: str, lease_id: str) -> dict[str, Any]: ...


class HttpEdgeClient:
    """Edge-initiated HTTP against ``/api/v1/edge/*``."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_STARGATE_URL,
        edge_id: str | None = None,
        token: str | None = None,
        lease_timeout_s: float = 75.0,
    ) -> None:
        self._base_url = base_url
        self._edge_id = edge_id if edge_id is not None else config.edge_id()
        self._token = token if token is not None else config.edge_token()
        self._lease_timeout_s = lease_timeout_s

    def _headers(self) -> dict[str, str]:
        """Identity headers. The token is never logged from here."""
        return {
            "X-Edge-Id": self._edge_id,
            "Authorization": f"Bearer {self._token}",
        }

    async def lease(
        self,
        *,
        kinds: tuple[str, ...],
        wait_s: float,
    ) -> dict[str, Any] | None:
        """Long-poll for work. ``None`` on 204, meaning re-issue the request."""
        body = {"kinds": list(kinds), "edge_id": self._edge_id, "wait_s": wait_s}
        response = await self._request(
            "POST",
            f"{_EDGE_BASE_PATH}/leases",
            json_body=body,
            timeout_s=self._lease_timeout_s,
        )
        if response.status_code == 204:
            return None
        return self._decoded(response)

    async def post_event(
        self,
        *,
        op_id: str,
        lease_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Post one progress event; its 2xx also renews the lease."""
        response = await self._request(
            "POST",
            f"{_EDGE_BASE_PATH}/ops/{op_id}/events",
            json_body={"type": event_type, "lease_id": lease_id, "payload": payload},
        )
        return self._decoded(response)

    async def post_result(
        self,
        *,
        op_id: str,
        lease_id: str,
        result: Any,
        outcome: str,
    ) -> dict[str, Any]:
        """Post the handler result verbatim with its terminal outcome."""
        response = await self._request(
            "POST",
            f"{_EDGE_BASE_PATH}/ops/{op_id}/result",
            json_body={"result": result, "outcome": outcome, "lease_id": lease_id},
        )
        return self._decoded(response)

    async def release(self, *, op_id: str, lease_id: str) -> dict[str, Any]:
        """Hand the lease back without a result."""
        response = await self._request(
            "POST",
            f"{_EDGE_BASE_PATH}/ops/{op_id}/release",
            json_body={"lease_id": lease_id},
        )
        return self._decoded(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any],
        timeout_s: float = 30.0,
    ) -> httpx.Response:
        """Issue one request, mapping every transport fault to one error."""
        try:
            async with make_async_client(self._base_url, timeout=timeout_s) as client:
                response = await client.request(
                    method, path, json=json_body, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise EdgeTransportError(f"{method} {path} failed: {exc}") from exc
        if response.status_code == 409:
            raise _conflict(method, path, response)
        if response.status_code >= 400:
            raise EdgeTransportError(f"{method} {path} returned {response.status_code}")
        return response

    @staticmethod
    def _decoded(response: httpx.Response) -> dict[str, Any]:
        """Decode a success body, treating a malformed one as a transport fault."""
        try:
            decoded = response.json()
        except ValueError as exc:
            raise EdgeTransportError("Stargate returned a non-JSON body") from exc
        if not isinstance(decoded, dict):
            raise EdgeTransportError("Stargate returned a non-object body")
        return decoded


def _conflict(
    method: str,
    path: str,
    response: httpx.Response,
) -> EdgeConflictError:
    """Build the semantic refusal for a 409, carrying its envelope code.

    The code is best-effort: a 409 whose body is missing or malformed is still
    a 409, and the loop's decision — drop the op, do not back off — does not
    depend on reading it.
    """
    code: str | None = None
    try:
        decoded = response.json()
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        error = decoded.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            code = error["code"]
    return EdgeConflictError(
        f"{method} {path} refused with 409 ({code or 'no code'})", code=code
    )
