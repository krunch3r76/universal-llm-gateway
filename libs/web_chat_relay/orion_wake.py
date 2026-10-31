"""Paste pointer wakes into a pinned chatgpt.com conversation over CDP 9342.

Does not import ``orion_session`` or ``orion_cli`` (untracked helpers). Uses
``claude_bundles.skills_ui_panel.connect_cdp`` for live delivery only.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from typing import Any, Protocol
from urllib.parse import urlparse

DEFAULT_ORION_CDP_URL = os.environ.get("ORION_CDP_URL", "http://127.0.0.1:9342")
PINNED_CONVERSATION_URL = (
    "https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion"
    "/c/6ae44d27-06cc-83e8-8b8a-df9b819755bd"
)
PINNED_CONVERSATION_ID = "6ae44d27-06cc-83e8-8b8a-df9b819755bd"

_STREAMING_JS = """
() => [...document.querySelectorAll('button')].some(
  b => (b.getAttribute('aria-label') || '').toLowerCase() === 'stop'
)
"""

_TAB_SYNC_STATE_JS = """
() => {
  const composers = [...document.querySelectorAll('[contenteditable="true"]')];
  const composerEmpty = composers.every(el => !(el.innerText || '').trim());
  return { visibilityState: document.visibilityState, composerEmpty };
}
"""

_DIALOG_SCAN_JS = """
() => {
  const norm = (s) => (s || '').trim();
  const label = (b) => norm(b.innerText) || norm(b.getAttribute('aria-label'));
  const dialogs = [...document.querySelectorAll('[role=dialog],[role=alertdialog]')].map((dlg, index) => {
    const text = norm(dlg.innerText);
    const aria = norm(dlg.getAttribute('aria-label'));
    const hay = (text + ' ' + aria).toLowerCase();
    const buttons = [...dlg.querySelectorAll('button,[role=button]')]
      .map(b => ({ label: label(b), disabled: !!(b.disabled || b.getAttribute('aria-disabled') === 'true') }))
      .filter(b => b.label && !b.disabled);
    const labels = buttons.map(b => b.label);
    const floating = hay.includes('join ulg council') ||
      labels.some(l => /floating chat|dock chat|minimize chat/i.test(l));
    const connector = (/ulg-code|\\bulg code\\b|\\blife\\b/.test(hay) ||
      labels.some(l => /ulg-code|\\blife\\b/i.test(l))) && labels.length >= 2;
    const composersInDlg = dlg.querySelectorAll('[contenteditable="true"]').length;
    return { index, text: text.slice(0, 200), aria, labels, floating, connector, composersInDlg };
  });
  return { dialogs };
}
"""

_CONNECTOR_DIALOG_JS = """
() => {
  const norm = (s) => (s || '').trim();
  const label = (b) => norm(b.innerText) || norm(b.getAttribute('aria-label'));
  const dialogs = [...document.querySelectorAll('[role=dialog],[role=alertdialog]')].map((dlg, index) => {
    const text = norm(dlg.innerText);
    const aria = norm(dlg.getAttribute('aria-label'));
    const hay = (text + ' ' + aria).toLowerCase();
    const buttons = [...dlg.querySelectorAll('button,[role=button]')]
      .map(b => ({ label: label(b), disabled: !!(b.disabled || b.getAttribute('aria-disabled') === 'true') }))
      .filter(b => b.label && !b.disabled);
    const labels = buttons.map(b => b.label);
    const floating = hay.includes('join ulg council') ||
      labels.some(l => /floating chat|dock chat|minimize chat/i.test(l));
    const connector = (/ulg-code|\\bulg code\\b|\\blife\\b/.test(hay) ||
      labels.some(l => /ulg-code|\\blife\\b/i.test(l))) && labels.length >= 2;
    return { index, labels, floating, connector };
  });
  const hit = dialogs.find(d => d.connector);
  if (!hit) return { present: false, buttons: [] };
  return { present: true, buttons: hit.labels, index: hit.index };
}
"""


def conversation_id_from_url(url: str) -> str:
    """Extract chatgpt.com conversation UUID from flat ``/c/{id}`` or project ``/g/.../c/{id}`` paths."""
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "c":
        return parts[1]
    if "c" in parts:
        idx = parts.index("c")
        if idx + 1 < len(parts) and re.fullmatch(
            r"[0-9a-f-]{36}", parts[idx + 1], flags=re.I
        ):
            return parts[idx + 1]
    return ""


def _strip_conversation_url(url: str) -> str:
    return url.split("?", 1)[0]


_NEW_EXACT_URL_RE = re.compile(r"New exact URL:\s*(https://chatgpt\.com/\S+)", re.I)
_CHAT_URL_LINE_RE = re.compile(r"chat_url:\s*(https://chatgpt\.com/\S+)", re.I)
_CHATGPT_CONVERSATION_RE = re.compile(r"https://chatgpt\.com/\S+/c/[0-9a-fA-F-]{36}")


def extract_orion_seat_registration_url(body: str, subject: str = "") -> str | None:
    """ChatGPT conversation URL from an Orion hop ``SEAT_REGISTRATION`` turn body."""
    text = str(body or "")
    subj = str(subject or "").strip()
    if not subj.upper().startswith("SEAT_REGISTRATION"):
        if (
            not text.startswith("TYPE: SEAT_REGISTRATION")
            and "SEAT_REGISTRATION" not in text
        ):
            return None

    match = _NEW_EXACT_URL_RE.search(text)
    if match is not None:
        return _seat_registration_url_from_raw(match.group(1))

    match = _CHAT_URL_LINE_RE.search(text)
    if match is not None:
        return _seat_registration_url_from_raw(match.group(1))

    for line in text.splitlines():
        if line.strip().startswith("Old exact URL:"):
            continue
        for hit in _CHATGPT_CONVERSATION_RE.finditer(line):
            cleaned = _seat_registration_url_from_raw(hit.group(0))
            if cleaned:
                return cleaned
    return None


def _seat_registration_url_from_raw(raw: str) -> str | None:
    trimmed = raw.rstrip(".,;)")
    url = _strip_conversation_url(trimmed)
    if conversation_id_from_url(url):
        return url
    return None


def _explicit_holder_url(raw: str) -> str | None:
    if raw.startswith("http") and "/c/" in raw:
        return _strip_conversation_url(raw)
    return None


def _matching_explicit_url_for_uuid(sub: dict[str, Any], conv_uuid: str) -> str | None:
    """Project-qualified URL in title/chat_url when it matches bare agent_id."""
    want = conv_uuid.lower()
    for key in ("title", "chat_url"):
        raw = str(sub.get(key) or "").strip()
        if not raw:
            continue
        explicit = _explicit_holder_url(raw)
        if explicit is None:
            continue
        if conversation_id_from_url(explicit).lower() == want:
            return explicit
    return None


def holder_conversation_url(sub: dict[str, Any] | None) -> str:
    """Resolve chatgpt.com /c/ URL from subscription holder title, chat_url, or agent_id."""
    if not sub:
        return PINNED_CONVERSATION_URL
    for key in ("agent_id", "title", "chat_url"):
        raw = str(sub.get(key) or "").strip()
        if not raw:
            continue
        explicit = _explicit_holder_url(raw)
        if explicit is not None:
            return explicit
        if re.fullmatch(r"[0-9a-f-]{36}", raw, flags=re.I):
            if key == "agent_id":
                matched = _matching_explicit_url_for_uuid(sub, raw)
                if matched is not None:
                    return matched
            return f"https://chatgpt.com/c/{raw}"
        cid = conversation_id_from_url(raw)
        if cid:
            return f"https://chatgpt.com/c/{cid}"
    return PINNED_CONVERSATION_URL


def parse_turn_numbers(raw: Any) -> list[int] | None:
    """Parse turn: int, numeric str, comma-separated str, or list of those."""
    if raw is None or raw is True or raw is False:
        return None
    if isinstance(raw, int):
        return [raw] if raw > 0 else None
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text == "0":
            return None
        parts = (
            [p.strip() for p in text.split(",") if p.strip()] if "," in text else [text]
        )
        out: list[int] = []
        for part in parts:
            try:
                num = int(part)
            except ValueError:
                return None
            if num <= 0:
                return None
            out.append(num)
        return out or None
    if isinstance(raw, (list, tuple)):
        out: list[int] = []
        for item in raw:
            parsed = parse_turn_numbers(item)
            if parsed is None or len(parsed) != 1:
                return None
            out.append(parsed[0])
        return out or None
    return None


def format_turn_pointer_label(turn_numbers: list[int]) -> str:
    """Human batch label for pointer text, e.g. 69–73 for a contiguous run."""
    nums = sorted(set(int(n) for n in turn_numbers))
    if not nums:
        return ""
    if len(nums) == 1:
        return str(nums[0])
    if nums == list(range(nums[0], nums[-1] + 1)):
        return f"{nums[0]}–{nums[-1]}"
    return ", ".join(str(n) for n in nums)


def build_wake_pointer(
    *,
    thread: str,
    turn: int | str,
    from_agent: str,
    reply_to: str | None = None,
) -> str:
    """Format the short Orion pointer line pasted into the chatgpt.com composer."""
    from agent_seat.registry import normalize_bus_address

    sender = (from_agent or "unknown").strip()
    turn_s = str(turn)
    multi = "–" in turn_s or "," in turn_s
    turn_word = "turns" if multi else "turn"
    if reply_to is not None and str(reply_to).strip():
        reply_target = normalize_bus_address(str(reply_to).strip())
    else:
        first_sender = sender.split(",")[0].strip() or "unknown"
        reply_target = normalize_bus_address(first_sender)
    return (
        f"WAKE — agent-bus thread {thread} {turn_word} {turn_s} (from {sender}). "
        f"Use your ulg-code connector: agent_bus_read fetch thread {thread}, "
        f"read {turn_word} {turn_s} only. Reply on thread {thread} to={reply_target} with "
        f"subject ANSWER|… per that turn. Do not paste letter drafts."
    )


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "pasted": False, "delivered": False, **extra}


class OrionPage(Protocol):
    """Minimal Playwright page surface used by Orion wake paste helpers (offline unit tests use fakes)."""

    url: str

    async def evaluate(self, expression: str) -> Any: ...

    async def keyboard(self) -> Any: ...

    def locator(self, selector: str) -> Any: ...

    async def wait_for_timeout(self, ms: int) -> None: ...


async def _is_streaming(page: OrionPage) -> bool:
    return bool(await page.evaluate(_STREAMING_JS))


def _blocking_dialog(scan: dict[str, Any]) -> dict[str, Any] | None:
    for dlg in scan.get("dialogs") or []:
        if dlg.get("connector") or dlg.get("floating"):
            continue
        return dlg
    return None


async def _sync_tab_before_paste(page: OrionPage, *, target_id: str) -> dict[str, Any]:
    """Foreground a background tab; reload only when hidden, empty, and not streaming."""
    brought_to_front = False
    tab_reloaded = False

    raw = await page.evaluate(_TAB_SYNC_STATE_JS)
    if isinstance(raw, dict):
        visibility = str(raw.get("visibilityState") or "visible")
        composer_empty_raw = raw.get("composerEmpty")
        composer_empty = (
            True if composer_empty_raw is None else bool(composer_empty_raw)
        )
    else:
        visibility = "visible"
        composer_empty = True

    bring_fn = getattr(page, "bring_to_front", None)
    if callable(bring_fn):
        await bring_fn()
        brought_to_front = True

    reload_fn = getattr(page, "reload", None)
    tab_streaming = await _is_streaming(page)
    if (
        visibility == "hidden"
        and composer_empty
        and not tab_streaming
        and callable(reload_fn)
    ):
        await reload_fn(wait_until="domcontentloaded")
        await page.wait_for_timeout(300)
        tab_reloaded = True

        if target_id not in (page.url or ""):
            return {
                "brought_to_front": brought_to_front,
                "tab_reloaded": tab_reloaded,
                "refuse": _refuse("conversation_missing", conversation_id=target_id),
            }

        scan_raw = await page.evaluate(_DIALOG_SCAN_JS)
        scan = scan_raw if isinstance(scan_raw, dict) else {"dialogs": []}
        blocking = _blocking_dialog(scan)
        if blocking is not None:
            return {
                "brought_to_front": brought_to_front,
                "tab_reloaded": tab_reloaded,
                "refuse": _refuse(
                    "non_connector_dialog",
                    pasted=False,
                    dialog=blocking,
                ),
            }

        post_raw = await page.evaluate(_TAB_SYNC_STATE_JS)
        if isinstance(post_raw, dict):
            composer_empty_raw = post_raw.get("composerEmpty")
            composer_empty = (
                True if composer_empty_raw is None else bool(composer_empty_raw)
            )

    if not composer_empty:
        return {
            "brought_to_front": brought_to_front,
            "tab_reloaded": tab_reloaded,
            "refuse": _refuse("non_empty_composer", pasted=False),
        }

    return {"brought_to_front": brought_to_front, "tab_reloaded": tab_reloaded}


async def _prepare_composer(page: OrionPage) -> dict[str, Any]:
    """Resolve composer for floating chat; dock as a receipted fallback."""
    receipt: dict[str, Any] = {"docked": False, "layout": "docked"}
    raw = await page.evaluate(_DIALOG_SCAN_JS)
    scan = raw if isinstance(raw, dict) else {"dialogs": []}
    floating_open = any(d.get("floating") for d in scan.get("dialogs") or [])
    if not floating_open:
        return receipt

    inner_ce = await page.evaluate(
        """
