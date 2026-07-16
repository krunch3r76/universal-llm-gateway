"""claude.ai chat hygiene — delete finished sessions after sealed asks.

Binding (friction 5195 / CDP ask pivot): every disposable subagent ask MUST
end with delete of that chat before the next ask. Skipping delete lets prior
context leak into the next compose.

Verified path (2026-07-16):
  header More options → delete-chat-trigger → confirm Delete (JS clicks).
Post-delete often lands on /new; re-navigate before next send if needed.
"""

from __future__ import annotations

from claude_bundles.project_chrome import project_url


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


async def delete_current_chat(page) -> dict:
    """Delete the active chat via header menu. Uses JS clicks (Playwright flaky)."""
    before = page.url
    r1 = await page.evaluate(
        """() => {
      const header = document.querySelector('[data-testid="chat-header"]');
      const btn = header?.querySelector('button[aria-label^="More options"]');
      if (!btn) return {ok:false, step:'more'};
      btn.click();
      return {ok:true, step:'more'};
    }"""
    )
    if not r1.get("ok"):
        return {"ok": False, "step": "more", "url": before}

    await page.wait_for_timeout(900)
    r2 = await page.evaluate(
        """() => {
      const del = document.querySelector('[data-testid="delete-chat-trigger"]');
      if (!del) return {ok:false, step:'delete-trigger'};
      del.click();
      return {ok:true, step:'delete-trigger'};
    }"""
    )
    if not r2.get("ok"):
        return {"ok": False, "step": "delete-trigger", "url": before}

    await page.wait_for_timeout(900)
    r3 = await page.evaluate(
        """() => {
      const buttons = [...document.querySelectorAll('button,[role=button]')];
      const del = buttons.find(
        (b) => /^delete$/i.test((b.innerText || '').trim()) && b.offsetParent
      );
      if (!del) return {ok:false, step:'confirm'};
      del.click();
      return {ok:true, step:'confirm'};
    }"""
    )
    if not r3.get("ok"):
        return {"ok": False, "step": "confirm", "url": before}

    await page.wait_for_timeout(2500)
    return {
        "ok": True,
        "deleted_from": before,
        "landed_on": page.url,
        "steps": {"more": r1, "trigger": r2, "confirm": r3},
    }


async def delete_chat_if_active(page, *, return_to: str | None = None) -> dict:
    """Delete when on a chat URL; optionally navigate after."""
    if not in_active_chat(page.url or ""):
        return {"ok": True, "step": "skip_not_in_chat", "url": page.url}

    result = await delete_current_chat(page)
    if return_to:
        await page.goto(return_to, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2000)
        result["returned_to"] = page.url
    return result


async def goto_fresh_compose(
    page,
    *,
    project_uuid: str | None = None,
    compose_url: str | None = None,
    ensure_cowork_auto: bool = True,
) -> str:
    """Land on a clean compose surface (Project chrome or bare /new).

    On bare ``/new``, default ``ensure_cowork_auto=True`` selects Cowork +
    Automatically approve before the caller picks a model / sends
    (``agent_skill:claude-ai-cdp-navigation`` § Cowork + Auto).
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
    if ensure_cowork_auto and on_new and not project_uuid:
        from claude_bundles.chat_cowork_mode import ensure_cowork_auto as _ensure

        result = await _ensure(page)
        if not result.get("ok"):
            # Soft: Project-less /new without chips is a UI regression to surface.
            page_err = result.get("mode") or result.get("approval") or result
            raise RuntimeError(f"ensure_cowork_auto failed: {page_err}")
    return page.url
