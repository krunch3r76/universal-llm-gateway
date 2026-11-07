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


def _dom_inserted_but_unsent(result: dict[str, Any]) -> bool:
    """True when insertion succeeded but send actuation did not run.

    Requires ``reason=no_send_button`` plus ``pasted``, ``inserted``, or
    ``phase`` inserted/send_unknown from the inner DOM layer — not bare
    ``no_send_button`` alone.
    """
    reason = str(result.get("reason") or "")
    if reason != "no_send_button":
        return False
    if result.get("pasted") is True:
        return True
    if result.get("inserted") is True:
        return True
    phase = str(result.get("phase") or "")
    return phase in {"inserted", "send_unknown"}


_open_lock_registry_guard = asyncio.Lock()
_open_locks_by_conversation: dict[str, asyncio.Lock] = {}
_attach_misses: dict[str, int] = {}
_ATTACH_MISS_LIMIT = 3


async def _conversation_open_lock(conversation_id: str) -> asyncio.Lock:
    key = conversation_id.lower()
    async with _open_lock_registry_guard:
        lock = _open_locks_by_conversation.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _open_locks_by_conversation[key] = lock
        return lock


def _normalize_stored_endpoint(raw: str) -> str:
    return str(raw or "").split("?", 1)[0].strip()


def _is_chatgpt_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().removeprefix("www.")
    return host == "chatgpt.com"


def _pages_for_conversation_id(pages: Any, conversation_id: str) -> list[Any]:
    want = conversation_id.lower()
    matches: list[Any] = []
    for page in pages:
        url = str(getattr(page, "url", "") or "")
        parsed = urlparse(url)
        if not _is_chatgpt_host(parsed.hostname):
            continue
        cid = conversation_id_from_url(url)
        if cid and cid.lower() == want:
            matches.append(page)
    return matches


def _tab_row_matches_conversation_id(row: dict[str, Any], conversation_id: str) -> bool:
    url = str(row.get("url") or "")
    parsed = urlparse(url)
    if not _is_chatgpt_host(parsed.hostname):
        return False
    cid = conversation_id_from_url(url)
    return bool(cid and cid.lower() == conversation_id.lower())


def _clear_attach_misses(conversation_id: str) -> None:
    _attach_misses.pop(conversation_id.lower(), None)


def _refuse_cdp_row_poll_miss(conversation_id: str, target_id: str) -> dict[str, Any]:
    key = conversation_id.lower()
    count = _attach_misses.get(key, 0) + 1
    _attach_misses[key] = count
    if count >= _ATTACH_MISS_LIMIT:
        return _refuse(
            "conversation_tab_unattachable",
            conversation_id=conversation_id,
            retryable=False,
            target_id=target_id,
        )
    return _refuse(
        "conversation_missing",
        conversation_id=conversation_id,
        retryable=True,
        target_id=target_id,
    )


def _validate_stored_binding_endpoint(stored_endpoint: str) -> dict[str, Any] | None:
    url = _normalize_stored_endpoint(stored_endpoint)
    if not conversation_id_from_url(url):
        return _refuse("bad_conversation_url", conversation_url=url)
    return None


def _field_qualifies_as_stored_conversation_url(raw: str) -> bool:
    text = str(raw or "").strip()
    if not text.startswith("http") or "/c/" not in text:
        return False
    return bool(conversation_id_from_url(text))


def _stored_endpoint_from_delivery_binding(binding_reference: str) -> str:
    """Read subscription row URL fields by id; no synthesis or pinned fallback."""
    from agent_bus_store.db.connection import connect

    ref = str(binding_reference or "").strip()
    if not ref:
        return ""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions WHERE id=? LIMIT 1",
            (ref,),
        ).fetchone()
    if row is None:
        return ""
    data = dict(row)
    for key in ("title", "agent_id", "chat_url"):
        raw = str(data.get(key) or "").strip()
        if raw and _field_qualifies_as_stored_conversation_url(raw):
            return _normalize_stored_endpoint(raw)
    for key in ("title", "agent_id", "chat_url"):
        raw = str(data.get(key) or "").strip()
        if raw:
            return raw
    return ""


