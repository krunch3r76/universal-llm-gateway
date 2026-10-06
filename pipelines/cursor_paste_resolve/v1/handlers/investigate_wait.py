"""Wait + turn fetch for the investigate hop (agent-bus wait snapshot has no body)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root, workspaces_root

from ._message import extract_investigate_splice

# Keys returned by libs/agent_bus_store/routes/wait.py `_snapshot`. No turn text.
WAIT_SNAPSHOT_KEYS = frozenset(
    {
        "thread_id",
        "complete",
        "status",
        "producer",
        "producers",
        "suggested_next",
        "push_required",
        "next_poll_after_s",
        "turn_count",
        "thread_status",
        "pointer_read_at",
        "qualifying_reply_turn",
    }
)

_SIDECAR_LINE = re.compile(
    r"^Sidecar:\s+((?:cortex|workspaces)://\S+)\s*$", re.MULTILINE
)
_WAIT_ROUNDS = 24
_WAIT_TIMEOUT = 55.0
# httpx client timeout must exceed the blocking wait= slice or the first poll
# raises wait_transport while the admitted producer is still running.
WAIT_CLIENT_TIMEOUT = _WAIT_TIMEOUT + 15.0
_ULG = "universal-llm-gateway"


def wait_transport_backoff_s(round_index: int) -> float:
    """Cap post-admit transport retries so connection-refused cannot spin 24 rounds."""
    return float(min(2**round_index, 10))


def wait_snapshot_fixture(**overrides: Any) -> dict[str, Any]:
    """Exact `_snapshot` key set. Tests must not invent body/text/content."""
    payload: dict[str, Any] = {
        "thread_id": "1",
        "complete": True,
        "status": "complete",
        "producer": {},
        "producers": [],
        "suggested_next": {},
        "push_required": False,
        "next_poll_after_s": 0,
        "turn_count": 2,
        "thread_status": "active",
        "pointer_read_at": None,
        "qualifying_reply_turn": 2,
    }
    payload.update(overrides)
    return payload


def poll_hint_wait_target(
    dispatched: dict[str, Any],
    *,
    fallback_thread: str,
    fallback_after: int,
) -> tuple[str, int]:
    """Worker thread + after_turn from team_dispatch poll_hint (not mint/coord)."""
    hint = dispatched.get("poll_hint")
    args = hint.get("arguments") if isinstance(hint, dict) else None
    if not isinstance(args, dict):
        args = {}
    thread = str(
        args.get("thread") or dispatched.get("thread_id") or fallback_thread
    ).strip()
    after = args.get("after_turn")
    if not isinstance(after, int):
        after = fallback_after
    return thread or fallback_thread, after


def producer_terminal_without_reply(snapshot: dict[str, Any]) -> bool:
    """Pinned producer finished and wait still has no qualifying reply (a:38445)."""
    reply = snapshot.get("qualifying_reply_turn")
    if isinstance(reply, int) and reply >= 1:
        return False
    producer = snapshot.get("producer")
    if isinstance(producer, dict) and str(producer.get("state") or "") == "terminal":
        return True
    for row in snapshot.get("producers") or []:
        if isinstance(row, dict) and str(row.get("state") or "") == "terminal":
            return True
    return False


def read_share_uri(uri: str) -> str | None:
    """Read ``cortex://`` or ``workspaces://`` share text; refuse path escape."""
    text = (uri or "").strip()
    if text.startswith("cortex://"):
        root = cortex_files_root().resolve()
        path = (root / text.removeprefix("cortex://")).resolve()
    elif text.startswith("workspaces://"):
        rest = text.removeprefix("workspaces://")
        projects = workspaces_root().resolve()
        if projects.name == _ULG:
            projects = projects.parent
        path = (projects / rest).resolve()
        root = projects
    else:
        return None
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def read_cortex_share(uri: str) -> str | None:
    """Backward-compatible alias — prefer ``read_share_uri``."""
    return read_share_uri(uri)


def closeout_source_ref(body: str) -> str | None:
    """``source_ref`` or first share URI in a cursor-sdk CLOSEOUT JSON envelope."""
    text = (body or "").strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    ref = data.get("source_ref")
    if isinstance(ref, str) and ref.strip().startswith(("cortex://", "workspaces://")):
        return ref.strip()
    # Do not fall through evidence_uris.artifact_paths — an inline-splice
    # envelope listing an unrelated artifact would replace the body (15474 B4).
    return None


