"""Debounced operator notification for grok auth expiry.

Posts an agent-bus message to the configured ``to`` seat when grok auth
is expired/missing, at most once per ``debounce_h`` hours.

Debounce latch: ``sidecar_dir/auth-alert.latch`` — plain text ISO timestamp
of the last successful notification.  File-based so it survives worker
restarts; operator may delete it to force re-notification.

∀ notify call: no-op if:
  - ``agent_bus_token`` is empty (config.agent_bus_token == ""), OR
  - latch file exists ∧ now - latch_ts < debounce_h hours
On successful POST: write/overwrite latch file.
On failed POST: log error; do NOT write latch (retry on next trigger).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_LATCH_FILENAME = "auth-alert.latch"
_POST_TIMEOUT_S = 10.0

_NOTIFY_BODY_TEMPLATE = """\
**GrokBuild auth expired** — the `grok` CLI OAuth token on the host has expired.
Dispatches will be rejected with `reason_code=missing_grok_auth` until the
operator re-authorizes.

**Steps to fix (run on the host):**

```bash
grok login --device-auth
# Follow the device-flow URL printed to the terminal
# After completing auth in the browser:
grok models   # verify — should return model list with exit 0
```

**Then verify in this session:**
- Reply to this thread with "grok auth restored" after completing the login.
- The grokbuild-worker will detect the restored state on the next probe
  (startup or periodic).