() => {
  const dlg = [...document.querySelectorAll('[role=dialog],[role=alertdialog]')].find(d => {
    const hay = ((d.innerText || '') + ' ' + (d.getAttribute('aria-label') || '')).toLowerCase();
    const labels = [...d.querySelectorAll('button,[role=button]')].map(
      b => (b.getAttribute('aria-label') || b.innerText || '').toLowerCase()
    );
    return hay.includes('join ulg council') ||
      labels.some(l => l.includes('floating chat') || l.includes('dock chat'));
  });
  if (!dlg) return 0;
  return dlg.querySelectorAll('[contenteditable="true"]').length;
}
"""
    )
    if int(inner_ce or 0) > 0:
        receipt["layout"] = "floating"
        return receipt

    dock = page.locator('button[aria-label="Dock Chat"]').first
    if await dock.count() and await dock.is_visible():
        await dock.click()
        receipt["docked"] = True
        receipt["layout"] = "docked"
        await page.wait_for_timeout(500)
        return receipt

    receipt["layout"] = "floating"
    return receipt


async def _visible_submit_button(page: OrionPage, selector: str) -> Any | None:
    loc = page.locator(selector).first
    if await loc.count() and await loc.is_visible():
        return loc
    return None


async def _steer_submit_button(page: OrionPage) -> Any | None:
    root = page.locator("button")
    filter_fn = getattr(root, "filter", None)
    if callable(filter_fn):
        try:
            steer = filter_fn(has_text="Steer").first
            if await steer.count() and await steer.is_visible():
                return steer
        except TypeError:
            pass
    for selector in (
        'form button[aria-label="Steer"]',
        'button[aria-label="Steer"]',
        'button[aria-label*="Steer"]',
    ):
        hit = await _visible_submit_button(page, selector)
        if hit is not None:
            return hit
    return None


async def _paste_and_send(
    page: OrionPage, text: str, *, layout: str = "docked"
) -> None:
    if layout == "floating":
        comp = page.locator(
            '[role=dialog] [contenteditable="true"], [role=alertdialog] [contenteditable="true"]'
        ).first
        if not await comp.count():
            comp = page.locator('[contenteditable="true"]').last
    else:
        comp = page.locator('[contenteditable="true"]').last
    await comp.click()
    kb = page.keyboard
    await kb.press("Control+A")
    await kb.insert_text(text)
    steer = None
    if await _is_streaming(page):
        steer = await _steer_submit_button(page)
    if steer is not None:
        await steer.click()
        return
    send = page.locator('form button[aria-label="Send"]').first
    if await send.count() and await send.is_visible():
        await send.click()
        return
    await comp.press("Enter")


async def _approval_surface(page: OrionPage) -> str:
    """Classify open dialogs while waiting for connector approval modals."""
    raw = await page.evaluate(_DIALOG_SCAN_JS)
    scan = raw if isinstance(raw, dict) else {"dialogs": []}
    dlgs = scan.get("dialogs") or []
    if not dlgs:
        return "empty"
    if any(d.get("connector") for d in dlgs):
        return "connector"
    if all(d.get("floating") for d in dlgs):
        return "floating_only"
    return "other"


async def _handle_approvals(
    page: OrionPage, *, max_dialogs: int = 2, timeout_s: float = 10.0
) -> dict[str, Any]:
    """Click the second option on each connector dialog (ulg-code, then life)."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    dialogs: list[dict[str, Any]] = []
    while asyncio.get_event_loop().time() < deadline and len(dialogs) < max_dialogs:
        raw = await page.evaluate(_CONNECTOR_DIALOG_JS)
        last = raw if isinstance(raw, dict) else {"present": False, "buttons": []}
        if not last.get("present"):
            if dialogs:
                break
            surface = await _approval_surface(page)
            if surface == "floating_only":
                return {"outcome": "approval_absent", "dialogs": dialogs}
            await page.wait_for_timeout(200)
            if asyncio.get_event_loop().time() >= deadline:
                return {"outcome": "approval_absent", "dialogs": dialogs}
            continue
        buttons = list(last.get("buttons") or [])
        if len(buttons) < 2:
            return {
                "outcome": "approval_unrecognized",
                "dialogs": dialogs,
                "buttons": buttons,
            }
        idx = int(last.get("index") or 0)
        dlg = page.locator("[role=dialog], [role=alertdialog]").nth(idx)
        opts = dlg.locator("button, [role=button]")
        await opts.nth(1).click()
        dialogs.append(
            {
                "buttons": buttons,
                "clicked_index": 1,
                "clicked_text": buttons[1],
            }
        )
        await page.wait_for_timeout(600)
    if not dialogs:
        return {"outcome": "approval_absent", "dialogs": dialogs}
    return {"outcome": "approval_complete", "dialogs": dialogs}


