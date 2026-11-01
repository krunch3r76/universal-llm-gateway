"""Grant-gated operator relay paste into Grok Bot / display-chat sidebars."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.error import URLError

from cursor_bridge.cdp_composer_input import (
    cdp_dispatch_enter,
    cdp_insert_text,
    clear_our_composer_draft,
)
from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.chat_surface import (
    probe_surface,
    select_sidebar_target,
    wake_decision,
)
from cursor_bridge.display_interaction_locks import (
    agent_paste_lock,
    display_switch_lock,
)
from cursor_bridge.dom_composer_paste import (
    composer_selector,
    dom_click_send,
    dom_set_composer_text,
    foreign_draft_guard,
    read_operator_busy,
    wait_for_operator_idle,
)
from cursor_bridge.operator_agent_locate import (
    locate_off_sidebar,
    visible_sidebar_agent_ids,
)
from cursor_bridge.wake_delivery import (
    _busy_or_draft_reason,
    _pick_page,
    _refuse,
    normalize_target,
)

OPERATOR_HEADER = "Operator request — Kaywan, relayed on his behalf"
WATERMARK = "Pasted on Kaywan's behalf by gc1 — AI-assisted"
RELAY_NONCE_LABEL = "Relay-Nonce"
_VALID_GRANTS = frozenset({"operator", "explicit"})


def make_run_nonce() -> str:
    """Unique per paste run; embedded in the outbound body for sent-transcript readback."""
    return uuid.uuid4().hex[:12]


def watermark_with_nonce(run_nonce: str) -> str:
    nonce = str(run_nonce or "").strip()
    if not nonce:
        return WATERMARK
    return f"{WATERMARK} · {RELAY_NONCE_LABEL}: {nonce}"


def extract_run_nonce_from_line(line: str) -> str:
    for part in str(line or "").splitlines():
        if RELAY_NONCE_LABEL not in part:
            continue
        _, _, tail = part.partition(f"{RELAY_NONCE_LABEL}:")
        return tail.strip()
    return ""


def wrap_operator_message(
    *,
    text: str,
    authorization_source: str,
    scope: str,
    run_nonce: str | None = None,
) -> str:
    """Format one operator relay body with header, source, scope, watermark, and run nonce."""
    body = (text or "").strip()
    src = (authorization_source or "").strip()
    scope_s = (scope or "").strip()
    nonce = str(run_nonce or "").strip() or make_run_nonce()
    wm = watermark_with_nonce(nonce)
    return (
        f"{OPERATOR_HEADER}\n\nSource: {src}\nScope: {scope_s}\n\n{body}\n\n{wm}"
    )


def _grant_refuse(reason: str, **extra: Any) -> dict[str, Any]:
    return {**_refuse(reason, **extra), "readback_ok": False}


def _screenshot_dir() -> Path:
    root = Path(os.environ.get("CORTEX_FILES_ROOT", "/tmp")).expanduser()
    dest = root / "operator-paste-screenshots"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


_DOM_FINGERPRINT_KEYS = (
    "state",
    "active_agent_id",
    "active_title",
    "composer_draft",
    "generating",
    "recent_messages",
)


def _sha256_dom_from_surface(surface: dict[str, Any]) -> str:
    """Fingerprint probe-visible chat surface (Electron DOM read), not full page HTML."""
    payload = {k: surface.get(k) for k in _DOM_FINGERPRINT_KEYS}
    dom_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(dom_bytes).hexdigest()


def _dom_artifacts_from_surface(surface: dict[str, Any]) -> dict[str, Any]:
    return {
        "sha256_dom": _sha256_dom_from_surface(surface),
        "sha256_png": None,
        "screenshot_path": None,
    }


async def _capture_screenshot_artifact(
    ws_url: str, *, agent_id: str, phase: str
) -> dict[str, str | None]:
    import websockets

    async with websockets.connect(ws_url, max_size=8_000_000) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Page.captureScreenshot",
                    "params": {"format": "png"},
                }
            )
        )
        raw_png = await asyncio.wait_for(_cdp_response(ws, 1), 8.0)
    png_b64_str = (raw_png.get("result") or {}).get("data") or ""
    png_b64 = png_b64_str.encode("ascii")
    png_path: str | None = None
    sha256_png: str | None = None
    if png_b64_str:
        fname = f"{agent_id[:8]}-{phase}-{uuid.uuid4().hex[:12]}.png"
        out = _screenshot_dir() / fname
        out.write_bytes(base64.standard_b64decode(png_b64_str))
        png_path = str(out)
        sha256_png = hashlib.sha256(png_b64).hexdigest()
    return {"sha256_png": sha256_png, "screenshot_path": png_path}


async def _finalize_verification_artifacts(
    ws_url: str,
    *,
    surface: dict[str, Any],
    agent_id: str,
    phase: str,
    dom_conclusive: bool,
) -> dict[str, Any]:
    """DOM probe fingerprint always; PNG only when DOM verification is inconclusive."""
    artifacts = _dom_artifacts_from_surface(surface)
    if dom_conclusive:
        return {**artifacts, "verify_method": "dom"}
    png = await _capture_screenshot_artifact(ws_url, agent_id=agent_id, phase=phase)
    return {
        **artifacts,
        **png,
        "verify_method": "screenshot_fallback",
    }


def _transcript_ambiguous(
    post: dict[str, Any],
    *,
    watermark: str,
    body_snippet: str,
    run_nonce: str = "",
) -> bool:
    """True when transcript/composer state does not decisively show submit/readback."""
    composer_draft = str(post.get("composer_draft") or "").strip()
    messages = _transcript_message_texts(post)
    nonce = str(run_nonce or "").strip()
    if nonce:
        watermark_visible = _nonce_in_sent_transcript(post, run_nonce=nonce)
    else:
        watermark_visible = any(watermark in m for m in messages)
    snippet = body_snippet.strip()
    snippet_visible = bool(snippet) and any(snippet in m for m in messages)
    if composer_draft and not watermark_visible and not snippet_visible:
        return True
    if not watermark_visible and not snippet_visible and not composer_draft:
        return True
    return False


def _dom_verification_conclusive(
    post: dict[str, Any],
    *,
    expected_agent_id: str,
    watermark: str,
    body_snippet: str,
    readback_ok: bool,
    run_nonce: str = "",
) -> bool:
    active = str(post.get("active_agent_id") or "").strip()
    if not active or active != expected_agent_id:
        return False
    if _transcript_ambiguous(
        post, watermark=watermark, body_snippet=body_snippet, run_nonce=run_nonce
    ):
        return False
    return readback_ok


async def _insert_composer_text(
    ws_url: str, *, selector: str, line: str
) -> dict[str, Any]:
    """Set composer text once via DOM — no submit, no focus()."""
    return await dom_set_composer_text(ws_url, selector=selector, text=line)


async def _submit_composer_send_button(
    ws_url: str, *, selector: str, line: str
) -> dict[str, Any]:
    send = await dom_click_send(ws_url, selector=selector, line=line)
    clicked = bool(send.get("send_clicked"))
    return {
        "ok": clicked,
        "method": "send_button",
        "send_fallback_clicked": clicked,
    }


async def _submit_composer_enter(ws_url: str) -> dict[str, Any]:
    return await cdp_dispatch_enter(ws_url)


def _surface_agent_id(surface: dict[str, Any]) -> str:
    return str(surface.get("active_agent_id") or "").strip()


async def _probe_agent_match(
    ws_url: str, *, expected_agent_id: str
) -> tuple[bool, dict[str, Any]]:
    surface = await probe_surface(ws_url)
    return _surface_agent_id(surface) == expected_agent_id, surface


def _target_changed_refuse(
    *,
    disp: str,
    app: str,
    aid_s: str,
    selected: bool,
    locate_method: str | None,
    surface: dict[str, Any],
    line: str,
    verdict: dict[str, Any],
) -> dict[str, Any]:
    return {
        **verdict,
        "ok": False,
        "reason": "target_changed",
        "pasted": False,
        "delivered": False,
        "readback_ok": False,
        "submitted": False,
        "target_verified": False,
        "selected": selected,
        "display": disp,
        "app": app,
        "agent_id": aid_s,
        "locate_method": locate_method,
        "message": line,
        "active_agent_id": surface.get("active_agent_id"),
        "expected_agent_id": aid_s,
    }


async def _insert_composer_with_fallback(
    ws_url: str, *, selector: str, line: str
) -> dict[str, Any]:
    dom = await _insert_composer_text(ws_url, selector=selector, line=line)
    if dom.get("ok"):
        return {**dom, "insert_method": "dom_set"}
    cdp = await cdp_insert_text(ws_url, selector=selector, text=line)
    if cdp.get("ok"):
        return {**cdp, "insert_method": "cdp_insert"}
    return dom if dom.get("reason") else cdp


async def _guarded_insert_and_submit(
    ws_url: str,
    *,
    disp: str,
    app: str,
    aid_s: str,
    prompt_sel: str,
    flat_line: str,
    body_snippet: str,
    line: str,
    verdict: dict[str, Any],
    selected: bool,
    locate_method: str | None,
    surface: dict[str, Any],
    run_nonce: str,
) -> tuple[dict[str, Any] | None, bool, dict[str, Any], list[str]]:
    """Insert + submit under display_switch_lock with agent re-verify."""
    submit_methods: list[str] = []
    post_submit = surface
    wm_line = watermark_with_nonce(run_nonce)

    async with display_switch_lock(disp):
        matched, surface = await _probe_agent_match(ws_url, expected_agent_id=aid_s)
        if not matched:
            await clear_our_composer_draft(
                ws_url, selector=prompt_sel, watermark=WATERMARK, line=flat_line
            )
            return (
                _target_changed_refuse(
                    disp=disp,
                    app=app,
                    aid_s=aid_s,
                    selected=selected,
                    locate_method=locate_method,
                    surface=surface,
                    line=line,
                    verdict=verdict,
                ),
                False,
                surface,
                submit_methods,
            )

        busy = await read_operator_busy(
            ws_url, selector=prompt_sel, watermark=WATERMARK
        )
        if bool(busy.get("operator_active")):
            return (
                _grant_refuse(
                    "operator_busy",
                    display=disp,
                    app=app,
                    submitted=False,
                    selected=selected,
                    expected_agent_id=aid_s,
                    pasted=False,
                ),
                False,
                surface,
                submit_methods,
            )

        try:
            insert_res = await _insert_composer_with_fallback(
                ws_url, selector=prompt_sel, line=flat_line
            )
        except (TimeoutError, RuntimeError, OSError) as exc:
            return (
                _grant_refuse(
                    "paste_failed",
                    display=disp,
                    app=app,
                    selected=selected,
                    detail=type(exc).__name__,
                ),
                False,
                surface,
                submit_methods,
            )
        if not insert_res.get("ok"):
            return (
                _grant_refuse(
                    str(insert_res.get("reason") or "no_composer"),
                    display=disp,
                    app=app,
                    selected=selected,
                    expected_agent_id=aid_s,
                    active_agent_id=verdict.get("active_agent_id"),
                ),
                False,
                surface,
                submit_methods,
            )

        matched, surface = await _probe_agent_match(ws_url, expected_agent_id=aid_s)
        if not matched:
            await clear_our_composer_draft(
                ws_url, selector=prompt_sel, watermark=WATERMARK, line=flat_line
            )
            return (
                _target_changed_refuse(
                    disp=disp,
                    app=app,
                    aid_s=aid_s,
                    selected=selected,
                    locate_method=locate_method,
                    surface=surface,
                    line=line,
                    verdict=verdict,
                ),
                False,
                surface,
                submit_methods,
            )

        try:
            first_send = await _submit_composer_send_button(
                ws_url, selector=prompt_sel, line=flat_line
            )
            submit_methods.append("send_button")
        except (TimeoutError, RuntimeError, OSError) as exc:
            return (
                _grant_refuse(
                    "submit_failed",
                    display=disp,
                    app=app,
                    selected=selected,
                    detail=type(exc).__name__,
                ),
                False,
                surface,
                submit_methods,
            )

        submitted = False
        if first_send.get("ok"):
            post_submit, submitted = await _poll_submit_verified(
                ws_url,
                watermark=wm_line,
                body_snippet=body_snippet,
                run_nonce=run_nonce,
            )
        else:
            post_submit = surface

        if not submitted:
            retry_surface = await probe_surface(ws_url)
            if _submit_verified(
                retry_surface,
                watermark=wm_line,
                body_snippet=body_snippet,
                run_nonce=run_nonce,
            ):
                post_submit = retry_surface
                submitted = True
            else:
                matched, surface = await _probe_agent_match(
                    ws_url, expected_agent_id=aid_s
                )
                if not matched:
                    await clear_our_composer_draft(
                        ws_url,
                        selector=prompt_sel,
                        watermark=WATERMARK,
                        line=flat_line,
                    )
                    return (
                        _target_changed_refuse(
                            disp=disp,
                            app=app,
                            aid_s=aid_s,
                            selected=selected,
                            locate_method=locate_method,
                            surface=surface,
                            line=line,
                            verdict=verdict,
                        ),
                        False,
                        surface,
                        submit_methods,
                    )
                try:
                    enter = await _submit_composer_enter(ws_url)
                    submit_methods.append("cdp_enter")
                    if enter.get("ok"):
                        post_submit, submitted = await _poll_submit_verified(
                            ws_url,
                            watermark=wm_line,
                            body_snippet=body_snippet,
                            run_nonce=run_nonce,
                        )
                except (TimeoutError, RuntimeError, OSError):
                    submitted = False

        matched, verify_surface = await _probe_agent_match(
            ws_url, expected_agent_id=aid_s
        )
        if not matched:
            await clear_our_composer_draft(
                ws_url, selector=prompt_sel, watermark=WATERMARK, line=flat_line
            )
            return (
                _target_changed_refuse(
                    disp=disp,
                    app=app,
                    aid_s=aid_s,
                    selected=selected,
                    locate_method=locate_method,
                    surface=verify_surface,
                    line=line,
                    verdict=verdict,
                ),
                False,
                verify_surface,
                submit_methods,
            )

    return (None, submitted, post_submit, submit_methods)


async def _cdp_response(ws: Any, mid: int) -> dict[str, Any]:
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg


_READBACK_POLL_TIMEOUT_S = 60.0
_READBACK_POLL_INTERVAL_S = 0.4
_SUBMIT_VERIFY_TIMEOUT_S = 8.0
_SUBMIT_VERIFY_INTERVAL_S = 0.25


def _message_turns(post: dict[str, Any]) -> list[dict[str, str]]:
    raw = post.get("message_turns") or []
    turns: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("text") or "").strip()
        if role in {"user", "assistant"} and text:
            turns.append({"role": role, "text": text})
    return turns


def _transcript_message_texts(post: dict[str, Any]) -> list[str]:
    turns = _message_turns(post)
    if turns:
        return [t["text"] for t in turns]
    return [str(m) for m in (post.get("recent_messages") or [])]


def _nonce_in_sent_transcript(post: dict[str, Any], *, run_nonce: str) -> bool:
    """True only when this run's nonce appears in probed transcript (composer excluded in probe)."""
    nonce = str(run_nonce or "").strip()
    if not nonce:
        return False
    needle = f"{RELAY_NONCE_LABEL}: {nonce}"
    return any(needle in msg for msg in _transcript_message_texts(post))


