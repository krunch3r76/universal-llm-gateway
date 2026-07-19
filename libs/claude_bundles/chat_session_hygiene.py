"""claude.ai chat hygiene — delete finished sessions after sealed asks.

Binding (friction 5195 / CDP ask pivot): every disposable subagent ask MUST
end with delete of that chat before the next ask. Skipping delete lets prior
context leak into the next compose.

Verified path (2026-07-16):
  header More options → delete-chat-trigger → confirm Delete (JS clicks).
Post-delete often lands on /new; re-navigate before next send if needed.

Resilience (a:25084): bounded header poll, session→chat triggers, sidebar
Recents fallback keyed by title in aria-label; cleanup_ok on result dict.
"""

from __future__ import annotations

import json
import os

from claude_bundles.project_chrome import project_url

HEADER_POLL_MS = 4000
HEADER_POLL_INTERVAL_MS = 500
MENU_WAIT_MS = 700
CONFIRM_WAIT_MS = 2500
ESCAPE_WAIT_MS = 300

_POLL_HEADER_MORE_JS = """() => {
  const header = document.querySelector('[data-testid="chat-header"]');
  const btn = header?.querySelector('button[aria-label^="More options"]');
  if (!btn) return {ok: false, step: 'more'};
  btn.click();
  return {ok: true, step: 'more'};
}"""

_DELETE_TRIGGERS_JS = """() => {
  const session = document.querySelector('[data-testid="delete-session-trigger"]');
  if (session) {
    session.click();
    return {ok: true, step: 'delete-trigger', testid: 'delete-session-trigger'};
  }
  const chat = document.querySelector('[data-testid="delete-chat-trigger"]');
  if (chat) {
    chat.click();
    return {ok: true, step: 'delete-trigger', testid: 'delete-chat-trigger'};
  }
  return {ok: false, step: 'delete-trigger'};
}"""

_CONFIRM_DELETE_JS = """() => {
  const buttons = [...document.querySelectorAll('button,[role=button]')];
  const del = buttons.find(
    (b) => /^delete$/i.test((b.innerText || '').trim()) && b.offsetParent
  );
  if (!del) return {ok: false, step: 'confirm'};
  del.click();
  return {ok: true, step: 'confirm'};
}"""

_EXTRACT_TITLE_JS = """() => {
  const header = document.querySelector('[data-testid="chat-header"]');
  const titleEl = header?.querySelector('h1, h2, [class*="title"]');
  const fromHeader = (titleEl?.textContent || '').trim();
  if (fromHeader) return fromHeader;
  const docTitle = (document.title || '').replace(/\\s*[-–|].*$/, '').trim();
  return docTitle;
}"""

_SIDEBAR_CLICK_MORE_JS = """(title) => {
  const prefix = 'More options for ';
  const target = prefix + title;
  const btn = [...document.querySelectorAll('button[aria-label^="More options for "]')]
    .find((b) => (b.getAttribute('aria-label') || '') === target);
  if (!btn) return {ok: false, step: 'sidebar-not-found', title};
  btn.scrollIntoView({ block: 'center' });
  btn.click();
  return {ok: true, step: 'sidebar-more', aria: btn.getAttribute('aria-label')};
}"""