def resolve_turn_text(turn: dict[str, Any]) -> str:
    """Inline body, cortex/workspaces sidecar, or CLOSEOUT ``source_ref`` spill."""
    uri = str(turn.get("sidecar_uri") or "").strip()
    if uri:
        spilled = read_share_uri(uri)
        if spilled:
            return spilled
    body = str(turn.get("body") or "")
    match = _SIDECAR_LINE.search(body)
    if match:
        spilled = read_share_uri(match.group(1))
        if spilled:
            return spilled
    ref = closeout_source_ref(body)
    if ref:
        spilled = read_share_uri(ref)
        if spilled:
            return spilled
    return body


async def latest_turn_number(bus: Any, thread_id: str, headers: dict[str, str]) -> int:
    """Pin wait `after_turn` to the tip at admit so a rerun cannot match an old closeout."""
    resp = await bus.get(
        "/turns",
        params={"thread": thread_id, "last": 1},
        headers=headers,
    )
    data = resp.json() if resp.content else {}
    if not isinstance(data, dict):
        return 0
    turns = data.get("turns") or []
    if not turns or not isinstance(turns[0], dict):
        return 0
    return int(turns[0].get("turn_number") or 0)


async def fetch_qualifying_body(
    bus: Any,
    thread_id: str,
    turn_number: int,
    headers: dict[str, str],
) -> str:
    resp = await bus.get(
        "/turns/by-number",
        params={"thread": thread_id, "turn_number": str(turn_number)},
        headers=headers,
    )
    if resp.status_code >= 400:
        return ""
    data = resp.json() if resp.content else {}
    if not isinstance(data, dict):
        return ""
    return resolve_turn_text(data)


async def wait_sdk_closeout(
    bus: Any,
    thread_id: str,
    *,
    headers: dict[str, str],
    after_turn: int,
    execution_id: str,
) -> dict[str, Any]:
    """Poll wait `_snapshot`, then GET the qualifying turn body.

    ``producer_terminal`` fails closed on the first snapshot (no 24-round spin).
    When wait status stays ``no_new_turn``/``predicate_unmet`` but the pinned
    producer is already ``terminal``, fail closed the same way (a:38445) —
    do not burn the round budget.
    After admit (``execution_id`` pinned), a transport error retries the wait
    round instead of fail-closing the hop.
    """
    last: dict[str, Any] = {}
    last_transport: str | None = None
    params: dict[str, Any] = {
        "from_agent": "cursor-sdk",
        "wait": int(_WAIT_TIMEOUT),
        "completion": "proof_reply_from",
        "after_turn": int(after_turn),
    }
    if execution_id:
        params["execution_id"] = execution_id
    for round_index in range(_WAIT_ROUNDS):
        try:
            resp = await bus.get(
                f"/threads/{thread_id}/wait",
                params=params,
                headers=headers,
            )
        except Exception as exc:
            last_transport = f"wait transport_error: {exc}"
            if execution_id:
                await asyncio.sleep(wait_transport_backoff_s(round_index))
                continue
            return {
                "ok": False,
                "failure_class": "wait_transport",
                "error": last_transport,
            }
        try:
            last = resp.json() if resp.content else {}
        except Exception:
            last = {"raw": (resp.text or "")[:300]}
        if not isinstance(last, dict):
            last = {"value": last}
        if resp.status_code >= 400:
            last.setdefault("error", f"http_{resp.status_code}")
            return {
                "ok": False,
                "failure_class": "wait_http",
                "http_status": resp.status_code,
                "error": str(last.get("error")),
            }
        status = str(last.get("status") or "")
        if status == "producer_terminal" or (
            status in {"no_new_turn", "predicate_unmet"}
            and execution_id
            and producer_terminal_without_reply(last)
        ):
            return {
                "ok": False,
                "failure_class": "producer_terminal",
                "error": "investigate hop producer_terminal",
                "wait": last,
            }
        if status in {"no_new_turn", "predicate_unmet"}:
            continue
        if status != "complete":
            continue
        reply_turn = last.get("qualifying_reply_turn")
        if not isinstance(reply_turn, int) or reply_turn < 1:
            return {
                "ok": False,
                "failure_class": "missing_qualifying_turn",
                "error": "wait complete without qualifying_reply_turn",
                "wait": last,
            }
        body = await fetch_qualifying_body(bus, thread_id, reply_turn, headers)
        splice = extract_investigate_splice(body)
        if not splice:
            return {
                "ok": False,
                "failure_class": "empty_splice",
                "error": "investigate hop returned an empty splice",
                "wait": last,
                "body": body[:400],
            }
        return {"ok": True, "body": body, "splice": splice, "wait": last}
    if last_transport and not last:
        return {
            "ok": False,
            "failure_class": "wait_transport",
            "error": last_transport,
        }
    return {
        "ok": False,
        "failure_class": "wait_exhausted",
        "error": "investigate hop wait exhausted without a splice",
        "wait": last,
    }