def _assistant_began(
    post: dict[str, Any],
    *,
    watermark: str,
    pre_messages: list[str],
    body_snippet: str = "",
    run_nonce: str = "",
) -> bool:
    """True when streaming is active or assistant prose appears after our user turn."""
    if bool(post.get("generating")):
        return True
    snippet = body_snippet.strip()
    nonce = str(run_nonce or "").strip()
    turns = _message_turns(post)
    if turns:
        user_idx = -1
        for i, turn in enumerate(turns):
            if turn["role"] != "user":
                continue
            text = turn["text"]
            if nonce and f"{RELAY_NONCE_LABEL}: {nonce}" in text:
                user_idx = i
            elif not nonce and (watermark in text or (snippet and snippet in text)):
                user_idx = i
        if user_idx >= 0:
            for turn in turns[user_idx + 1 :]:
                if turn["role"] == "assistant" and turn["text"].strip():
                    return True
        return False
    messages = _transcript_message_texts(post)
    wm_idx = -1
    for i, msg in enumerate(messages):
        if nonce and f"{RELAY_NONCE_LABEL}: {nonce}" in msg:
            wm_idx = i
        elif not nonce and (watermark in msg or (snippet and snippet in msg)):
            wm_idx = i
    if wm_idx >= 0 and wm_idx < len(messages) - 1:
        return True
    if messages != pre_messages and wm_idx >= 0:
        for msg in messages[wm_idx + 1 :]:
            if msg.strip() and watermark not in msg:
                return True
    return False