*Trigger*: {trigger}
*grok_auth_dir*: `{grok_auth_dir}`
*deploy_shape*: `{deploy_shape}`
"""


def _latch_path(sidecar_dir: Path) -> Path:
    return sidecar_dir / _LATCH_FILENAME


def _latch_active(sidecar_dir: Path, debounce_h: int) -> bool:
    """Return True if debounce latch is within the debounce window."""
    latch = _latch_path(sidecar_dir)
    if not latch.exists():
        return False
    try:
        raw = latch.read_text().strip()
        latch_ts = datetime.fromisoformat(raw)
        if latch_ts.tzinfo is None:
            latch_ts = latch_ts.replace(tzinfo=UTC)
        return datetime.now(UTC) - latch_ts < timedelta(hours=debounce_h)
    except (ValueError, OSError):
        return False


def _write_latch(sidecar_dir: Path) -> None:
    latch = _latch_path(sidecar_dir)
    try:
        latch.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as exc:
        logger.warning("auth_notifier: failed to write latch %s: %s", latch, exc)


def _clear_latch(sidecar_dir: Path) -> None:
    latch = _latch_path(sidecar_dir)
    try:
        latch.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("auth_notifier: failed to clear latch %s: %s", latch, exc)


def _post_turn(
    *,
    url: str,
    token: str,
    slug: str,
    to: str,
    subject: str,
    body: str,
    tags: list[str],
) -> tuple[int, str]:
    """Sync POST /threads/with-turn to agent-bus.  Never raises.

    Review §F2: /threads/with-turn binds ThreadWithTurnCreate, which requires
    ``slug`` (creates a NEW thread, numeric id auto-assigned) — NOT ``thread``.
    ``from`` is a plain str AgentName (no registry rejection, §F5).
    """
    import httpx
    from transport_utils import make_sync_client

    payload: dict[str, Any] = {
        "slug": slug,
        "from": "grokbuild-worker",
        "to": to,
        "subject": subject,
        "body": body,
        "tags": tags,
        "allow_long_body": True,
    }
    try:
        with make_sync_client(url, timeout=_POST_TIMEOUT_S) as client:
            resp = client.post(
                "/threads/with-turn",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            return resp.status_code, resp.text
    except httpx.HTTPError as exc:
        logger.error("auth_notifier: agent-bus transport error: %s", exc)
        return 599, f"transport_error: {exc}"


def notify_if_needed(
    *,
    sidecar_dir: Path,
    agent_bus_url: str,
    agent_bus_token: str,
    notify_slug: str,
    notify_to: str,
    debounce_h: int,
    trigger: str,
    grok_auth_dir: str,
    deploy_shape: str,
    debounce_key_out: list[str] | None = None,
) -> bool:
    """Fire operator notification if latch is inactive.

    Returns True if notification was sent, False if skipped (latch active,
    token missing, or POST failed).

    ``debounce_key_out``: if provided, appended with the latch ISO timestamp
    so the caller can embed it in the emitted event's ``debounce_key`` field.
    """
    if not agent_bus_token:
        logger.debug("auth_notifier: AGENT_BUS_TOKEN not configured; no-op")
        return False

    if _latch_active(sidecar_dir, debounce_h):
        logger.debug("auth_notifier: debounce latch active; skip notification")
        return False

    body = _NOTIFY_BODY_TEMPLATE.format(
        trigger=trigger,
        grok_auth_dir=grok_auth_dir,
        deploy_shape=deploy_shape,
    )
    status, text = _post_turn(
        url=agent_bus_url,
        token=agent_bus_token,
        slug=notify_slug,
        to=notify_to,
        subject="ACTION REQUIRED: grok auth expired — run grok login --device-auth",
        body=body,
        tags=[
            "type:operator-action",
            "subsystem:grokbuild",
            "action:grok-auth-refresh",
        ],
    )
    if status < 400:
        logger.info(
            "auth_notifier: posted auth-required notification to %s slug=%s",
            notify_to,
            notify_slug,
        )
        _write_latch(sidecar_dir)
        if debounce_key_out is not None:
            debounce_key_out.append(datetime.now(UTC).isoformat())
        return True
    logger.error(
        "auth_notifier: agent-bus POST failed status=%d body=%s", status, text[:200]
    )
    return False


async def notify_if_needed_async(
    *,
    sidecar_dir: Path,
    agent_bus_url: str,
    agent_bus_token: str,
    notify_slug: str,
    notify_to: str,
    debounce_h: int,
    trigger: str,
    grok_auth_dir: str,
    deploy_shape: str,
) -> bool:
    """Async wrapper — offloads blocking POST to the thread-pool executor."""
    return await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: notify_if_needed(
            sidecar_dir=sidecar_dir,
            agent_bus_url=agent_bus_url,
            agent_bus_token=agent_bus_token,
            notify_slug=notify_slug,
            notify_to=notify_to,
            debounce_h=debounce_h,
            trigger=trigger,
            grok_auth_dir=grok_auth_dir,
            deploy_shape=deploy_shape,
        ),
    )


def clear_notification_latch(sidecar_dir: Path) -> None:
    """Clear latch when auth is restored so next failure triggers a fresh notification."""
    _clear_latch(sidecar_dir)
    logger.info("auth_notifier: latch cleared (auth restored)")


def _cortex_todo_open(cortex_url: str, cortex_token: str) -> None:
    """Create a cortex todo entity for operator grok auth.  Best-effort."""
    from transport_utils import make_sync_client

    if not cortex_url or not cortex_token:
        return
    try:
        with make_sync_client(cortex_url, timeout=5.0) as client:
            client.post(
                "/dispatch",
                headers={"Authorization": f"Bearer {cortex_token}"},
                json={
                    "tool": "entity_create",
                    "arguments": {
                        "entity_id": "todo:operator-grok-auth",
                        "entity_type": "todo",
                        "title": "Re-authorize grok CLI: run grok login --device-auth",
                        "workflow_state": "open",
                        "tags": ["subsystem:grokbuild", "action:grok-auth-refresh"],
                    },
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to create cortex todo for grok auth: %s", exc)


def _cortex_todo_close(cortex_url: str, cortex_token: str) -> None:
    """Close the cortex todo entity on auth restore.  Best-effort."""
    from transport_utils import make_sync_client

    if not cortex_url or not cortex_token:
        return
    try:
        with make_sync_client(cortex_url, timeout=5.0) as client:
            client.post(
                "/dispatch",
                headers={"Authorization": f"Bearer {cortex_token}"},
                json={
                    "tool": "entity_update",
                    "arguments": {
                        "entity_id": "todo:operator-grok-auth",
                        "workflow_state": "completed",
                    },
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to close cortex todo for grok auth: %s", exc)
