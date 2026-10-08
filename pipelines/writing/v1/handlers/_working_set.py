"""Read-only working-set client for the writer-specialist assemble step.

WritingAssembleHandler is the only caller. The client loads cortex entities,
assertions, cortex notes, and agent-bus turns. It never posts, sends, or
marks a thread read. Transport failure, timeout, HTTP 400+, and not-found
raise WorkingSetUnavailable so assemble can refuse before any generate step.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    make_async_client,
)

CORTEX_TOOLS = frozenset({"entity_get", "assertion_get"})
_REQUEST_TIMEOUT_S = 15.0


class WorkingSetUnavailable(Exception):  # noqa: N818
    """A working-set ref could not be read, so assemble must refuse.

    ``ref`` is the caller's uri. ``reason`` is timeout, not_found, an HTTP
    status, or a transport label. The assemble handler catches this and
    returns a refusal payload instead of calling a model.
    """

    def __init__(self, ref: str, reason: str) -> None:
        self.ref = ref
        self.reason = reason
        super().__init__(f"{ref}: {reason}")


def _bus_headers() -> dict[str, str]:
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def _body_not_found(body: Any) -> bool:
    if not isinstance(body, dict) or "error" not in body:
        return False
    blob = str(body.get("error")).lower()
    return "not found" in blob or "not_found" in blob


class WorkingSetClient:
    """Fetch pins for one assemble call without mutating cortex or the bus.

    Construct one client per assemble execution. Each public method opens its
    own HTTP client, so there is no shared socket to close. ``read_note``
    stays inside ``cortex_files_root`` and returns the file bytes' sha256.
    """

    async def entity_get(self, entity_id: str) -> dict[str, Any]:
        """Load one cortex entity by id through ``/dispatch``.

        ``entity_id`` is the bare id, without the ``entity:`` prefix. Returns
        the JSON object from cortex-api. Raises ``WorkingSetUnavailable`` on
        transport failure, timeout, HTTP 400+, or a not-found body.
        """
        return await self._cortex(
            "entity_get",
            {"entity_id": entity_id},
            ref=f"entity:{entity_id}",
        )

    async def assertion_get(self, assertion_id: int) -> dict[str, Any]:
        """Load one cortex assertion by integer id through ``/dispatch``.

        ``assertion_id`` is the bare integer from an ``a:`` ref. Returns the
        JSON object. Raises ``WorkingSetUnavailable`` on the same transport
        and not-found conditions as ``entity_get``.
        """
        return await self._cortex(
            "assertion_get",
            {"assertion_id": assertion_id},
            ref=f"a:{assertion_id}",
        )

    async def read_note(self, uri: str) -> tuple[str, str]:
        """Read a ``cortex://`` note from the files root and hash its bytes.

        Paths that resolve outside ``cortex_files_root`` are refused. Returns
        ``(text, sha256_hex)``. A missing file or unreadable path raises
        ``WorkingSetUnavailable`` with ``ref`` set to ``uri``.
        """
        if not uri.startswith("cortex://") or uri == "cortex://":
            raise WorkingSetUnavailable(uri, "bad_uri")
        root = cortex_files_root().resolve()
        relative = uri.removeprefix("cortex://").lstrip("/")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise WorkingSetUnavailable(uri, "path_escapes_root") from exc
        if not path.is_file():
            raise WorkingSetUnavailable(uri, "not_found")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise WorkingSetUnavailable(uri, f"transport:{type(exc).__name__}") from exc
        try:
            text = data.decode("utf-8")
        except UnicodeError as exc:
            raise WorkingSetUnavailable(uri, "malformed_response") from exc
        return text, hashlib.sha256(data).hexdigest()

    async def bus_read(self, thread: str, turn: int | None = None) -> Any:
        """GET agent-bus turns for ``thread``, optionally one ``turn``.

        Always sends ``mark_read=false``. Returns the JSON body (a turn list
        or an object). Raises ``WorkingSetUnavailable`` on timeout, transport
        error, HTTP 400+, or not-found. The ref is ``agent-bus:<thread>`` or
        ``agent-bus:<thread>#<turn>``.
        """
        ref = f"agent-bus:{thread}" if turn is None else f"agent-bus:{thread}#{turn}"
        params: dict[str, Any] = {"thread": str(thread), "mark_read": "false"}
        if turn is not None:
            params["turn_number"] = turn
        body = await self._http(
            ref,
            DEFAULT_AGENT_BUS_URL,
            lambda client: client.get("/turns", params=params, headers=_bus_headers()),
        )
        return body

    async def _cortex(
        self, tool: str, arguments: dict[str, Any], *, ref: str
    ) -> dict[str, Any]:
        if tool not in CORTEX_TOOLS:
            raise WorkingSetUnavailable(ref, "tool_forbidden")
        body = await self._http(
            ref,
            DEFAULT_CORTEX_URL,
            lambda client: client.post(
                "/dispatch",
                json={"tool": tool, "arguments": arguments},
            ),
        )
        if not isinstance(body, dict):
            raise WorkingSetUnavailable(ref, "malformed_response")
        return body

    async def _http(self, ref: str, base_url: str, call: Any) -> Any:
        try:
            async with make_async_client(
                base_url, timeout=_REQUEST_TIMEOUT_S
            ) as client:
                resp = await call(client)
        except WorkingSetUnavailable:
            raise
        except Exception as exc:
            reason = (
                "timeout" if _is_timeout(exc) else f"transport:{type(exc).__name__}"
            )
            raise WorkingSetUnavailable(ref, reason) from exc
        status = int(getattr(resp, "status_code", 0) or 0)
        try:
            body = resp.json()
        except Exception:
            body = None
        if status == 404 or _body_not_found(body):
            raise WorkingSetUnavailable(ref, "not_found")
        if status >= 400:
            raise WorkingSetUnavailable(ref, f"http_{status}")
        if isinstance(body, dict) and body.get("error"):
            raise WorkingSetUnavailable(ref, "error_body")
        if body is None:
            raise WorkingSetUnavailable(ref, "malformed_response")
        return body