def _readback_fields(
    post: dict[str, Any],
    *,
    watermark: str,
    pre_messages: list[str],
    body_snippet: str = "",
    run_nonce: str = "",
) -> dict[str, bool]:
    composer_empty = not str(post.get("composer_draft") or "").strip()
    nonce = str(run_nonce or "").strip()
    if nonce:
        watermark_visible = _nonce_in_sent_transcript(post, run_nonce=nonce)
    else:
        watermark_visible = _message_in_transcript(
            post, watermark=watermark, body_snippet=body_snippet
        )
    assistant_began = _assistant_began(
        post,
        watermark=watermark,
        pre_messages=pre_messages,
        body_snippet=body_snippet,
        run_nonce=nonce,
    )
    readback_ok = composer_empty and watermark_visible and assistant_began
    return {
        "composer_empty_after": composer_empty,
        "watermark_visible": watermark_visible,
        "assistant_began": assistant_began,
        "readback_ok": readback_ok,
    }


def _readback_ok(
    post: dict[str, Any], *, watermark: str, pre_messages: list[str]
) -> bool:
    return _readback_fields(post, watermark=watermark, pre_messages=pre_messages)[
        "readback_ok"
    ]


def _target_verified(surface: dict[str, Any], *, expected_agent_id: str) -> bool:
    active = str(surface.get("active_agent_id") or "").strip()
    return bool(active) and active == expected_agent_id