def _refuse_unless_binding_row_matches_claim(
    binding_reference: str,
    claim_endpoint: str,
) -> dict[str, Any] | None:
    """Refuse synthesized claim URLs unless the subscription row stored a /c/ URL."""
    from agent_bus_store.db.connection import connect

    claim_norm = _normalize_stored_endpoint(claim_endpoint)
    ref = str(binding_reference or "").strip()
    if not ref:
        return _refuse(
            "bad_conversation_url",
            stored_endpoint="",
            opened_url=claim_norm,
        )
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions WHERE id=? LIMIT 1",
            (ref,),
        ).fetchone()
    if row is None:
        return _refuse(
            "bad_conversation_url",
            stored_endpoint="",
            opened_url=claim_norm,
        )
    data = dict(row)
    for key in ("title", "agent_id", "chat_url"):
        raw = str(data.get(key) or "").strip()
        if raw and _field_qualifies_as_stored_conversation_url(raw):
            if _normalize_stored_endpoint(raw) == claim_norm:
                return None
            return _refuse(
                "bad_conversation_url",
                stored_endpoint=raw,
                opened_url=claim_norm,
            )
    first_raw = ""
    for key in ("title", "agent_id", "chat_url"):
        raw = str(data.get(key) or "").strip()
        if raw:
            first_raw = raw
            break
    return _refuse(
        "bad_conversation_url",
        stored_endpoint=first_raw,
        opened_url=claim_norm,
    )


def _target_id_from_cdp_rows(
    rows: list[dict[str, Any]],
    conversation_id: str,
    page: Any | None,
) -> str | None:
    """Pick receipt target id: sole CDP match, else the page's own target id."""
    matching = [
        r for r in rows if _tab_row_matches_conversation_id(r, conversation_id)
    ]
    if len(matching) == 1:
        tid = str(matching[0].get("id") or "").strip()
        return tid or None
    if page is not None:
        tid = getattr(page, "target_id", None)
        if tid:
            text = str(tid).strip()
            return text or None
    return None


async def _poll_playwright_page_for_conversation(
    ctx: Any,
    conversation_id: str,
    *,
    attempts: int = 20,
    delay_s: float = 0.25,
) -> Any | None:
    for _ in range(attempts):
        matches = _pages_for_conversation_id(ctx.pages, conversation_id)
        if matches:
            return matches[0]
        await asyncio.sleep(delay_s)
    return None


