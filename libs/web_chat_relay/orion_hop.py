"""Hop Orion to a new chatgpt.com conversation (build-only; inject page, no CDP)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from agent_seat.registry import normalize_bus_address

from web_chat_relay.orion_hop_storage import (
    OrionHopAttempt,
    OrionHopStorage,
    new_attempt_id,
)
from web_chat_relay.orion_wake import (
    _DIALOG_SCAN_JS,
    PINNED_CONVERSATION_ID,
    PINNED_CONVERSATION_URL,
    OrionPage,
    _blocking_dialog,
    _handle_approvals,
    _paste_and_send,
    conversation_id_from_url,
)

STANDING_HANDOFF_URI = "cortex://notes/system/threads/21207-standing-handoff.md"
ORION_IDENTITY_TOKEN = "ai_agent:orion"
ORION_REGISTRATION_SUBJECT = "SEAT_REGISTRATION|orion"

_NEW_CHAT_SELECTOR = 'button[data-orion-action="new-chat-in-project"]'
_MODEL_PICKER_SELECTOR = 'button[data-orion-model-picker]'
_MODEL_LABEL_SELECTOR = "[data-orion-model-picker-label]"
_EFFORT_OPTION_SELECTOR = "[data-orion-effort-option]"

HandoffRead = Callable[[], Awaitable[dict[str, Any]]]

_THINKING_RANK = (
    ("extra high", 100),
    ("extended", 80),
    ("thinking", 60),
    ("high", 40),
)


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "pasted": False, **extra}


def build_identity_pointer_line(
    *,
    old_url: str,
    new_url: str,
    handoff_read_sha256: str,
    identity_token: str = ORION_IDENTITY_TOKEN,
    handoff_uri: str = STANDING_HANDOFF_URI,
) -> str:
    """Endpoint hop pointer: identity token, old/new /c/ URLs, handoff fs read receipt."""
    old_s = old_url.split("?", 1)[0]
    new_s = new_url.split("?", 1)[0]
    return (
        f"{handoff_uri} — identity {identity_token}; endpoint {old_s} → {new_s}; "
        f"read_sha256 {handoff_read_sha256}"
    )


def build_hop_paste_text(
    birth_prompt: bytes,
    *,
    old_url: str,
    new_url: str,
    handoff_read_sha256: str,
) -> str:
    """Birth prompt bytes unchanged plus the identity / endpoint pointer line."""
    body = birth_prompt.decode("utf-8")
    pointer = build_identity_pointer_line(
        old_url=old_url,
        new_url=new_url,
        handoff_read_sha256=handoff_read_sha256,
    )
    if body.endswith("\n"):
        return body + pointer
    return body + "\n" + pointer


def _thinking_rank(label: str) -> int:
    low = label.lower()
    best = 0
    for needle, score in _THINKING_RANK:
        if needle in low:
            best = max(best, score)
    return best


def _is_pro_label(label: str) -> bool:
    return bool(re.search(r"\bpro\b", label, re.I))


def _connector_name_from_dialog(dialog: dict[str, Any]) -> str:
    hay = " ".join(
        [
            str(dialog.get("text") or ""),
            str(dialog.get("aria") or ""),
            " ".join(dialog.get("labels") or []),
        ]
    ).lower()
    if re.search(r"ulg-code|\bulg code\b", hay):
        return "ulg-code"
    if re.search(r"\blife\b", hay):
        return "life"
    clicked = str(dialog.get("clicked_text") or "").lower()
    if "ulg-code" in clicked or "ulg code" in clicked:
        return "ulg-code"
    if "life" in clicked:
        return "life"
    return "connector"


def _enrich_approval_receipts(approval: dict[str, Any]) -> list[dict[str, str]]:
    receipts: list[dict[str, str]] = []
    for dlg in approval.get("dialogs") or []:
        if not isinstance(dlg, dict):
            continue
        receipts.append(
            {
                "connector": _connector_name_from_dialog(dlg),
                "option_clicked": str(dlg.get("clicked_text") or ""),
            }
        )
    return receipts


def _urls_in_body(body: str) -> list[str]:
    found = re.findall(
        r"https://chatgpt\.com/c/[0-9a-f-]{36}",
        body,
        flags=re.I,
    )
    return [u.split("?", 1)[0] for u in found]


def _registration_body_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in str(body or "").splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("identity:"):
            fields["identity"] = stripped.split(":", 1)[1].strip()
        elif low.startswith("old:"):
            fields["old_url"] = stripped.split(":", 1)[1].strip().split("?", 1)[0]
        elif low.startswith("new:"):
            fields["new_url"] = stripped.split(":", 1)[1].strip().split("?", 1)[0]
    return fields


def parse_orion_seat_registration(record: dict[str, Any] | None) -> dict[str, str] | None:
    """Validate exact ``SEAT_REGISTRATION|orion`` from Orion with bound old/new + digest."""
    if not record:
        return None
    if normalize_bus_address(str(record.get("from") or "")) != "orion":
        return None
    subject = str(record.get("subject") or "").strip()
    if subject != ORION_REGISTRATION_SUBJECT:
        return None
    body = str(record.get("body") or "")
    fields = _registration_body_fields(body)
    identity = fields.get("identity", "")
    if identity != ORION_IDENTITY_TOKEN:
        return None
    sha_match = re.search(r"read_sha256\s+([0-9a-f]{64})", body, re.I)
    if not sha_match:
        return None
    old_url = fields.get("old_url", "")
    new_url = fields.get("new_url", "")
    if not old_url or not new_url:
        urls = _urls_in_body(body)
        if len(urls) < 2:
            return None
        new_candidates = [
            u for u in urls if conversation_id_from_url(u) != PINNED_CONVERSATION_ID
        ]
        if not new_candidates:
            return None
        new_url = new_candidates[-1]
        old_candidates = [u for u in urls if u != new_url]
        if not old_candidates:
            return None
        old_url = old_candidates[0]
    old_url = old_url.split("?", 1)[0]
    new_url = new_url.split("?", 1)[0]
    if old_url == new_url:
        return None
    if STANDING_HANDOFF_URI not in body and "21207-standing-handoff" not in body:
        return None
    cid = conversation_id_from_url(new_url)
    if not cid:
        return None
    return {
        "conversation_url": new_url,
        "conversation_id": cid,
        "old_conversation_url": old_url,
        "identity_token": ORION_IDENTITY_TOKEN,
        "handoff_read_sha256": sha_match.group(1).lower(),
    }


def registration_matches_attempt(
    parsed: dict[str, str], attempt: OrionHopAttempt
) -> bool:
    return (
        parsed.get("identity_token") == attempt.identity
        and parsed.get("old_conversation_url") == attempt.normalized_old()
        and parsed.get("conversation_url") == attempt.normalized_new()
        and parsed.get("handoff_read_sha256") == attempt.handoff_read_sha256.lower()
    )


def smoke_receipt_binds_attempt(
    smoke: dict[str, Any],
    *,
    attempt: OrionHopAttempt,
    candidate_url: str,
) -> bool:
    candidate = candidate_url.split("?", 1)[0]
    if smoke.get("ok") is not True or smoke.get("delivered") is not True:
        return False
    if str(smoke.get("attempt_id") or "") != attempt.attempt_id:
        return False
    bound = str(smoke.get("candidate_url") or smoke.get("conversation_url") or "")
    return bound.split("?", 1)[0] == candidate


def build_hop_attempt(
    *,
    old_url: str,
    new_url: str,
    handoff_read_sha256: str,
    attempt_id: str | None = None,
) -> OrionHopAttempt:
    return OrionHopAttempt(
        attempt_id=attempt_id or new_attempt_id(),
        identity=ORION_IDENTITY_TOKEN,
        expected_old_url=old_url.split("?", 1)[0],
        intended_new_url=new_url.split("?", 1)[0],
        handoff_read_sha256=handoff_read_sha256.lower(),
    )


async def preflight_standing_handoff(read_handoff: HandoffRead | None) -> dict[str, Any]:
    """Refuse hop when the standing-handoff note does not read."""
    if read_handoff is None:
        return _refuse("handoff_missing")
    try:
        raw = await read_handoff()
    except Exception:
        return _refuse("handoff_missing")
    if not isinstance(raw, dict) or raw.get("ok") is not True:
        return _refuse("handoff_missing")
    sha = str(raw.get("read_sha256") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        return _refuse("handoff_missing")
    return {"ok": True, "read_sha256": sha, "uri": STANDING_HANDOFF_URI}


class OrionWakeTargetRollback:
    """In-memory wake target; commit requires attempt-bound smoke receipt."""

    def __init__(self, initial_url: str) -> None:
        self.wake_target_url = initial_url.split("?", 1)[0]

    def commit_after_smoke(
        self,
        *,
        candidate_url: str,
        smoke: dict[str, Any],
        attempt: OrionHopAttempt,
    ) -> dict[str, Any]:
        candidate = candidate_url.split("?", 1)[0]
        if not smoke_receipt_binds_attempt(
            smoke, attempt=attempt, candidate_url=candidate
        ):
            return {
                "ok": False,
                "reason": "smoke_not_bound",
                "wake_target_url": self.wake_target_url,
                "candidate_url": candidate,
            }
        previous = self.wake_target_url
        self.wake_target_url = candidate
        return {
            "ok": True,
            "reason": "wake_target_committed",
            "previous_wake_target_url": previous,
            "wake_target_url": self.wake_target_url,
        }


class WakeTargetWriter(Protocol):
    async def write_wake_target(
        self, *, expected_old: str, new_url: str
    ) -> dict[str, Any]: ...


class WakeTargetReader(Protocol):
    async def read_wake_target(self) -> str: ...


class OrionWakeDedupeLedger:
    """At-most-once delivery per (thread, turn, endpoint) with durable receipts."""

    def __init__(self, storage: OrionHopStorage | None = None) -> None:
        self._storage = storage or OrionHopStorage()
        self._mem_first: dict[tuple[str, str, str], dict[str, Any]] = {}

    @staticmethod
    def _mem_key(thread: str, turn: int | str, endpoint_url: str) -> tuple[str, str, str]:
        return (str(thread or "").strip(), str(turn), endpoint_url.split("?", 1)[0])

    def _claim_sync(
        self,
        *,
        thread: str,
        turn: int | str,
        endpoint_url: str,
        kind: str,
    ) -> tuple[str, dict[str, Any] | None]:
        return self._storage.claim_delivery(
            thread=thread, turn=turn, endpoint_url=endpoint_url, kind=kind
        )

    def _finish_sync(
        self,
        *,
        thread: str,
        turn: int | str,
        endpoint_url: str,
        kind: str,
        receipt: dict[str, Any],
        ambiguous: bool,
    ) -> None:
        self._storage.finish_delivery(
            thread=thread,
            turn=turn,
            endpoint_url=endpoint_url,
            kind=kind,
            receipt=receipt,
            ambiguous=ambiguous,
        )

    async def deliver(
        self,
        *,
        thread: str,
        turn: int | str,
        endpoint_url: str,
        deliver_fn: Callable[[], Awaitable[dict[str, Any]]],
        kind: str = "pointer_wake",
    ) -> dict[str, Any]:
        mem_key = self._mem_key(thread, turn, endpoint_url)
        if mem_key in self._mem_first:
            return {
                "ok": True,
                "reason": "duplicate_suppressed",
                "delivered": False,
                "pasted": False,
                "first_receipt": self._mem_first[mem_key],
            }
        status, first = await asyncio.to_thread(
            self._claim_sync,
            thread=thread,
            turn=turn,
            endpoint_url=endpoint_url,
            kind=kind,
        )
        if status == "duplicate":
            return {
                "ok": True,
                "reason": "duplicate_suppressed",
                "delivered": False,
                "pasted": False,
                "first_receipt": first or {},
            }
        if status == "in_flight":
            return {
                "ok": False,
                "reason": "delivery_in_flight",
                "delivered": False,
                "pasted": False,
            }
        ambiguous = False
        try:
            receipt = await deliver_fn()
        except Exception as exc:
            receipt = _refuse("deliver_fn_failed", error=type(exc).__name__)
            ambiguous = True
        if receipt.get("pasted") or receipt.get("delivered"):
            self._mem_first[mem_key] = receipt
        if receipt.get("reason") == "deferred_streaming":
            ambiguous = True
        await asyncio.to_thread(
            self._finish_sync,
            thread=thread,
            turn=turn,
            endpoint_url=endpoint_url,
            kind=kind,
            receipt=receipt,
            ambiguous=ambiguous,
        )
        if ambiguous and receipt.get("pasted"):
            receipt = {
                **receipt,
                "ok": False,
                "reason": "delivery_ambiguous",
            }
        return receipt


async def _open_new_conversation_in_project(page: OrionPage) -> dict[str, Any]:
    before_id = conversation_id_from_url(page.url or "")
    btn = page.locator(_NEW_CHAT_SELECTOR).first
    if not await btn.count():
        return _refuse("new_chat_control_missing")
    await btn.click()
    new_url = (page.url or "").split("?", 1)[0]
    new_id = conversation_id_from_url(new_url)
    if not new_id:
        return _refuse("new_conversation_url_missing")
    if new_id == PINNED_CONVERSATION_ID:
        return _refuse("new_conversation_same_as_pinned")
    if before_id and new_id == before_id and new_url == PINNED_CONVERSATION_URL:
        return _refuse("new_conversation_not_opened")
    return {
        "ok": True,
        "conversation_url": new_url,
        "conversation_id": new_id,
    }


async def _read_model_picker_label(page: OrionPage) -> str:
    loc = page.locator(_MODEL_LABEL_SELECTOR).first
    if await loc.count():
        return (await loc.inner_text()).strip()
    loc = page.locator(_MODEL_PICKER_SELECTOR).first
    if await loc.count():
        return (await loc.inner_text()).strip()
    return ""


async def _collect_effort_options(page: OrionPage) -> list[dict[str, Any]]:
    root = page.locator(_EFFORT_OPTION_SELECTOR)
    count = await root.count()
    options: list[dict[str, Any]] = []
    for i in range(count):
        opt = root.nth(i)
        label = (await opt.inner_text()).strip()
        locked = False
        if hasattr(opt, "is_disabled"):
            locked = bool(await opt.is_disabled())
        options.append({"label": label, "locked": locked, "index": i})
    return options


async def _click_effort_option(page: OrionPage, index: int) -> None:
    await page.locator(_EFFORT_OPTION_SELECTOR).nth(index).click()


async def select_orion_effort(page: OrionPage) -> dict[str, Any]:
    """Read picker label, prefer Pro, else highest thinking-effort option shown."""
    label_before = await _read_model_picker_label(page)
    picker = page.locator(_MODEL_PICKER_SELECTOR).first
    if await picker.count():
        await picker.click()

    options = await _collect_effort_options(page)
    if not options:
        return {
            "outcome": "effort_unavailable",
            "label_before": label_before,
            "label_after": label_before,
        }

    pro_idx: int | None = None
    for opt in options:
        if _is_pro_label(opt["label"]):
            pro_idx = int(opt["index"])
            if not opt.get("locked"):
                await _click_effort_option(page, pro_idx)
                label_after = await _read_model_picker_label(page)
                return {
                    "outcome": "effort_selected",
                    "label_before": label_before,
                    "label_after": label_after,
                    "selected_label": opt["label"],
                }
            break

    thinking = [o for o in options if _thinking_rank(o["label"]) > 0 and not o.get("locked")]
    if thinking:
        best = max(thinking, key=lambda o: _thinking_rank(o["label"]))
        await _click_effort_option(page, int(best["index"]))
        label_after = await _read_model_picker_label(page)
        return {
            "outcome": "effort_selected",
            "label_before": label_before,
            "label_after": label_after,
            "selected_label": best["label"],
            "pro_locked": pro_idx is not None,
        }

    return {
        "outcome": "effort_unavailable",
        "label_before": label_before,
        "label_after": label_before,
    }


async def orion_hop(
    page: OrionPage,
    birth_prompt: bytes,
    *,
    read_handoff: HandoffRead | None = None,
    storage: OrionHopStorage | None = None,
) -> dict[str, Any]:
    """Open a new project conversation, select effort, paste hop prompt, approve connectors."""
    handoff = await preflight_standing_handoff(read_handoff)
    if not handoff.get("ok"):
        return handoff

    old_url = (page.url or PINNED_CONVERSATION_URL).split("?", 1)[0]

    opened = await _open_new_conversation_in_project(page)
    if not opened.get("ok"):
        return opened

    new_url = str(opened.get("conversation_url") or "")

    effort = await select_orion_effort(page)
    if effort.get("outcome") == "effort_unavailable":
        return _refuse(
            "effort_unavailable",
            effort=effort,
            conversation_url=new_url,
            old_conversation_url=old_url,
        )

    scan_raw = await page.evaluate(_DIALOG_SCAN_JS)
    scan = scan_raw if isinstance(scan_raw, dict) else {"dialogs": []}
    blocking = _blocking_dialog(scan)
    if blocking is not None:
        return _refuse(
            "non_connector_dialog",
            dialog=blocking,
            effort=effort,
            conversation_url=new_url,
            old_conversation_url=old_url,
        )

    handoff_sha = str(handoff.get("read_sha256") or "")
    paste_text = build_hop_paste_text(
        birth_prompt,
        old_url=old_url,
        new_url=new_url,
        handoff_read_sha256=handoff_sha,
    )
    try:
        await _paste_and_send(page, paste_text)
        await page.wait_for_timeout(400)
        approval = await _handle_approvals(page)
    except Exception as exc:
        return _refuse(
            "paste_failed",
            error=type(exc).__name__,
            effort=effort,
            conversation_url=new_url,
            old_conversation_url=old_url,
        )

    connector_receipts = _enrich_approval_receipts(approval)
    if approval.get("outcome") == "approval_unrecognized":
        return {
            "ok": False,
            "reason": "approval_unrecognized",
            "pasted": True,
            "effort": effort,
            "approval": approval,
            "connector_receipts": connector_receipts,
            "conversation_url": new_url,
            "conversation_id": opened.get("conversation_id"),
            "old_conversation_url": old_url,
            "handoff_read_sha256": handoff_sha,
            "identity_token": ORION_IDENTITY_TOKEN,
            "paste_len": len(paste_text),
        }

    attempt = build_hop_attempt(
        old_url=old_url,
        new_url=new_url,
        handoff_read_sha256=handoff_sha,
    )
    if storage is not None:
        storage.save_pending_attempt(attempt)
    return {
        "ok": True,
        "reason": "hopped",
        "pasted": True,
        "effort": effort,
        "approval": approval,
        "connector_receipts": connector_receipts,
        "conversation_url": new_url,
        "conversation_id": opened.get("conversation_id"),
        "old_conversation_url": old_url,
        "handoff_read_sha256": handoff_sha,
        "identity_token": ORION_IDENTITY_TOKEN,
        "attempt_id": attempt.attempt_id,
        "hop_attempt": attempt.to_dict(),
        "paste_len": len(paste_text),
        "pinned_conversation_preserved": PINNED_CONVERSATION_URL,
    }


def resolve_orion_wake_conversation_url(
    subscription: dict[str, Any] | None,
    *,
    storage: OrionHopStorage | None = None,
) -> str:
    """Production wake target: persisted hop commit overrides subscription holder."""
    from web_chat_relay.orion_wake import holder_conversation_url

    default = holder_conversation_url(subscription)
    store = storage or OrionHopStorage()
    return store.read_wake_target_url(default=default)


async def deliver_orion_chat_wake(
    *,
    thread: str,
    turn: int | str,
    from_agent: str,
    conversation_url: str | None = None,
    cdp_url: str | None = None,
    page: OrionPage | None = None,
    connect_cdp=None,
    storage: OrionHopStorage | None = None,
    subscription: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Production entry: durable dedupe ledger + ``deliver_orion_wake`` paste gate."""
    from web_chat_relay.orion_wake import DEFAULT_ORION_CDP_URL, deliver_orion_wake

    store = storage or OrionHopStorage()
    target = conversation_url or resolve_orion_wake_conversation_url(
        subscription, storage=store
    )
    ledger = OrionWakeDedupeLedger(store)

    async def _paste() -> dict[str, Any]:
        return await deliver_orion_wake(
            thread=thread,
            turn=turn,
            from_agent=from_agent,
            conversation_url=target,
            cdp_url=cdp_url or DEFAULT_ORION_CDP_URL,
            page=page,
            connect_cdp=connect_cdp,
        )

    return await ledger.deliver(
        thread=thread,
        turn=turn,
        endpoint_url=target,
        deliver_fn=_paste,
        kind="pointer_wake",
    )


