"""Post a terminal thread run onto agent-bus and journal the outcome.

The jobs process is the delivery actor. A failed post leaves the run
status unchanged and appends ``undelivered``. Recovery redelivers while
the attempt count is under the journal cap, so one 503 becomes one retry.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from jobs.journal import DELIVERY_ATTEMPT_CAP, Journal

logger = get_logger(__name__)

_RESULT_LIMIT = 1500


class Delivery:
    """Agent-bus projection of a terminal fold. Not a second run authority."""

    def __init__(self, journal: Journal, *, client_factory: Any | None = None) -> None:
        self.journal = journal
        self._client_factory = client_factory or make_async_client

    async def recover(self) -> None:
        """Redeliver each pending terminal thread run once per call."""
        for run_id in self.journal.pending_deliveries():
            await self.deliver(run_id)

    async def deliver(self, run_id: str) -> None:
        """POST ``/turns`` when the fold is a terminal thread run under the cap.

        Success appends ``delivered`` with the bus ``turn_number``. HTTP and
        transport failures append ``undelivered`` and leave run status as it
        was. A run already delivered, or already at the attempt cap, is skipped.
        """
        fold = self.journal.fold(run_id)
        if fold is None or fold.output_contract != "thread":
            return
        if fold.state not in {"completed", "failed", "cancelled", "lost"}:
            return
        if self.journal.delivery_attempts(run_id) >= DELIVERY_ATTEMPT_CAP:
            return
        latest = self.journal.latest_delivery(run_id)
        if latest is not None and latest[0] == "delivered":
            return
        attempt = self.journal.delivery_attempts(run_id) + 1
        token = os.environ.get("AGENT_BUS_TOKEN", "")
        body = _turn_body(fold)
        try:
            async with self._client_factory(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
                response = await client.post(
                    "/turns",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"} if token else {},
                )
        except httpx.HTTPError as exc:
            self.journal.append(
                run_id,
                "undelivered",
                {"error": str(exc), "attempt": attempt},
            )
            logger.warning("jobs delivery transport failure run=%s", run_id)
            return
        if response.status_code >= 400:
            self.journal.append(
                run_id,
                "undelivered",
                {"http_status": response.status_code, "attempt": attempt},
            )
            return
        payload = response.json()
        turn_number = int(payload.get("turn_number") or 0)
        self.journal.append(
            run_id,
            "delivered",
            {"turn_number": turn_number, "attempt": attempt},
        )


def _turn_body(fold: Any) -> dict[str, Any]:
    error = fold.data.get("error") or {}
    result = fold.data.get("result")
    result_text = json.dumps(result) if result is not None else ""
    if len(result_text) > _RESULT_LIMIT:
        result_text = result_text[:_RESULT_LIMIT]
    href = f"/api/v1/capabilities/jobs/{fold.job}/runs/{fold.run_id}"
    text = "\n".join(
        [
            f"run_id: {fold.run_id}",
            f"status: {fold.state}",
            f"exit_code: {fold.data.get('exit_code')}",
            f"error: {error.get('code')}",
            f"result: {result_text}",
            f"href: {href}",
        ]
    )
    return {
        "thread": fold.target_thread,
        "from": "jobs",
        "to": "cursor",
        "subject": f"jobs {fold.job} run {fold.run_id} {fold.state}",
        "body": text,
    }