async def _open_orion_binding_tab(
    ctx: Any,
    *,
    stored_endpoint: str,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    store_path: Any | None = None,
    binding_reference: str | None = None,
) -> dict[str, Any]:
    """Open the binding row URL verbatim (hidden) or reuse an existing conversation tab.

    CDP rows count as a match only on chatgpt.com (same host rule as
    ``_pages_for_conversation_id``). When a matching row exists but Playwright
    never attaches, return ``conversation_missing`` (retryable) until three
    consecutive poll misses for that conversation id in this process; the third
    returns ``conversation_tab_unattachable`` (not retryable). That path neither
    closes nor opens tabs. The miss counter is in-memory only and resets on
    process restart. Successful attach clears the counter.
    """
    if binding_reference:
        gate = _refuse_unless_binding_row_matches_claim(
            binding_reference, stored_endpoint
        )
        if gate is not None:
            return gate
        stored = _stored_endpoint_from_delivery_binding(binding_reference)
        if not stored:
            return _refuse(
                "bad_conversation_url",
                stored_endpoint="",
                opened_url=_normalize_stored_endpoint(stored_endpoint),
            )
    else:
        stored = _normalize_stored_endpoint(stored_endpoint)
    invalid = _validate_stored_binding_endpoint(stored)
    if invalid is not None:
        return invalid
    conversation_id = conversation_id_from_url(stored)
    assert conversation_id

    lock = await _conversation_open_lock(conversation_id)
    async with lock:
        existing = _pages_for_conversation_id(ctx.pages, conversation_id)
        if existing:
            _clear_attach_misses(conversation_id)
            page0 = existing[0]
            from web_tab.cdp_actuation import default_actuation_port

            impl0 = default_actuation_port()
            listing0 = impl0.list_tabs(cdp_url=cdp_url)
            rows0 = (
                list(listing0.get("pages") or [])
                if listing0.get("reachable")
                else []
            )
            return {
                "ok": True,
                "page": page0,
                "opened": False,
                "reused": True,
                "closed": False,
                "target_id": _target_id_from_cdp_rows(rows0, conversation_id, page0),
                "policy_just_opened": False,
            }

        from web_tab.cdp_actuation import default_actuation_port
        from web_tab.config import normalize_cdp_store_key
        from web_tab.provider_selectors import surface_for_site
        from web_tab.tab_binding_store import save_binding
        from web_tab.tab_session import tab_session_from_row
        from web_tab.target_lock import TargetLockBusyError, cdp_open_bind_lock

        impl = default_actuation_port()
        listing = impl.list_tabs(cdp_url=cdp_url)
        if not listing.get("reachable"):
            return _refuse("cdp_unreachable")
        rows = list(listing.get("pages") or [])
        for row in rows:
            if _tab_row_matches_conversation_id(row, conversation_id):
                page = await _poll_playwright_page_for_conversation(
                    ctx, conversation_id
                )
                if page is not None:
                    _clear_attach_misses(conversation_id)
                    return {
                        "ok": True,
                        "page": page,
                        "opened": False,
                        "reused": True,
                        "closed": False,
                        "target_id": _target_id_from_cdp_rows(
                            rows, conversation_id, page
                        ),
                        "policy_just_opened": False,
                    }
                return _refuse_cdp_row_poll_miss(
                    conversation_id,
                    str(row.get("id") or "").strip(),
                )

        target_id = ""
        try:
            cdp_norm = normalize_cdp_store_key(cdp_url)
            async with cdp_open_bind_lock(cdp_norm):
                relist = impl.list_tabs(cdp_url=cdp_url)
                relist_rows = list(relist.get("pages") or [])
                for row in relist_rows:
                    if _tab_row_matches_conversation_id(row, conversation_id):
                        page = await _poll_playwright_page_for_conversation(
                            ctx, conversation_id
                        )
                        if page is not None:
                            _clear_attach_misses(conversation_id)
                            return {
                                "ok": True,
                                "page": page,
                                "opened": False,
                                "reused": True,
                                "closed": False,
                                "target_id": _target_id_from_cdp_rows(
                                    relist_rows, conversation_id, page
                                ),
                                "policy_just_opened": False,
                            }
                        return _refuse_cdp_row_poll_miss(
                            conversation_id,
                            str(row.get("id") or "").strip(),
                        )
                opened = impl.create_background_tab(
                    cdp_url=cdp_url,
                    url=stored,
                    force_new=True,
                    reuse=False,
                )
                if not opened.get("ok"):
                    err = opened.get("error")
                    if isinstance(err, dict) and err.get("code") == "cdp_unreachable":
                        return _refuse("cdp_unreachable")
                    return _refuse(
                        "conversation_missing",
                        conversation_id=conversation_id,
                    )
                target_id = str(opened.get("target_id") or "").strip()
                ws = str(opened.get("web_socket_debugger_url") or "").strip() or None
                session = tab_session_from_row(
                    target_id=target_id,
                    cdp_url=cdp_norm,
                    conversation_url=stored,
                    surface=surface_for_site("chatgpt"),
                    web_socket_debugger_url=ws,
                    created_by_agent=True,
                )
                save_binding(session, path=store_path)
        except TargetLockBusyError:
            return _refuse(
                "conversation_missing",
                conversation_id=conversation_id,
                retryable=True,
            )
        except Exception as exc:
            return _refuse("cdp_unreachable", error=type(exc).__name__)

        page = await _poll_playwright_page_for_conversation(ctx, conversation_id)
        if page is None:
            return _refuse(
                "conversation_missing",
                conversation_id=conversation_id,
                retryable=True,
                target_id=target_id,
            )
        _clear_attach_misses(conversation_id)
        return {
            "ok": True,
            "page": page,
            "opened": True,
            "reused": False,
            "closed": False,
            "target_id": target_id,
            "policy_just_opened": True,
        }


