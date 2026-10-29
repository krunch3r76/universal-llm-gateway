"""Hop Orion to a new chatgpt.com conversation (build-only; inject page, no CDP)."""

from __future__ import annotations

import re
from typing import Any

from agent_seat.registry import normalize_bus_address

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

_NEW_CHAT_SELECTOR = 'button[data-orion-action="new-chat-in-project"]'
_MODEL_PICKER_SELECTOR = 'button[data-orion-model-picker]'
_MODEL_LABEL_SELECTOR = "[data-orion-model-picker-label]"
_EFFORT_OPTION_SELECTOR = "[data-orion-effort-option]"

_THINKING_RANK = (
    ("extra high", 100),
    ("extended", 80),
    ("thinking", 60),
    ("high", 40),
)


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "pasted": False, **extra}


def build_hop_paste_text(birth_prompt: bytes) -> str:
    """Birth prompt bytes unchanged plus one final line pointing at the standing handoff."""
    body = birth_prompt.decode("utf-8")
    pointer = STANDING_HANDOFF_URI
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


def parse_orion_seat_registration(record: dict[str, Any] | None) -> dict[str, str] | None:
    """Validate a seat-registration bus turn for Orion repin."""
    if not record:
        return None
    if normalize_bus_address(str(record.get("from") or "")) != "orion":
        return None
    subject = str(record.get("subject") or "").strip()
    if not subject.startswith("SEAT_REGISTRATION"):
        return None
    body = str(record.get("body") or "")
    match = re.search(r"https://chatgpt\.com/c/[0-9a-f-]{36}", body, re.I)
    if not match:
        return None
    url = match.group(0).split("?", 1)[0]
    cid = conversation_id_from_url(url)
    if not cid or cid == PINNED_CONVERSATION_ID:
        return None
    return {"conversation_url": url, "conversation_id": cid}


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


async def orion_hop(page: OrionPage, birth_prompt: bytes) -> dict[str, Any]:
    """Open a new project conversation, select effort, paste hop prompt, approve connectors."""
    opened = await _open_new_conversation_in_project(page)
    if not opened.get("ok"):
        return opened

    effort = await select_orion_effort(page)
    if effort.get("outcome") == "effort_unavailable":
        return _refuse(
            "effort_unavailable",
            effort=effort,
            conversation_url=opened.get("conversation_url"),
        )

    scan_raw = await page.evaluate(_DIALOG_SCAN_JS)
    scan = scan_raw if isinstance(scan_raw, dict) else {"dialogs": []}
    blocking = _blocking_dialog(scan)
    if blocking is not None:
        return _refuse(
            "non_connector_dialog",
            dialog=blocking,
            effort=effort,
            conversation_url=opened.get("conversation_url"),
        )

    paste_text = build_hop_paste_text(birth_prompt)
    try:
        await _paste_and_send(page, paste_text)
        await page.wait_for_timeout(400)
        approval = await _handle_approvals(page)
    except Exception as exc:
        return _refuse(
            "paste_failed",
            error=type(exc).__name__,
            effort=effort,
            conversation_url=opened.get("conversation_url"),
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
            "conversation_url": opened.get("conversation_url"),
            "conversation_id": opened.get("conversation_id"),
            "paste_len": len(paste_text),
        }

    return {
        "ok": True,
        "reason": "hopped",
        "pasted": True,
        "effort": effort,
        "approval": approval,
        "connector_receipts": connector_receipts,
        "conversation_url": opened.get("conversation_url"),
        "conversation_id": opened.get("conversation_id"),
        "paste_len": len(paste_text),
        "pinned_conversation_preserved": PINNED_CONVERSATION_URL,
    }


async def repin_orion_wake_conversation(
    page: OrionPage,
    *,
    registration: dict[str, Any] | None,
    apply_repin=None,
) -> dict[str, Any]:
    """Repin the wake holder to the new /c/ URL after Orion SEAT_REGISTRATION."""
    parsed = parse_orion_seat_registration(registration)
    if not parsed:
        return _refuse("registration_invalid")
    url = parsed["conversation_url"]
    if apply_repin is not None:
        await apply_repin(page, url)
    elif hasattr(page, "repin_conversation"):
        await page.repin_conversation(url)  # type: ignore[attr-defined]
    return {
        "ok": True,
        "reason": "repinned",
        "conversation_url": url,
        "conversation_id": parsed["conversation_id"],
    }