async def record_orion_smoke_receipt(
    *,
    attempt: OrionHopAttempt,
    candidate_url: str,
    wake_result: dict[str, Any],
    storage: OrionHopStorage | None = None,
) -> dict[str, Any]:
    """Bind a successful smoke wake to a hop attempt (required before repin)."""
    store = storage or OrionHopStorage()
    receipt = {
        **wake_result,
        "attempt_id": attempt.attempt_id,
        "candidate_url": candidate_url.split("?", 1)[0],
        "identity": attempt.identity,
        "handoff_read_sha256": attempt.handoff_read_sha256,
    }
    if wake_result.get("delivered") is True and wake_result.get("ok") is True:
        store.save_smoke_receipt(attempt.attempt_id, receipt)
    return receipt


async def repin_orion_wake_conversation(
    page: OrionPage,
    *,
    registration: dict[str, Any] | None,
    attempt: OrionHopAttempt | None = None,
    smoke_receipt: dict[str, Any] | None = None,
    storage: OrionHopStorage | None = None,
    writer: WakeTargetWriter | None = None,
    reader: WakeTargetReader | None = None,
    apply_repin: Callable[[OrionPage, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Repin wake holder after validated registration + attempt-bound smoke receipt."""
    parsed = parse_orion_seat_registration(registration)
    if not parsed:
        return _refuse("registration_invalid")
    store = storage or OrionHopStorage()
    bound_attempt = attempt or store.load_pending_attempt()
    if bound_attempt is None:
        return _refuse("attempt_missing")
    if not registration_matches_attempt(parsed, bound_attempt):
        return _refuse("attempt_registration_mismatch")
    smoke = smoke_receipt or store.load_smoke_receipt(bound_attempt.attempt_id)
    if smoke is None:
        return _refuse("smoke_receipt_missing")
    new_url = parsed["conversation_url"]
    if not smoke_receipt_binds_attempt(
        smoke, attempt=bound_attempt, candidate_url=new_url
    ):
        return _refuse("smoke_not_bound")
    expected_old = bound_attempt.normalized_old()
    if reader is not None:
        current = (await reader.read_wake_target()).split("?", 1)[0]
        if current != expected_old:
            return _refuse(
                "wake_target_stale",
                current_wake_target_url=current,
                expected_old_url=expected_old,
            )
    wrote = False
    if writer is not None:
        cas = await writer.write_wake_target(expected_old=expected_old, new_url=new_url)
        if not cas.get("ok"):
            return {**cas, "ok": False, "pasted": False}
        wrote = True
    elif apply_repin is not None:
        await apply_repin(page, new_url)
        wrote = True
    elif hasattr(page, "repin_conversation"):
        await page.repin_conversation(new_url)  # type: ignore[attr-defined]
        wrote = True
    else:
        return _refuse("wake_target_writer_missing")
    persist = await asyncio.to_thread(
        lambda: store.compare_and_set_wake_target(
            expected_old=expected_old,
            new_url=new_url,
        )
    )
    if not persist.get("ok"):
        if wrote:
            return {
                **persist,
                "ok": False,
                "reason": "wake_target_uncertain_after_write",
                "pasted": False,
            }
        return {**persist, "ok": False, "pasted": False}
    store.clear_pending_attempt(bound_attempt.attempt_id)
    return {
        "ok": True,
        "reason": "repinned",
        "conversation_url": new_url,
        "conversation_id": parsed["conversation_id"],
        "attempt_id": bound_attempt.attempt_id,
        "wake_target_url": persist.get("wake_target_url"),
    }