def _force_delete_fail() -> bool:
    return os.environ.get("CDP_FORCE_DELETE_FAIL", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def in_active_chat(url: str) -> bool:
    u = url or ""
    return "/chat/" in u or "/cowork/cse_" in u


def _page_score(url: str) -> int:
    """Prefer live chat /new over project shell (5195 friction: harvest n=0)."""
    u = url or ""
    if "/chat/" in u or "/cowork/cse_" in u:
        return 100
    if u.rstrip("/").endswith("/new") or "/new?" in u:
        return 80
    if "/cowork/project/" in u:
        return 10
    if "claude.ai" in u:
        return 40
    return 0


async def pick_chat_page(ctx, *, prefer_url_substr: str | None = None):
    """Pick the best existing claude.ai tab; open one if none.

    5195 friction: ``next(claude.ai tab)`` grabbed the Project shell and
    harvest stayed at n=0. Prefer ``/chat/`` and ``/new``.
    """
    pages = list(ctx.pages)
    scored: list[tuple[int, object]] = []
    for page in pages:
        url = page.url or ""
        if "claude.ai" not in url:
            continue
        score = _page_score(url)
        if prefer_url_substr and prefer_url_substr in url:
            score += 50
        scored.append((score, page))
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        page = scored[0][1]
        await page.bring_to_front()
        return page
    page = await ctx.new_page()
    await page.bring_to_front()
    return page


async def _poll_header_more(page) -> dict:
    """Bounded poll for chat-header More options before step=more hard fail."""
    elapsed = 0
    last = {"ok": False, "step": "more"}
    while elapsed < HEADER_POLL_MS:
        last = await page.evaluate(_POLL_HEADER_MORE_JS)
        if last.get("ok"):
            return last
        await page.wait_for_timeout(HEADER_POLL_INTERVAL_MS)
        elapsed += HEADER_POLL_INTERVAL_MS
    return last


async def _click_delete_triggers(page) -> dict:
    r = await page.evaluate(_DELETE_TRIGGERS_JS)
    if r.get("ok"):
        return r
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(ESCAPE_WAIT_MS)
    return r


async def _confirm_delete(page) -> dict:
    return await page.evaluate(_CONFIRM_DELETE_JS)


async def _delete_via_header(page) -> dict:
    r1 = await _poll_header_more(page)
    if not r1.get("ok"):
        return {"ok": False, "step": "more", "path": "header", "attempts": {"more": r1}}

    await page.wait_for_timeout(MENU_WAIT_MS)
    r2 = await _click_delete_triggers(page)
    if not r2.get("ok"):
        return {
            "ok": False,
            "step": r2.get("step", "delete-trigger"),
            "path": "header",
            "attempts": {"more": r1, "trigger": r2},
        }

    await page.wait_for_timeout(MENU_WAIT_MS)
    r3 = await _confirm_delete(page)
    if not r3.get("ok"):
        return {
            "ok": False,
            "step": "confirm",
            "path": "header",
            "attempts": {"more": r1, "trigger": r2, "confirm": r3},
        }

    await page.wait_for_timeout(CONFIRM_WAIT_MS)
    return {
        "ok": True,
        "path": "header",
        "steps": {"more": r1, "trigger": r2, "confirm": r3},
    }


async def _delete_via_sidebar(page, *, title: str) -> dict:
    if not title:
        return {"ok": False, "step": "sidebar-not-found", "path": "sidebar", "title": ""}

    r1 = await page.evaluate(_SIDEBAR_CLICK_MORE_JS, title)
    if not r1.get("ok"):
        return {
            "ok": False,
            "step": r1.get("step", "sidebar-not-found"),
            "path": "sidebar",
            "title": title,
            "attempts": {"more": r1},
        }

    await page.wait_for_timeout(MENU_WAIT_MS)
    r2 = await _click_delete_triggers(page)
    if not r2.get("ok"):
        return {
            "ok": False,
            "step": r2.get("step", "delete-trigger"),
            "path": "sidebar",
            "title": title,
            "attempts": {"more": r1, "trigger": r2},
        }

    await page.wait_for_timeout(MENU_WAIT_MS)
    r3 = await _confirm_delete(page)
    if not r3.get("ok"):
        return {
            "ok": False,
            "step": "confirm",
            "path": "sidebar",
            "title": title,
            "attempts": {"more": r1, "trigger": r2, "confirm": r3},
        }

    await page.wait_for_timeout(CONFIRM_WAIT_MS)
    return {
        "ok": True,
        "path": "sidebar",
        "title": title,
        "steps": {"more": r1, "trigger": r2, "confirm": r3},
    }


async def delete_current_chat(page) -> dict:
    """Delete the active chat via header menu, with sidebar Recents fallback."""
    before = page.url or ""
    if _force_delete_fail():
        return {"ok": False, "step": "more", "url": before, "forced": True}

    header_result = await _delete_via_header(page)
    if header_result.get("ok"):
        return {
            "ok": True,
            "deleted_from": before,
            "landed_on": page.url,
            "path": "header",
            "steps": header_result.get("steps"),
        }

    title = await page.evaluate(_EXTRACT_TITLE_JS)
    sidebar_result = await _delete_via_sidebar(page, title=str(title or ""))
    if sidebar_result.get("ok"):
        return {
            "ok": True,
            "deleted_from": before,
            "landed_on": page.url,
            "path": "sidebar",
            "title": sidebar_result.get("title"),
            "steps": sidebar_result.get("steps"),
            "header_attempt": header_result,
        }

    fail_step = sidebar_result.get("step") or header_result.get("step") or "more"
    return {
        "ok": False,
        "step": fail_step,
        "url": before,
        "path": sidebar_result.get("path") or header_result.get("path"),
        "title": title,
        "attempts": {
            "header": header_result,
            "sidebar": sidebar_result,
        },
    }


async def delete_chat_if_active(page, *, return_to: str | None = None) -> dict:
    """Delete when on a chat URL; optionally navigate after."""
    if not in_active_chat(page.url or ""):
        return {"ok": True, "step": "skip_not_in_chat", "url": page.url, "cleanup_ok": True}

    result = await delete_current_chat(page)
    result["cleanup_ok"] = bool(result.get("ok"))
    if return_to:
        await page.goto(return_to, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2000)
        result["returned_to"] = page.url
    return result


def _compose_setup_error(
    *,
    step: str,
    url: str,
    result: dict,
    on_new: bool,
) -> RuntimeError:
    """Structured fail-closed error distinguishing /new toggle vs project shell."""
    mode_block = result.get("mode") or result.get("approval") or result
    payload: dict = {
        "step": step,
        "url": url,
        "surface": "bare_new" if on_new else "project_or_cse",
    }
    if isinstance(mode_block, dict):
        payload.update(
            {
                "inner_step": mode_block.get("step"),
                "before": mode_block.get("before"),
                "after": mode_block.get("after"),
                "via": mode_block.get("via"),
                "attest": mode_block.get("attest"),
            }
        )
    hint = (
        "new_compose_toggle_failed"
        if on_new
        else "project_shell_compose — mid-flight CSE lanes do not prove /new toggle"
    )
    payload["hint"] = hint
    return RuntimeError(f"{step} failed: {json.dumps(payload, default=str)}")


async def goto_fresh_compose(
    page,
    *,
    project_uuid: str | None = None,
    compose_url: str | None = None,
    ensure_cowork_auto: bool = True,
) -> str:
    """Land on a clean compose surface (Project chrome or bare /new).

    On bare ``/new``, default ``ensure_cowork_auto=True`` selects Cowork + Auto
    (friction 25051 — Chat CDP Send path broken). Pass ``ensure_cowork_auto=False``
    only on **operator-gated** Chat dispatches.
    """
    if compose_url:
        url = compose_url
    elif project_uuid:
        url = project_url(project_uuid)
    else:
        url = "https://claude.ai/new"
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(2500)
    # Chat/Cowork + Auto only exist on bare /new compose — not Project shell.
    on_new = url.rstrip("/").endswith("/new") or "/new?" in url
    if on_new and not project_uuid:
        from claude_bundles.chat_cowork_mode import (
            ensure_chat_compose as _ensure_chat,
        )
        from claude_bundles.chat_cowork_mode import (
            ensure_cowork_auto as _ensure_cowork,
        )

        if ensure_cowork_auto:
            result = await _ensure_cowork(page)
            if not result.get("ok"):
                raise _compose_setup_error(
                    step="ensure_cowork_auto",
                    url=page.url,
                    result=result,
                    on_new=on_new,
                )
        else:
            result = await _ensure_chat(page)
            if not result.get("ok"):
                raise _compose_setup_error(
                    step="ensure_chat_compose",
                    url=page.url,
                    result=result,
                    on_new=on_new,
                )
    return page.url