async def _guarded_close_orion_tab(
    *,
    cdp_url: str,
    target_id: str,
    conversation_url: str,
    store_path: Any | None = None,
) -> dict[str, Any]:
    """Close only agent-provenance tabs; focus refuses every close (21815#45 Q1)."""
    from web_tab.cdp_actuation import _close_target, default_actuation_port
    from web_tab.operator_guard import (
        operator_recheck_under_lock,
        wait_operator_idle_before_lock,
    )
    from web_tab.provider_selectors import composer_selector_for_site
    from web_tab.target_lock import TargetLockBusyError, target_write_lock
    from web_tab.write_ops import _created_by_agent_for_target

    impl = default_actuation_port()
    listing = impl.list_tabs(cdp_url=cdp_url)
    if not listing.get("reachable"):
        return {"ok": False, "reason": "cdp_unreachable"}
    row = next(
        (r for r in list(listing.get("pages") or []) if str(r.get("id")) == target_id),
        None,
    )
    if row is None:
        return {"ok": False, "reason": "target_not_found", "target_id": target_id}
    tab_url = str(row.get("url") or "")
    ws = str(row.get("web_socket_debugger_url") or "").strip()
    if not ws:
        return {"ok": False, "reason": "target_not_found", "target_id": target_id}

    created_by_agent = _created_by_agent_for_target(
        cdp_url=cdp_url,
        target_id=target_id,
        conversation_url=conversation_url,
        tab_url=tab_url,
        store_path=store_path,
    )
    if not created_by_agent:
        return {"ok": False, "reason": "operator_provenance_tab", "target_id": target_id}

    selector = composer_selector_for_site("chatgpt")
    pre = await wait_operator_idle_before_lock(ws, selector=selector)
    if not pre.get("ok"):
        return {
            "ok": False,
            "reason": str(pre.get("reason") or "operator_busy"),
            "target_id": target_id,
            "retryable": True,
        }
    try:
        async with target_write_lock(target_id, timeout_s=15):
            guard = await operator_recheck_under_lock(
                ws,
                selector=selector,
                created_by_agent=created_by_agent,
                lock_holder=False,
            )
            if not guard.get("ok"):
                return {
                    "ok": False,
                    "reason": str(guard.get("reason") or "operator_busy"),
                    "target_id": target_id,
                    "retryable": True,
                }
            probe = guard.get("probe") if isinstance(guard.get("probe"), dict) else {}
            has_focus = probe.get("document_has_focus") is True
            visible = str(probe.get("document_visibility") or "") == "visible"
            if has_focus or visible:
                return {
                    "ok": False,
                    "reason": "tab_has_focus",
                    "target_id": target_id,
                    "retryable": True,
                }
            try:
                _close_target(cdp_url, target_id)
            except Exception as exc:
                return {
                    "ok": False,
                    "reason": "cdp_unreachable",
                    "target_id": target_id,
                    "error": type(exc).__name__,
                }
    except TargetLockBusyError:
        return {
            "ok": False,
            "reason": "target_lock_busy",
            "target_id": target_id,
            "retryable": True,
        }

    return {"ok": True, "reason": "closed", "target_id": target_id}


async def sweep_orion_duplicate_tabs(
    ctx: Any,
    *,
    stored_endpoint: str,
    primary_page: OrionPage,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    store_path: Any | None = None,
    primary_target_id: str | None = None,
) -> dict[str, Any]:
    """Close agent-opened duplicate chatgpt.com tabs for one conversation (F12, F13)."""
    stored = _normalize_stored_endpoint(stored_endpoint)
    conversation_id = conversation_id_from_url(stored)
    if not conversation_id:
        return {"ok": True, "closed": False, "closed_target_ids": []}
    primary_tid = str(primary_target_id or "").strip()
    if not primary_tid:
        return {"ok": True, "closed": False, "closed_target_ids": []}
    closed_ids: list[str] = []
    from web_tab.cdp_actuation import default_actuation_port

    impl = default_actuation_port()
    listing = impl.list_tabs(cdp_url=cdp_url)
    rows = list(listing.get("pages") or []) if listing.get("reachable") else []
    for row in rows:
        if not _tab_row_matches_conversation_id(row, conversation_id):
            continue
        target_id = str(row.get("id") or "").strip()
        if not target_id or target_id == primary_tid:
            continue
        outcome = await _guarded_close_orion_tab(
            cdp_url=cdp_url,
            target_id=target_id,
            conversation_url=stored,
            store_path=store_path,
        )
        if outcome.get("ok"):
            closed_ids.append(target_id)
    return {
        "ok": True,
        "closed": bool(closed_ids),
        "closed_target_ids": closed_ids,
    }


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