def _message_in_transcript(
    post: dict[str, Any], *, watermark: str, body_snippet: str, run_nonce: str = ""
) -> bool:
    nonce = str(run_nonce or "").strip()
    if nonce:
        return _nonce_in_sent_transcript(post, run_nonce=nonce)
    snippet = body_snippet.strip()
    messages = _transcript_message_texts(post)
    return any(watermark in m or (snippet and snippet in m) for m in messages)


def _submit_verified(
    post: dict[str, Any], *, watermark: str, body_snippet: str, run_nonce: str = ""
) -> bool:
    composer_empty = not str(post.get("composer_draft") or "").strip()
    return composer_empty and _message_in_transcript(
        post, watermark=watermark, body_snippet=body_snippet, run_nonce=run_nonce
    )


async def _poll_submit_verified(
    ws_url: str,
    *,
    watermark: str,
    body_snippet: str,
    run_nonce: str = "",
    timeout_s: float = _SUBMIT_VERIFY_TIMEOUT_S,
    interval_s: float = _SUBMIT_VERIFY_INTERVAL_S,
) -> tuple[dict[str, Any], bool]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    post: dict[str, Any] = {}
    verified = False
    while loop.time() <= deadline:
        post = await probe_surface(ws_url)
        verified = _submit_verified(
            post,
            watermark=watermark,
            body_snippet=body_snippet,
            run_nonce=run_nonce,
        )
        if verified:
            return post, True
        await asyncio.sleep(interval_s)
    return post, verified