async def deliver_orion_wake(
    *,
    thread: str,
    turn: int | str,
    from_agent: str,
    conversation_url: str | None = None,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    page: OrionPage | None = None,
    connect_cdp=None,
    paste_text: str | None = None,
) -> dict[str, Any]:
    """Attach to pinned chat, paste a pointer wake, optional MCP approval click."""
    from agent_seat.registry import normalize_bus_address

    if normalize_bus_address(from_agent) == "orion":
        return _refuse("orion_echo_drop")

    thread_s = str(thread or "").strip()
    if not thread_s or turn in (None, "", 0, "0"):
        return _refuse("bad_options")

    target_url = (conversation_url or PINNED_CONVERSATION_URL).split("?", 1)[0]
    target_id = conversation_id_from_url(target_url)
    if not target_id:
        return _refuse("bad_conversation_url", conversation_url=target_url)

    owns_cdp = False
    pw = None
    if page is None:
        connect = connect_cdp
        if connect is None:
            from claude_bundles.skills_ui_panel import connect_cdp as connect

        try:
            pw, _browser, ctx, _page0 = await connect(cdp_url)
        except Exception as exc:
            return _refuse("cdp_unreachable", error=type(exc).__name__)
        owns_cdp = True
        page = None
        for p in ctx.pages:
            if target_id in (p.url or ""):
                page = p
                break
        if page is None:
            if owns_cdp:
                await pw.stop()
            return _refuse("conversation_missing", conversation_id=target_id)

    assert page is not None
    if target_id not in (page.url or ""):
        if owns_cdp and pw is not None:
            await pw.stop()
        return _refuse("conversation_missing", conversation_id=target_id)

    scan_raw = await page.evaluate(_DIALOG_SCAN_JS)
    scan = scan_raw if isinstance(scan_raw, dict) else {"dialogs": []}
    blocking = _blocking_dialog(scan)
    if blocking is not None:
        if owns_cdp and pw is not None:
            await pw.stop()
        return _refuse(
            "non_connector_dialog",
            pasted=False,
            dialog=blocking,
        )

    tab_sync = await _sync_tab_before_paste(page, target_id=target_id)
    if tab_sync.get("refuse") is not None:
        if owns_cdp and pw is not None:
            await pw.stop()
        return {
            **tab_sync["refuse"],
            "brought_to_front": tab_sync["brought_to_front"],
            "tab_reloaded": tab_sync["tab_reloaded"],
        }

    layout_receipt = await _prepare_composer(page)
    line = (
        paste_text
        if paste_text is not None
        else build_wake_pointer(thread=thread_s, turn=turn, from_agent=from_agent)
    )
    try:
        await _paste_and_send(
            page, line, layout=str(layout_receipt.get("layout") or "docked")
        )
        await page.wait_for_timeout(400)
        approval = await _handle_approvals(page)
    except Exception as exc:
        if owns_cdp and pw is not None:
            await pw.stop()
        return _refuse(
            "paste_failed",
            error=type(exc).__name__,
            brought_to_front=tab_sync["brought_to_front"],
            tab_reloaded=tab_sync["tab_reloaded"],
        )

    if owns_cdp and pw is not None:
        await pw.stop()

    sync_flags = {
        "brought_to_front": tab_sync["brought_to_front"],
        "tab_reloaded": tab_sync["tab_reloaded"],
    }
    if approval.get("outcome") == "approval_unrecognized":
        return {
            "ok": False,
            "reason": "approval_unrecognized",
            "pasted": True,
            "delivered": False,
            "approval": approval,
            "pointer_len": len(line),
            **layout_receipt,
            **sync_flags,
        }

    return {
        "ok": True,
        "reason": "delivered",
        "pasted": True,
        "delivered": True,
        "approval": approval,
        "pointer_len": len(line),
        "conversation_id": target_id,
        **layout_receipt,
        **sync_flags,
    }


