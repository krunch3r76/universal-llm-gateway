"""Thin sync HTTP cortex/bus reader for implement admission."""

from __future__ import annotations

import os
from typing import Any

from transport_utils import DEFAULT_AGENT_BUS_URL, DEFAULT_CORTEX_URL, make_sync_client

_CORTEX_TIMEOUT = 15.0


class StargateCortexReader:
    """Thin sync HTTP relay to cortex-api for implement_admission readers."""

    def _dispatch(self, tool: str, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        payload = {"tool": tool, "arguments": {"entity_id": entity_id, **kwargs}}
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
            resp = client.post("/dispatch", json=payload)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        return data

    def assertion_state(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._dispatch("assertion_state", entity_id, **kwargs)

    def assertions(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._dispatch("assertions", entity_id, **kwargs)

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        # Entity-seed normalize (todo:/plan:/plan_phase:) reads the entity here.
        # Without this method, _normalize_entity's broad except surfaces the
        # AttributeError as source_not_found — the bug stub readers masked.
        return self._dispatch("entity_get", entity_id, **kwargs)

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """WitnessCortex surface — G1 fold reads derived_from here."""
        kwargs: dict[str, Any] = {}
        if type_id:
            kwargs["type_id"] = type_id
        data = self._dispatch("relationships", entity_id, **kwargs)
        items = data.get("items")
        if items is None:
            items = data.get("relationships")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def assertion_get(self, assertion_id: int) -> dict[str, Any]:
        payload = {
            "tool": "assertion_get",
            "arguments": {"assertion_id": assertion_id},
        }
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
            resp = client.post("/dispatch", json=payload)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        return data

    def bus_turn_get(self, thread: str, turn_number: int) -> dict[str, Any] | None:
        """Read-only agent-bus turn lookup by thread + turn_number."""
        from urllib.parse import urlencode

        token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        qs = urlencode({"thread": str(thread), "turn_number": int(turn_number)})
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_CORTEX_TIMEOUT) as client:
            resp = client.get(f"/turns/by-number?{qs}", headers=headers)
            if resp.status_code >= 400:
                return None
            data = resp.json()
        return data if isinstance(data, dict) else None

    def bus_thread_last_turn(self, thread: str) -> dict[str, Any] | None:
        """Read-only fetch of the latest turn on an agent-bus thread."""
        from urllib.parse import urlencode

        token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        qs = urlencode({"thread": str(thread), "last": 1})
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_CORTEX_TIMEOUT) as client:
            resp = client.get(f"/turns?{qs}", headers=headers)
            if resp.status_code >= 400:
                return None
            payload = resp.json()
        if not isinstance(payload, dict):
            return None
        turns = payload.get("turns")
        if not isinstance(turns, list) or not turns:
            return None
        first = turns[0]
        return first if isinstance(first, dict) else None
