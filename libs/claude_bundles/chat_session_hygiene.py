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
        return 100
    # Settings / scheduled / customize are hostile to sealed compose.
    if any(
        tok in u
        for tok in (
            "scheduled-task",
            "settings",
            "customize",
            "/artifacts",
            "/recents",
        )
    ):
        return 5
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


def compose_auth_failure_hint(url: str) -> str | None:
    """Return unauthenticated hint when *url* is a login/logout gate, else None."""
    low = (url or "").lower()
    if "/logout" in low or "/login" in low:
        return "new_compose_unauthenticated"
    return None


def classify_compose_setup_failure(result: dict, *, url: str, on_new: bool) -> dict:
    """Separate toggle-broken from approval-stuck and unauthenticated.

    Fleet conflation (2026-07-31): ``ensure_cowork_auto`` can attest Cowork
    successfully and still fail when approval stays on ``Manually approve``.
    The old hint always said ``new_compose_toggle_failed`` on bare ``/new``,
    so seats treated approval/auth failures as a broken Chat↔Cowork toggle.
    """
    auth_hint = compose_auth_failure_hint(url)
    if auth_hint:
        return {
            "failure_class": "unauthenticated",
            "hint": auth_hint,
            "failing_block": "url",
        }

    mode = result.get("mode") if isinstance(result.get("mode"), dict) else None
    approval = (
        result.get("approval") if isinstance(result.get("approval"), dict) else None
    )

    if mode is not None and not mode.get("ok"):
        hint = (
            "new_compose_toggle_failed"
            if on_new
            else "project_shell_compose — mid-flight CSE lanes do not prove /new toggle"
        )
        return {
            "failure_class": "toggle",
            "hint": hint,
            "failing_block": "mode",
            "mode": mode,
        }

    if approval is not None and not approval.get("ok"):
        after = approval.get("after") if isinstance(approval.get("after"), dict) else {}
        aria = ""
        approval_fp = after.get("approval")
        if isinstance(approval_fp, dict):
            aria = str(approval_fp.get("aria") or "")
        stuck_manual = "manually approve" in aria.lower()
        return {
            "failure_class": "approval",
            "hint": "new_compose_approval_failed",
            "failing_block": "approval",
            "stuck_manual": stuck_manual,
            "mode_ok": bool(mode and mode.get("ok")),
            "mode": mode,
            "approval": approval,
        }

    # ensure_chat_compose / unknown shapes — prefer the first non-ok nested block.
    for key in ("mode", "approval"):
        block = result.get(key)
        if isinstance(block, dict) and not block.get("ok", True):
            return {
                "failure_class": "toggle" if key == "mode" else "approval",
                "hint": (
                    "new_compose_toggle_failed"
                    if on_new and key == "mode"
                    else "new_compose_approval_failed"
                    if key == "approval"
                    else "project_shell_compose — mid-flight CSE lanes do not prove /new toggle"
                ),
                "failing_block": key,
                key: block,
            }

    hint = (
        "new_compose_toggle_failed"
        if on_new
        else "project_shell_compose — mid-flight CSE lanes do not prove /new toggle"
    )
    return {
        "failure_class": "toggle" if on_new else "project_shell",
        "hint": hint,
        "failing_block": "result",
        "result": result,
    }


def _compose_setup_error(
    *,
    step: str,
    url: str,
    result: dict,
    on_new: bool,
) -> RuntimeError:
    """Structured fail-closed error with failure_class (toggle|approval|unauthenticated)."""
    classified = classify_compose_setup_failure(result, url=url, on_new=on_new)
    failing_key = classified.get("failing_block")
    mode_block: dict
    if failing_key in {"mode", "approval"} and isinstance(
        classified.get(failing_key), dict
    ):
        mode_block = classified[failing_key]  # type: ignore[assignment]
    elif isinstance(result.get("mode"), dict):
        mode_block = result["mode"]
    elif isinstance(result.get("approval"), dict):
        mode_block = result["approval"]
    else:
        mode_block = result if isinstance(result, dict) else {}

    payload: dict = {
        "step": step,
        "url": url,
        "surface": "bare_new" if on_new else "project_or_cse",
        "failure_class": classified["failure_class"],
        "hint": classified["hint"],
        "stuck_manual": classified.get("stuck_manual"),
        "mode_ok": classified.get("mode_ok"),
    }
    if isinstance(mode_block, dict):
        payload.update(
            {
                "inner_step": mode_block.get("step"),
                "before": mode_block.get("before"),
                "after": mode_block.get("after"),
                "via": mode_block.get("via"),
                "attest": mode_block.get("attest"),
                "compose_mode_fingerprint": mode_block.get("compose_mode_fingerprint"),
                "candidates": mode_block.get("candidates"),
                "click_probe": mode_block.get("click_probe"),
                "surface_radiogroup_count": mode_block.get("surface_radiogroup_count"),
                "radiogroup_names": mode_block.get("radiogroup_names"),
                "gate_rejects": mode_block.get("gate_rejects"),
                "polled_ms": mode_block.get("polled_ms"),
            }
        )
    # Keep nested mode/approval when both exist so readers see the successful
    # Cowork attest beside the failed Auto flip (b7ea437d fingerprint).
    if isinstance(result.get("mode"), dict):
        payload["mode"] = result["mode"]
    if isinstance(result.get("approval"), dict):
        payload["approval"] = result["approval"]
    # Drop null optional keys for compact bus bodies.
    payload = {k: v for k, v in payload.items() if v is not None}
    return RuntimeError(f"{step} failed: {json.dumps(payload, default=str)}")


async def goto_fresh_compose(
    page,
    *,
    project_uuid: str | None = None,
    compose_url: str | None = None,
    ensure_cowork_auto: bool = True,
    stargate_execution_id: str = "",
    satellite_execution_id: str = "",
) -> str:
    """Land on a clean compose surface (Project chrome or bare /new).

    On bare ``/new``, default ``ensure_cowork_auto=True`` selects Cowork + Auto
    (friction 25051 — Chat CDP Send path broken). Pass ``ensure_cowork_auto=False``
    only on **operator-gated** Chat dispatches.

    ``stargate_execution_id`` / ``satellite_execution_id`` thread into
    ``cdp.generate.compose_attested`` on both arms (arc 6928 / 7034 B1 producer).
    """
    if compose_url:
        url = compose_url
    elif project_uuid:
        url = project_url(project_uuid)
    else:
        url = "https://claude.ai/new"
    await page.goto(url, wait_until="domcontentloaded", timeout=90000)
    await page.wait_for_timeout(2500)
    landed = page.url or url
    auth_hint = compose_auth_failure_hint(landed)
    if auth_hint:
        raise _compose_setup_error(
            step="compose_auth_preflight",
            url=landed,
            result={"ok": False, "step": "unauthenticated", "url": landed},
            on_new=True,
        )
    # Chat/Cowork + Auto only exist on bare /new compose — not Project shell.
    on_new = url.rstrip("/").endswith("/new") or "/new?" in url
    if on_new and not project_uuid:
        from claude_bundles.compose_setup_emit import ensure_bare_new_compose

        result = await ensure_bare_new_compose(
            page,
            ensure_cowork_auto=ensure_cowork_auto,
            stargate_execution_id=stargate_execution_id,
            satellite_execution_id=satellite_execution_id,
        )
        if not result.get("ok"):
            raise _compose_setup_error(
                step=(
                    "ensure_cowork_auto"
                    if ensure_cowork_auto
                    else "ensure_chat_compose"
                ),
                url=page.url,
                result=result,
                on_new=on_new,
            )
    return page.url