def _validate_call_against_claim(
    claim: Any,
    *,
    thread: str,
    turn: int | str,
    conversation_url: str | None,
) -> dict[str, Any] | None:
    thread_s = str(thread or "").strip()
    if thread_s != str(claim.thread_id):
        return _refuse("wake_claim_thread_mismatch")
    turn_list = parse_turn_numbers(turn)
    if turn_list is None:
        return _refuse("bad_turn")
    if tuple(turn_list) != tuple(int(n) for n in claim.turn_numbers):
        return _refuse("wake_claim_turn_mismatch")
    if conversation_url:
        supplied = str(conversation_url).split("?", 1)[0]
        if supplied != claim.normalized_endpoint():
            return _refuse("wake_claim_endpoint_mismatch")
    return None


async def deliver_orion_chat_wake(
    *,
    thread: str,
    turn: int | str,
    from_agent: str,
    conversation_url: str | None = None,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    page: OrionPage | None = None,
    connect_cdp=None,
    pipeline_options: dict[str, Any] | None = None,
    delivery_owner: Any | None = None,
) -> dict[str, Any]:
    """Orion-chat-wake entry: shared owner claim/dedupe, then CDP paste (no re-claim)."""
    from agent_bus_store.db.orion_wake_owner_adapter import (
        LedgerWakeDeliveryOwner,
        WakeClaimContext,
        enrich_delivery_receipt,
        resolve_turn_id_for_wake,
        shared_wake_delivery_owner,
        wake_claim_from_options,
    )

    opts = dict(pipeline_options or {})
    opts.setdefault("thread", thread)
    opts.setdefault("turn", turn)
    if conversation_url:
        opts.setdefault("conversation_url", conversation_url)
    claim = wake_claim_from_options(opts)
    if claim is None:
        thread_s = str(thread or "").strip()
        endpoint = (conversation_url or PINNED_CONVERSATION_URL).split("?", 1)[0]
        turn_list = parse_turn_numbers(turn)
        if turn_list is None or len(turn_list) != 1:
            return _refuse("bad_turn")
        turn_n = turn_list[0]
        turn_id = resolve_turn_id_for_wake(thread_id=thread_s, turn_number=turn_n)
        if turn_id is None:
            return _refuse("turn_id_unresolved")
        claim = WakeClaimContext(
            wake_id=str(opts.get("wake_id") or "").strip() or uuid.uuid4().hex,
            agent=str(opts.get("agent") or "orion"),
            thread_id=thread_s,
            turn_ids=(turn_id,),
            turn_numbers=(turn_n,),
            endpoint_url=endpoint,
        )
    mismatch = _validate_call_against_claim(
        claim,
        thread=thread,
        turn=turn,
        conversation_url=conversation_url,
    )
    if mismatch is not None:
        return mismatch
    owner: LedgerWakeDeliveryOwner
    if delivery_owner is None:
        owner = shared_wake_delivery_owner()
    else:
        owner = delivery_owner

    prepared = owner.prepare_paste_delivery(claim)
    if not prepared.get("ok"):
        return prepared
    if prepared.get("reason") == "duplicate_suppressed":
        return prepared

    pointer_turn = format_turn_pointer_label(list(claim.turn_numbers))
    from web_chat_relay.orion_evidence_card import (
        compose_orion_chat_wake_paste,
        load_wake_turns_for_claim,
    )

    wake_turns = load_wake_turns_for_claim(
        thread_id=str(claim.thread_id),
        turn_numbers=list(claim.turn_numbers),
    )
    paste_text = compose_orion_chat_wake_paste(
        thread=str(claim.thread_id),
        turn=pointer_turn or turn,
        from_agent=from_agent,
        turns=wake_turns,
    )
    receipt = await deliver_orion_wake(
        thread=thread,
        turn=pointer_turn or turn,
        from_agent=from_agent,
        conversation_url=claim.normalized_endpoint(),
        cdp_url=cdp_url,
        page=page,
        connect_cdp=connect_cdp,
        paste_text=paste_text,
    )
    # Why: live deliver_orion_wake steers during streaming; deferred_streaming receipts
    # remain for legacy in-flight ledger retry rows and injected pipeline responses.
    if receipt.get("reason") == "deferred_streaming":
        receipt = {**receipt, "ok": False, "retry_eligible": True}
    owner.record_paste_receipt(claim, receipt)
    return enrich_delivery_receipt(receipt, claim)