async def _poll_readback(
    ws_url: str,
    *,
    watermark: str,
    pre_messages: list[str],
    body_snippet: str = "",
    run_nonce: str = "",
    timeout_s: float = _READBACK_POLL_TIMEOUT_S,
    interval_s: float = _READBACK_POLL_INTERVAL_S,
) -> tuple[dict[str, Any], dict[str, bool]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    post: dict[str, Any] = {}
    fields = _readback_fields(
        post,
        watermark=watermark,
        pre_messages=pre_messages,
        body_snippet=body_snippet,
        run_nonce=run_nonce,
    )
    while loop.time() <= deadline:
        post = await probe_surface(ws_url)
        fields = _readback_fields(
            post,
            watermark=watermark,
            pre_messages=pre_messages,
            body_snippet=body_snippet,
            run_nonce=run_nonce,
        )
        if fields["readback_ok"]:
            return post, fields
        await asyncio.sleep(interval_s)
    return post, fields


async def deliver_operator_paste(
    *,
    display: object,
    app: object,
    agent_id: object,
    text: str,
    grant: object,
    authorization_source: object,
    scope: object = "",
    select: bool = True,
) -> dict[str, Any]:
    """Paste one grant-gated operator message; refuse without grant and source."""
    grant_s = str(grant or "").strip().lower()
    auth_src = str(authorization_source or "").strip()
    if grant_s not in _VALID_GRANTS or not auth_src:
        return _grant_refuse(
            "grant_or_source_required",
            grant=grant_s or None,
            authorization_source=auth_src or None,
        )
    aid_s = str(agent_id or "").strip()
    if not aid_s:
        return _grant_refuse("agent_id_required")
    target = normalize_target(display, app)
    if not target.get("ok"):
        return _grant_refuse(
            "unknown_target", display=target.get("display"), app=target.get("app")
        )
    if target["app"] == "cursor":
        return _grant_refuse(
            "cursor_ide_seat_forbidden", display=target["display"], app=target["app"]
        )
    line = wrap_operator_message(
        text=text,
        authorization_source=auth_src,
        scope=str(scope or "").strip(),
    )
    async with agent_paste_lock(target["display"], aid_s):
        return await _deliver_operator_locked(
            target=target,
            aid_s=aid_s,
            select=bool(select),
            line=line,
        )


async def _deliver_operator_locked(
    *,
    target: dict[str, Any],
    aid_s: str,
    select: bool,
    line: str,
) -> dict[str, Any]:
    disp = str(target["display"])
    async with display_switch_lock(disp):
        return await _deliver_operator_session(
            target=target, aid_s=aid_s, select=select, line=line
        )


async def _deliver_operator_session(
    *,
    target: dict[str, Any],
    aid_s: str,
    select: bool,
    line: str,
) -> dict[str, Any]:
    disp, app = target["display"], target["app"]
    try:
        pages = list_pages(target["origin"])
    except URLError as exc:
        return _grant_refuse(
            "debug_unreachable", display=disp, app=app, detail=type(exc).__name__
        )
    page = _pick_page(pages, app)
    if page is None:
        return _grant_refuse("no_page", display=disp, app=app, pages=len(pages))
    ws_url = page["web_socket_debugger_url"]
    from cursor_bridge.dom_composer_paste import install_operator_activity_probe

    await install_operator_activity_probe(ws_url)
    try:
        surface = await probe_surface(ws_url)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _grant_refuse(
            "surface_probe_failed", display=disp, app=app, detail=type(exc).__name__
        )
    pre_messages = [str(m) for m in (surface.get("recent_messages") or [])]
    restore_agent_id = str(surface.get("active_agent_id") or "").strip()
    did_switch = False
    locate_method: str | None = None
    try:
        visible_ids = await visible_sidebar_agent_ids(ws_url)
    except (TimeoutError, RuntimeError, OSError) as exc:
        return _grant_refuse(
            "surface_probe_failed",
            display=disp,
            app=app,
            detail=type(exc).__name__,
        )
    if aid_s in visible_ids:
        locate_method = "sidebar"
    try:
        verdict = wake_decision(surface, agent_id=aid_s)
    except ValueError:
        return _grant_refuse("bad_options", display=disp, app=app)

    if verdict.get("reason") == "ambiguous_title":
        return _grant_refuse(
            "ambiguous_title",
            display=disp,
            app=app,
            expected_title=verdict.get("expected_title"),
            titles=verdict.get("titles"),
        )

    selected = False
    if (
        not verdict.get("ok")
        and select
        and verdict.get("state") == "chat_list"
        and aid_s in visible_ids
    ):
        guard = _busy_or_draft_reason(surface)
        if guard:
            return _grant_refuse(
                guard,
                display=disp,
                app=app,
                active_title=verdict.get("active_title"),
                expected_agent_id=verdict.get("expected_agent_id"),
            )
        idle = await wait_for_operator_idle(
            ws_url,
            selector=composer_selector(app=app, agent_id=aid_s),
            watermark=WATERMARK,
            refuse_if_active=True,
        )
        if not idle.get("idle"):
            return _grant_refuse(
                "operator_busy",
                display=disp,
                app=app,
                submitted=False,
                selected=selected,
            )
        async with display_switch_lock(disp):
            click_res = await select_sidebar_target(
                ws_url,
                expected_title=str(verdict.get("expected_title") or ""),
                expected_agent_id=str(verdict.get("expected_agent_id") or aid_s),
            )
        if click_res.get("clicked"):
            selected = True
            if restore_agent_id and restore_agent_id != aid_s:
                did_switch = True
            await asyncio.sleep(0.25)
            surface = await probe_surface(ws_url)
            verdict = wake_decision(surface, agent_id=aid_s)

    if not verdict.get("ok") and select and aid_s not in visible_ids:
        idle = await wait_for_operator_idle(
            ws_url,
            selector=composer_selector(app=app, agent_id=aid_s),
            watermark=WATERMARK,
            refuse_if_active=True,
        )
        if not idle.get("idle"):
            return _grant_refuse(
                "operator_busy",
                display=disp,
                app=app,
                submitted=False,
                selected=selected,
            )
        async with display_switch_lock(disp):
            located = await locate_off_sidebar(ws_url, aid_s)
        if located.get("ok"):
            locate_method = str(located.get("locate_method") or "")
            if restore_agent_id and restore_agent_id != aid_s:
                did_switch = True
            await asyncio.sleep(0.2)
            surface = await probe_surface(ws_url)
            verdict = wake_decision(surface, agent_id=aid_s)
        else:
            return _grant_refuse(
                "agent_not_found",
                display=disp,
                app=app,
                agent_id=aid_s,
                selected=selected,
            )

    if not verdict.get("ok"):
        reason = str(verdict.get("reason") or "refused")
        if reason == "agent_id_mismatch":
            return _grant_refuse(
                "agent_id_mismatch",
                display=disp,
                app=app,
                selected=selected,
                active_agent_id=verdict.get("active_agent_id"),
                expected_agent_id=aid_s,
                locate_method=locate_method,
            )
        return {
            **verdict,
            "pasted": False,
            "delivered": False,
            "readback_ok": False,
            "selected": selected,
            "display": disp,
            "app": app,
            "locate_method": locate_method,
        }

    active_aid = str(verdict.get("active_agent_id") or "").strip()
    if active_aid and active_aid != aid_s:
        return _grant_refuse(
            "agent_id_mismatch",
            display=disp,
            app=app,
            selected=selected,
            active_agent_id=active_aid,
            expected_agent_id=aid_s,
            locate_method=locate_method,
        )
    if locate_method is None and aid_s in visible_ids:
        locate_method = "sidebar"

    if not _target_verified(surface, expected_agent_id=aid_s):
        return _grant_refuse(
            "target_unverified",
            display=disp,
            app=app,
            selected=selected,
            active_agent_id=surface.get("active_agent_id"),
            expected_agent_id=aid_s,
            locate_method=locate_method,
            target_verified=False,
        )

    guard = foreign_draft_guard(surface, watermark=WATERMARK) or _busy_or_draft_reason(
        surface
    )
    if guard:
        return _grant_refuse(
            guard,
            display=disp,
            app=app,
            selected=selected,
            active_title=verdict.get("active_title"),
            expected_agent_id=verdict.get("expected_agent_id"),
        )
    prompt_sel = composer_selector(app=app, agent_id=aid_s)
    flat_line = " ".join(line.splitlines()).strip()
    body_snippet = flat_line[:120]
    run_nonce = extract_run_nonce_from_line(line)
    wm_line = watermark_with_nonce(run_nonce)

    idle = await wait_for_operator_idle(
        ws_url,
        selector=prompt_sel,
        watermark=WATERMARK,
        refuse_if_active=True,
    )
    if not idle.get("idle"):
        return _grant_refuse(
            "operator_busy",
            display=disp,
            app=app,
            submitted=False,
            selected=selected,
            expected_agent_id=aid_s,
        )

    early, submitted, post_submit, submit_methods = await _guarded_insert_and_submit(
        ws_url,
        disp=disp,
        app=app,
        aid_s=aid_s,
        prompt_sel=prompt_sel,
        flat_line=flat_line,
        body_snippet=body_snippet,
        line=line,
        verdict=verdict,
        selected=selected,
        locate_method=locate_method,
        surface=surface,
        run_nonce=run_nonce,
    )
    if early is not None:
        return early

    if not submitted:
        artifacts = await _finalize_verification_artifacts(
            ws_url,
            surface=post_submit,
            agent_id=aid_s,
            phase="submit_failed",
            dom_conclusive=False,
        )
        return {
            **verdict,
            "ok": False,
            "reason": "submit_unverified",
            "pasted": True,
            "delivered": False,
            "readback_ok": False,
            "submitted": False,
            "target_verified": True,
            "composer_empty_after": not str(
                post_submit.get("composer_draft") or ""
            ).strip(),
            "assistant_began": False,
            "watermark_visible": _message_in_transcript(
                post_submit,
                watermark=wm_line,
                body_snippet=body_snippet,
                run_nonce=run_nonce,
            ),
            "run_nonce": run_nonce,
            "selected": selected,
            "display": disp,
            "app": app,
            "agent_id": aid_s,
            "locate_method": locate_method,
            "message": line,
            "submit_methods": submit_methods,
            **artifacts,
        }

    post, rb = await _poll_readback(
        ws_url,
        watermark=wm_line,
        pre_messages=pre_messages,
        body_snippet=body_snippet,
        run_nonce=run_nonce,
    )
    if did_switch and restore_agent_id:
        await select_sidebar_target(ws_url, expected_agent_id=restore_agent_id)
    readback_ok = rb["readback_ok"]
    delivered = readback_ok
    dom_conclusive = _dom_verification_conclusive(
        post,
        expected_agent_id=aid_s,
        watermark=wm_line,
        body_snippet=body_snippet,
        readback_ok=readback_ok,
        run_nonce=run_nonce,
    )
    artifacts = await _finalize_verification_artifacts(
        ws_url,
        surface=post,
        agent_id=aid_s,
        phase="delivered" if delivered else "readback_inconclusive",
        dom_conclusive=dom_conclusive,
    )
    return {
        **verdict,
        "ok": delivered,
        "reason": "delivered" if delivered else "readback_failed",
        "pasted": True,
        "delivered": delivered,
        "readback_ok": readback_ok,
        "submitted": True,
        "target_verified": True,
        "composer_empty_after": rb["composer_empty_after"],
        "assistant_began": rb["assistant_began"],
        "watermark_visible": rb["watermark_visible"],
        "selected": selected,
        "display": disp,
        "app": app,
        "agent_id": aid_s,
        "locate_method": locate_method,
        "message": line,
        "submit_methods": submit_methods,
        "restore_agent_id": restore_agent_id if did_switch else None,
        "run_nonce": run_nonce,
        **artifacts,
    }


__all__ = [
    "WATERMARK",
    "RELAY_NONCE_LABEL",
    "OPERATOR_HEADER",
    "deliver_operator_paste",
    "make_run_nonce",
    "wrap_operator_message",
    "extract_run_nonce_from_line",
    "watermark_with_nonce",
]