async def _sync_tab_before_paste(
    page: OrionPage,
    *,
    target_id: str,
    policy_just_opened: bool = False,
    allow_bring_to_front: bool = True,
) -> dict[str, Any]:
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
    if allow_bring_to_front and callable(bring_fn):
        await bring_fn()
        brought_to_front = True

    reload_fn = getattr(page, "reload", None)
    tab_streaming = await _is_streaming(page)
    if (
        not policy_just_opened
        and visibility == "hidden"
        and composer_empty
        and not tab_streaming
        and callable(reload_fn)
    ):
        await reload_fn(wait_until="domcontentloaded")
        await page.wait_for_timeout(300)
        tab_reloaded = True

        page_cid = conversation_id_from_url(page.url or "")
        if page_cid != target_id:
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


def _cdp_transport_refusal(raw: dict[str, Any]) -> dict[str, Any] | None:
    if raw.get("error"):
        return {
            "ok": False,
            "reason": "cdp_transport_error",
            "retryable": True,
        }
    return None


async def _dom_deliver(
    page: OrionPage,
    text: str,
    *,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    preserve_newlines: bool = False,
) -> dict[str, Any]:
    """Paste via ChatGPT DOM helpers (web_tab execute_paste submit sequence).

    On ``dom_insert_and_send`` failure, forwards ``census``, ``pasted``,
    ``delivered``, ``inserted``, ``phase``, and ``send_clicked`` when present.
    """
    from cursor_bridge.dom_composer_paste import dom_insert_and_send
    from web_tab.cdp_actuation import default_actuation_port
    from web_tab.commit_verify import capture_dom_baseline, poll_verify_committed
    from web_tab.operator_guard import (
        operator_recheck_under_lock,
        wait_operator_idle_before_lock,
    )
    from web_tab.provider_selectors import composer_selector_for_site, match_tab_by_url
    from web_tab.target_lock import TargetLockBusyError, target_write_lock

    tab_url = str(getattr(page, "url", "") or "")
    impl = default_actuation_port()
    listing = impl.list_tabs(cdp_url=cdp_url)
    pages = list(listing.get("pages") or [])
    row = match_tab_by_url(pages, tab_url)
    if row is None:
        want_cid = conversation_id_from_url(tab_url)
        if want_cid:
            row = next(
                (r for r in pages if _tab_row_matches_conversation_id(r, want_cid)),
                None,
            )
    if row is None:
        return {"ok": False, "reason": "target_not_found"}
    target_id = str(row.get("id") or "").strip()
    ws = str(row.get("web_socket_debugger_url") or "").strip()
    if not target_id or not ws:
        return {"ok": False, "reason": "target_not_found"}

    site = "chatgpt"
    selector = composer_selector_for_site(site)
    verify_text = text.strip() if preserve_newlines else " ".join(text.splitlines()).strip()
    from web_tab.write_ops import _created_by_agent_for_target

    created_by_agent = _created_by_agent_for_target(
        cdp_url=cdp_url,
        target_id=target_id,
        conversation_url=tab_url,
        tab_url=tab_url,
    )
    pre = await wait_operator_idle_before_lock(ws, selector=selector)
    if not pre.get("ok"):
        return {
            "ok": False,
            "reason": str(pre.get("reason") or pre.get("code") or "operator_busy"),
            "code": str(pre.get("code") or "operator_active"),
            "before_lock": True,
        }
    try:
        async with target_write_lock(target_id, timeout_s=15):
            guard = await operator_recheck_under_lock(
                ws,
                selector=selector,
                created_by_agent=created_by_agent,
                lock_holder=False,
                allow_visible_idle_operator_tab=True,
            )
            if not guard.get("ok"):
                return {
                    "ok": False,
                    "reason": str(
                        guard.get("reason") or guard.get("code") or "operator_busy"
                    ),
                    "code": str(guard.get("code") or "operator_active"),
                    "after_lock": True,
                }
            baseline = await capture_dom_baseline(impl, ws_url=ws, site=site)
            transport = _cdp_transport_refusal(baseline)
            if transport:
                return transport
            base_users = int(baseline.get("user_count") or 0)
            conv_url = tab_url.split("?", 1)[0]
            insertion_writer = f"orion:{target_id}:{uuid.uuid4().hex}"
            result = await dom_insert_and_send(
                ws,
                selector=selector,
                line=text,
                preserve_newlines=preserve_newlines,
                draft_writer=insertion_writer,
                work_native_insert=True,
                expected_conversation_url=conv_url,
            )
    except TargetLockBusyError:
        return {"ok": False, "reason": "target_lock_busy", "retryable": True}

    if not result.get("ok"):
        failure: dict[str, Any] = {
            "ok": False,
            "reason": str(result.get("reason") or "paste_failed"),
        }
        census = result.get("census")
        if census is not None:
            failure["census"] = census
        for key in (
            "pasted",
            "delivered",
            "inserted",
            "phase",
            "send_clicked",
            "insertion_unknown",
            "retry_eligible",
        ):
            if key in result:
                failure[key] = result[key]
        if result.get("pasted") is True and "delivered" not in failure:
            failure["delivered"] = False
        return failure

    receipt: str = "dom_paste"
    verify = await poll_verify_committed(
        impl,
        ws_url=ws,
        site=site,
        prompt_text=verify_text,
        baseline_user_count=base_users,
    )
    transport = _cdp_transport_refusal(verify)
    if transport:
        return transport
    if verify.get("receipt") == "dom_committed":
        receipt = "dom_committed"
    return {
        "ok": True,
        "receipt": receipt,
        "target_id": target_id,
        "selector": selector,
    }


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
    binding_supplied: bool = False,
    stored_endpoint: str | None = None,
    binding_reference: str | None = None,
) -> dict[str, Any]:
    """Attach to pinned chat, paste a pointer wake, optional MCP approval click."""
    from agent_seat.registry import normalize_bus_address
    from web_tab.write_ops import _created_by_agent_for_target

    if normalize_bus_address(from_agent) == "orion":
        return _refuse("orion_echo_drop")

    thread_s = str(thread or "").strip()
    if not thread_s or turn in (None, "", 0, "0"):
        return _refuse("bad_options")

    stored = _normalize_stored_endpoint(
        stored_endpoint or conversation_url or ""
    )
    if binding_supplied:
        if not stored:
            return _refuse("bad_conversation_url", conversation_url=stored)
        invalid = _validate_stored_binding_endpoint(stored)
        if invalid is not None:
            return invalid
        target_url = stored
    elif conversation_url:
        target_url = _normalize_stored_endpoint(conversation_url)
        if not conversation_id_from_url(target_url):
            return _refuse("bad_conversation_url", conversation_url=target_url)
    elif page is not None:
        target_url = _normalize_stored_endpoint(str(getattr(page, "url", "") or ""))
        if not conversation_id_from_url(target_url):
            return _refuse("bad_conversation_url", conversation_url=target_url)
    else:
        return _refuse("bad_conversation_url", conversation_url="")

    target_id = conversation_id_from_url(target_url)
    assert target_id

    tab_policy: dict[str, Any] = {
        "opened": False,
        "reused": False,
        "closed": False,
        "target_id": None,
    }
    policy_just_opened = False
    selected_target_id: str | None = None
    ctx = None
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
        if binding_supplied:
            attach = await _open_orion_binding_tab(
                ctx,
                stored_endpoint=target_url,
                cdp_url=cdp_url,
                binding_reference=binding_reference,
            )
            if not attach.get("ok"):
                if owns_cdp and pw is not None:
                    await pw.stop()
                return attach
            page = attach["page"]
            tab_policy = {
                "opened": bool(attach.get("opened")),
                "reused": bool(attach.get("reused")),
                "closed": bool(attach.get("closed")),
                "target_id": attach.get("target_id"),
            }
            policy_just_opened = bool(attach.get("policy_just_opened"))
            tid = attach.get("target_id")
            if tid:
                selected_target_id = str(tid)
            else:
                from web_tab.cdp_actuation import default_actuation_port

                impl_attach = default_actuation_port()
                attach_list = impl_attach.list_tabs(cdp_url=cdp_url)
                attach_rows = (
                    list(attach_list.get("pages") or [])
                    if attach_list.get("reachable")
                    else []
                )
                selected_target_id = _target_id_from_cdp_rows(
                    attach_rows, target_id, page
                )
            tab_policy["target_id"] = selected_target_id
        else:
            matches = _pages_for_conversation_id(ctx.pages, target_id)
            if not matches:
                if owns_cdp and pw is not None:
                    await pw.stop()
                return _refuse("conversation_missing", conversation_id=target_id)
            page = matches[0]
            tab_policy["reused"] = True
            from web_tab.cdp_actuation import default_actuation_port

            impl_pre = default_actuation_port()
            pre_list = impl_pre.list_tabs(cdp_url=cdp_url)
            pre_rows = (
                list(pre_list.get("pages") or [])
                if pre_list.get("reachable")
                else []
            )
            selected_target_id = _target_id_from_cdp_rows(pre_rows, target_id, page)
            tab_policy["target_id"] = selected_target_id
    else:
        selected_target_id = getattr(page, "target_id", None)
        if selected_target_id is not None:
            selected_target_id = str(selected_target_id).strip() or None
        tab_policy["target_id"] = selected_target_id

    assert page is not None
    page_cid = conversation_id_from_url(page.url or "")
    if page_cid != target_id:
        if owns_cdp and pw is not None:
            stop_fn = getattr(pw, "stop", None)
            if callable(stop_fn):
                await stop_fn()
        return _refuse("conversation_missing", conversation_id=target_id)

    if ctx is not None and binding_supplied:
        if selected_target_id is None:
            tid = getattr(page, "target_id", None)
            selected_target_id = str(tid).strip() if tid else None
        sweep = await sweep_orion_duplicate_tabs(
            ctx,
            stored_endpoint=target_url,
            primary_page=page,
            cdp_url=cdp_url,
            primary_target_id=selected_target_id,
        )
        if sweep.get("closed"):
            tab_policy["closed"] = True

    allow_bring_to_front = False
    if not policy_just_opened and selected_target_id:
        from web_tab.cdp_actuation import default_actuation_port

        impl = default_actuation_port()
        listing = impl.list_tabs(cdp_url=cdp_url)
        rows = list(listing.get("pages") or []) if listing.get("reachable") else []
        row = next(
            (r for r in rows if str(r.get("id") or "") == selected_target_id),
            None,
        )
        if row is not None:
            allow_bring_to_front = _created_by_agent_for_target(
                cdp_url=cdp_url,
                target_id=selected_target_id,
                conversation_url=target_url,
                tab_url=str(row.get("url") or ""),
            )

    scan_raw = await page.evaluate(_DIALOG_SCAN_JS)
    scan = scan_raw if isinstance(scan_raw, dict) else {"dialogs": []}
    blocking = _blocking_dialog(scan)
    if blocking is not None:
        if owns_cdp and pw is not None:
            stop_fn = getattr(pw, "stop", None)
            if callable(stop_fn):
                await stop_fn()
        return _refuse(
            "non_connector_dialog",
            pasted=False,
            dialog=blocking,
        )

    tab_sync = await _sync_tab_before_paste(
        page,
        target_id=target_id,
        policy_just_opened=policy_just_opened,
        allow_bring_to_front=allow_bring_to_front,
    )
    if tab_sync.get("refuse") is not None:
        if owns_cdp and pw is not None:
            stop_fn = getattr(pw, "stop", None)
            if callable(stop_fn):
                await stop_fn()
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
        dom = await _dom_deliver(page, line, cdp_url=cdp_url, preserve_newlines=False)
        if not dom.get("ok"):
            if owns_cdp and pw is not None:
                await pw.stop()
            dom_reason = str(dom.get("reason") or "paste_failed")
            visibility_only = (
                dom_reason
                == "tab not created by agent has focus or is visible to the operator"
            )
            if dom_reason == "target_lock_busy" or dom.get("before_lock"):
                return _refuse(
                    "deferred_streaming",
                    error=dom_reason,
                    pasted=False,
                    brought_to_front=tab_sync["brought_to_front"],
                    tab_reloaded=tab_sync["tab_reloaded"],
                )
            if dom.get("after_lock") and not visibility_only:
                return _refuse(
                    "deferred_streaming",
                    error=dom_reason,
                    after_lock=True,
                    pasted=False,
                    brought_to_front=tab_sync["brought_to_front"],
                    tab_reloaded=tab_sync["tab_reloaded"],
                )
            if _dom_inserted_but_unsent(dom):
                inserted_unsent: dict[str, Any] = {
                    "ok": False,
                    "reason": dom_reason,
                    "pasted": True,
                    "delivered": False,
                    "error": dom_reason,
                    "brought_to_front": tab_sync["brought_to_front"],
                    "tab_reloaded": tab_sync["tab_reloaded"],
                }
                if dom.get("census") is not None:
                    inserted_unsent["census"] = dom["census"]
                for key in ("inserted", "phase", "send_clicked"):
                    if key in dom:
                        inserted_unsent[key] = dom[key]
                return inserted_unsent
            paste_refuse: dict[str, Any] = {
                "error": dom_reason,
                "pasted": False,
                "brought_to_front": tab_sync["brought_to_front"],
                "tab_reloaded": tab_sync["tab_reloaded"],
            }
            if dom.get("insertion_unknown") is True:
                paste_refuse["insertion_unknown"] = True
                paste_refuse["retry_eligible"] = bool(dom.get("retry_eligible", False))
            if dom.get("census") is not None:
                paste_refuse["census"] = dom["census"]
            return _refuse("paste_failed", **paste_refuse)
        await page.wait_for_timeout(400)
        approval = await _handle_approvals(page)
    except Exception as exc:
        if owns_cdp and pw is not None:
            stop_fn = getattr(pw, "stop", None)
            if callable(stop_fn):
                await stop_fn()
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
    dom_receipt = str(dom.get("receipt") or "dom_paste")
    if approval.get("outcome") == "approval_unrecognized":
        return {
            "ok": False,
            "reason": "approval_unrecognized",
            "pasted": True,
            "delivered": False,
            "approval": approval,
            "pointer_len": len(line),
            "receipt": dom_receipt,
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
        "receipt": dom_receipt,
        "conversation_id": target_id,
        **tab_policy,
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
        endpoint = _normalize_stored_endpoint(conversation_url or "")
        if not endpoint or not conversation_id_from_url(endpoint):
            return _refuse("bad_conversation_url", conversation_url=endpoint)
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
    if claim.binding_reference:
        gate = _refuse_unless_binding_row_matches_claim(
            claim.binding_reference, claim.normalized_endpoint()
        )
        if gate is not None:
            return enrich_delivery_receipt(gate, claim)
        open_endpoint = _stored_endpoint_from_delivery_binding(
            claim.binding_reference
        )
    else:
        open_endpoint = claim.normalized_endpoint()
    receipt = await deliver_orion_wake(
        thread=thread,
        turn=pointer_turn or turn,
        from_agent=from_agent,
        conversation_url=claim.normalized_endpoint(),
        cdp_url=cdp_url,
        page=page,
        connect_cdp=connect_cdp,
        paste_text=paste_text,
        binding_supplied=True,
        stored_endpoint=open_endpoint,
        binding_reference=claim.binding_reference,
    )
    # Why: live deliver_orion_wake steers during streaming; deferred_streaming receipts
    # remain for legacy in-flight ledger retry rows and injected pipeline responses.
    if receipt.get("reason") == "deferred_streaming":
        receipt = {**receipt, "ok": False, "retry_eligible": True}
    owner.record_paste_receipt(claim, receipt)
    return enrich_delivery_receipt(receipt, claim)
