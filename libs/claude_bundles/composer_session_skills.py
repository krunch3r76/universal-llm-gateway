"""Cowork composer session-skill attach via + → Skills → pick.

Slash-type multi-chip is broken on Cowork (friction a25806 — only the first
``/<slug>`` binds). Operator bind 2026-07-26: open the composer **+** control,
choose **Skills**, then select each Customize skill from the list one by one.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from claude_bundles.cowork_skill_delivery import SkillDeliveryError

_PLUS_ARIA = re.compile(
    r"^(add(\s+(attachment|files?|content|more))?|plus|\+|attach|"
    r"open (menu|attachments?)|more options?)$",
    re.I,
)
_PLUS_LOOSE = re.compile(r"\b(add|plus|attach|attachment)\b|\+|＋", re.I)
_SKILLS_ITEM = re.compile(r"^skills?\b", re.I)
_EXCLUDE_PLUS = re.compile(
    r"(upload|image|photo|model|approve|send|start task|stop|"
    r"voice|dictate|settings|sidebar|new chat|new task|quick task|"
    r"project|filter|sort|scheduled|search|collapse|home|code|"
    r"customize|artifacts|chats and tasks|pinned|surface|cowork|"
    r"write your prompt|chat-input|one.time|follow.up|"
    r"press and hold|model:|automatically approve)",
    re.I,
)
_ADD_MENU_ARIA = re.compile(
    r"add\s+files?.*connectors|add\s+attachment|add\s+content|more options",
    re.I,
)
_NAV_AWAY_URL = re.compile(
    r"/scheduled-task/|/settings/|/projects/|/artifacts(?:/|$)",
    re.I,
)

_COMPOSE_URL = re.compile(r"/new(?:\?|$|#)|/cowork/cse_|/chat/", re.I)
_MIN_PLUS_SCORE = 30
_MAX_PLUS_LABEL_CHARS = 28


def _slug_matchers(slug: str) -> list[re.Pattern[str]]:
    """Match Customize list labels — slug, spaced, or title-ish variants."""
    raw = slug.strip()
    spaced = raw.replace("-", " ")
    return [
        re.compile(rf"^{re.escape(raw)}$", re.I),
        re.compile(rf"^{re.escape(spaced)}$", re.I),
        re.compile(re.escape(raw), re.I),
        re.compile(re.escape(spaced), re.I),
    ]


def require_compose_surface(page: Page) -> str:
    """Fail closed unless the tab is a Cowork/Chat compose surface."""
    url = page.url or ""
    if not _COMPOSE_URL.search(url):
        raise SkillDeliveryError(
            f"Skills attach requires /new|/cowork/cse_|/chat/ compose — on {url!r}"
        )
    return url


async def inventory_composer_controls(page: Page) -> dict[str, Any]:
    """Page-wide control inventory near the composer — for attach + diagnostics."""
    return await page.evaluate(
        """() => {
          const composer = document.querySelector('[data-testid="chat-input"]')
            || document.querySelector('[contenteditable="true"][data-testid]')
            || document.querySelector('[contenteditable="true"]')
            || document.querySelector('[role="textbox"]');
          const cbox = composer ? composer.getBoundingClientRect() : null;
          const nodes = Array.from(document.querySelectorAll(
            'button, [role="button"], a[aria-label], div[aria-label]'
          ));
          const buttons = nodes.map((b, i) => {
            const r = b.getBoundingClientRect();
            const aria = (b.getAttribute('aria-label') || '').trim();
            const text = (b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
            const title = (b.getAttribute('title') || '').trim();
            const svg = !!b.querySelector('svg');
            const href = (b.getAttribute('href') || '').trim();
            const vNear = cbox
              ? (Math.abs((r.top + r.bottom) / 2 - (cbox.top + cbox.bottom) / 2) < 140
                 && r.bottom >= cbox.top - 60
                 && r.top <= cbox.bottom + 100)
              : false;
            const hNear = cbox
              ? (r.right >= cbox.left - 48
                 && r.left <= cbox.right + 48
                 && r.width > 0)
              : false;
            const near = vNear && hNear;
            const visible = !!(r.width && r.height
              && r.bottom > 0 && r.right > 0
              && r.top < (window.innerHeight || 0)
              && r.left < (window.innerWidth || 0));
            return {
              i, tag: b.tagName, aria, text, title, svg, near, hNear, visible, href,
              haspopup: b.getAttribute('aria-haspopup') || '',
              expanded: b.getAttribute('aria-expanded') || '',
              testid: b.getAttribute('data-testid') || '',
              y: Math.round(r.top), x: Math.round(r.left),
            };
          });
          const crect = cbox
            ? { left: Math.round(cbox.left), right: Math.round(cbox.right),
                top: Math.round(cbox.top), bottom: Math.round(cbox.bottom) }
            : null;
          return {
            url: location.href,
            has_composer: !!composer,
            composer_testid: composer && composer.getAttribute('data-testid'),
            composer_box: crect,
            buttons,
            near: buttons.filter((b) => b.visible && b.near),
            visible: buttons.filter((b) => b.visible).slice(0, 50),
          };
        }"""
    )


def _has_plus_signal(row: dict[str, Any]) -> bool:
    aria = str(row.get("aria") or "")
    text = str(row.get("text") or "")
    title = str(row.get("title") or "")
    testid = str(row.get("testid") or "")
    blob = f"{aria} {text} {title}".strip()
    if _ADD_MENU_ARIA.search(aria) or _ADD_MENU_ARIA.search(blob):
        return True
    if _PLUS_ARIA.match(aria) or _PLUS_ARIA.match(text) or text in {"+", "＋"}:
        return True
    if _PLUS_LOOSE.search(blob):
        return True
    if testid and re.search(r"add|plus|attach", testid, re.I):
        return True
    if (
        row.get("svg")
        and not text
        and not aria
        and row.get("near")
        and row.get("h_near")
    ):
        return True
    return False


def _score_plus_candidate(row: dict[str, Any]) -> int:
    """Higher = more likely the composer + control."""
    aria = str(row.get("aria") or "")
    text = str(row.get("text") or "")
    title = str(row.get("title") or "")
    testid = str(row.get("testid") or "")
    tag = str(row.get("tag") or "").upper()
    href = str(row.get("href") or "")
    blob = f"{aria} {text} {title}".strip()
    if _ADD_MENU_ARIA.search(aria) or _ADD_MENU_ARIA.search(blob):
        return 90
    if _EXCLUDE_PLUS.search(blob):
        return -100
    if testid == "chat-input" or re.search(r"chat-input|composer", testid, re.I):
        return -100
    if tag == "A" and href and not _PLUS_LOOSE.search(blob):
        return -100
    if _NAV_AWAY_URL.search(href):
        return -100
    if text and len(text) > _MAX_PLUS_LABEL_CHARS and not _PLUS_LOOSE.search(blob):
        return -100
    if row.get("near") and not row.get("h_near"):
        return -100
    score = 0
    if row.get("near") and row.get("h_near"):
        score += 35
    if _PLUS_ARIA.match(aria) or _PLUS_ARIA.match(text) or text in {"+", "＋"}:
        score += 45
    elif _PLUS_LOOSE.search(blob):
        score += 25
    if row.get("haspopup"):
        score += 10
    if row.get("svg") and not text and not aria and row.get("h_near"):
        score += 20
    if testid and re.search(r"add|plus|attach", testid, re.I):
        score += 25
    return score


def _is_usable_plus_candidate(row: dict[str, Any]) -> bool:
    return _score_plus_candidate(row) >= _MIN_PLUS_SCORE and _has_plus_signal(row)


async def _open_menu_items(page: Page) -> list[dict[str, str]]:
    return await page.evaluate(
        """() => {
          const nodes = [
            ...document.querySelectorAll(
              '[role="menuitem"], [role="option"], [role="menuitemcheckbox"], [role="menuitemradio"]'
            ),
            ...document.querySelectorAll('[cmdk-item], [data-radix-collection-item]'),
            ...document.querySelectorAll('[role="menu"] button, [role="listbox"] button'),
          ];
          return nodes.map((el) => ({
            role: el.getAttribute('role') || el.tagName,
            text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
            aria: (el.getAttribute('aria-label') || '').trim(),
          })).filter((row) => row.text || row.aria);
        }"""
    )


_PUA_RE = re.compile(r"[\ue000-\uf8ff]")


def _norm_menu_label(label: str) -> str:
    return _PUA_RE.sub("", label).strip()


async def _click_skills_entry(page: Page) -> None:
    clicked = await page.evaluate(
        """() => {
          const nodes = [
            ...document.querySelectorAll(
              '[role="menuitem"], [role="option"], [cmdk-item], [data-radix-collection-item]'
            ),
            ...document.querySelectorAll('[role="menu"] button'),
          ];
          for (const el of nodes) {
            const raw = (el.innerText || el.textContent || '').trim();
            const norm = raw.replace(/[\\uE000-\\uF8FF]/g, '').trim();
            if (/^skills?$/i.test(norm)) {
              el.scrollIntoView({block: 'nearest', inline: 'nearest'});
              el.click();
              return {ok: true, text: raw};
            }
          }
          return {ok: false};
        }"""
    )
    if not clicked or not clicked.get("ok"):
        items = await _open_menu_items(page)
        raise SkillDeliveryError(f"Skills menu entry not found — items={items[:20]!r}")


async def _click_menu_text(page: Page, pattern: re.Pattern[str], *, what: str) -> None:
    items = await _open_menu_items(page)
    for row in items:
        label = row.get("text") or row.get("aria") or ""
        norm = _norm_menu_label(label)
        if pattern.search(label) or pattern.search(norm):
            loc = page.get_by_role(
                "menuitem", name=re.compile(re.escape(norm[:60]), re.I)
            )
            if await loc.count() == 0:
                loc = page.get_by_role(
                    "option", name=re.compile(re.escape(norm[:60]), re.I)
                )
            if await loc.count() == 0:
                loc = page.locator('[role="menuitem"], [role="option"]').filter(
                    has_text=re.compile(re.escape(norm[:60]), re.I)
                )
            if await loc.count() == 0:
                clicked = await page.evaluate(
                    """([needle]) => {
                      const re = new RegExp(needle, 'i');
                      const nodes = [
                        ...document.querySelectorAll(
                          '[role="menuitem"], [role="option"], [cmdk-item]'
                        ),
                      ];
                      for (const el of nodes) {
                        const raw = (el.innerText || el.textContent || '').trim();
                        const norm = raw.replace(/[\\uE000-\\uF8FF]/g, '').trim();
                        if (re.test(norm) || re.test(raw)) {
                          el.scrollIntoView({block: 'nearest'});
                          el.click();
                          return {ok: true};
                        }
                      }
                      return {ok: false};
                    }""",
                    [pattern.pattern],
                )
                if clicked and clicked.get("ok"):
                    return
            else:
                await loc.first.click(force=True)
                return
    raise SkillDeliveryError(
        f"menu item for {what} not found (pattern={pattern.pattern}) — items={items[:20]!r}"
    )


async def _ranked_plus_candidates(page: Page) -> list[dict[str, Any]]:
    require_compose_surface(page)
    inv = await inventory_composer_controls(page)
    if not inv.get("has_composer"):
        raise SkillDeliveryError(
            f"composer not found before Skills attach — inventory={inv!r}"
        )
    # Prefer controls next to the composer — sidebar Scheduled/Quick task misleads.
    near = [b for b in inv.get("buttons", []) if b.get("visible") and b.get("near")]
    pool = near or [
        b
        for b in inv.get("buttons", [])
        if b.get("visible") and b.get("h_near")
    ]
    ranked = sorted(pool, key=_score_plus_candidate, reverse=True)
    usable = [b for b in ranked if _is_usable_plus_candidate(b)]
    if not usable:
        raise SkillDeliveryError(
            "composer + / Add control not found for Skills attach — "
            f"near={inv.get('near')!r} visible={inv.get('visible')!r}"
        )
    return usable[:8]


async def _recover_compose_if_needed(page: Page) -> None:
    """Return to bare /new when a mis-click leaves scheduled-task/settings."""
    url = page.url or ""
    if _NAV_AWAY_URL.search(url):
        from claude_bundles.chat_session_hygiene import goto_fresh_compose

        await goto_fresh_compose(page, compose_url="https://claude.ai/new")
        await page.wait_for_timeout(800)
        require_compose_surface(page)
        return
    inv = await inventory_composer_controls(page)
    if _COMPOSE_URL.search(url) and inv.get("has_composer"):
        return
    from claude_bundles.chat_session_hygiene import goto_fresh_compose

    await goto_fresh_compose(page, compose_url="https://claude.ai/new")
    await page.wait_for_timeout(800)
    require_compose_surface(page)


async def _click_inventory_control(page: Page, cand: dict[str, Any]) -> None:
    """Click a control from ``inventory_composer_controls`` without stale nth()."""
    result = await page.evaluate(
        """(target) => {
          const nodes = Array.from(document.querySelectorAll(
            'button, [role="button"], a[aria-label], div[aria-label]'
          ));
          const el = nodes[target.i];
          if (!el) return {ok: false, reason: 'missing_index', n: nodes.length};
          const aria = (el.getAttribute('aria-label') || '').trim();
          const text = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
          const title = (el.getAttribute('title') || '').trim();
          const testid = el.getAttribute('data-testid') || '';
          const fingerprint_ok = (
            aria === (target.aria || '')
            && text === (target.text || '')
            && title === (target.title || '')
            && testid === (target.testid || '')
          );
          if (!fingerprint_ok) {
            const hit = nodes.find((b) => {
              const r = b.getBoundingClientRect();
              if (!(r.width && r.height)) return false;
              const a = (b.getAttribute('aria-label') || '').trim();
              const t = (b.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
              if (target.aria && a === target.aria) return true;
              if (target.text && t === target.text) return true;
              if (target.testid && (b.getAttribute('data-testid') || '') === target.testid) {
                return true;
              }
              return false;
            });
            if (!hit) {
              return {
                ok: false,
                reason: 'fingerprint_mismatch',
                got: {aria, text, title, testid},
              };
            }
            hit.scrollIntoView({block: 'nearest', inline: 'nearest'});
            hit.click();
            return {ok: true, via: 'fingerprint_fallback'};
          }
          el.scrollIntoView({block: 'nearest', inline: 'nearest'});
          el.click();
          return {ok: true, via: 'index'};
        }""",
        {
            "i": cand.get("i"),
            "aria": cand.get("aria") or "",
            "text": cand.get("text") or "",
            "title": cand.get("title") or "",
            "testid": cand.get("testid") or "",
        },
    )
    if not result or not result.get("ok"):
        raise SkillDeliveryError(
            f"failed to click composer + candidate {cand!r} — result={result!r}"
        )


async def _open_plus_skills_menu(page: Page) -> dict[str, Any]:
    """Click composer + candidates until a menu containing Skills appears."""
    tried: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for _ in range(8):
        await _recover_compose_if_needed(page)
        cands = await _ranked_plus_candidates(page)
        cand = next(
            (
                c
                for c in cands
                if (
                    c.get("i"),
                    c.get("aria"),
                    c.get("text"),
                    c.get("testid"),
                    c.get("x"),
                    c.get("y"),
                )
                not in seen
            ),
            None,
        )
        if cand is None:
            break
        fp = (
            cand.get("i"),
            cand.get("aria"),
            cand.get("text"),
            cand.get("testid"),
            cand.get("x"),
            cand.get("y"),
        )
        seen.add(fp)
        try:
            await _click_inventory_control(page, cand)
        except SkillDeliveryError as exc:
            tried.append({"cand": cand, "click_error": str(exc)})
            await _recover_compose_if_needed(page)
            continue
        await page.wait_for_timeout(450)
        if not _COMPOSE_URL.search(page.url or "") or _NAV_AWAY_URL.search(page.url or ""):
            tried.append(
                {
                    "cand": {k: cand.get(k) for k in ("i", "aria", "text", "testid")},
                    "navigated_away": page.url,
                }
            )
            await _recover_compose_if_needed(page)
            continue
        items = await _open_menu_items(page)
        tried.append(
            {
                "cand": {
                    k: cand.get(k)
                    for k in ("i", "aria", "text", "title", "testid", "y", "x")
                },
                "score": _score_plus_candidate(cand),
                "items_head": items[:12],
            }
        )
        if any(
            _SKILLS_ITEM.search(_norm_menu_label(row.get("text") or row.get("aria") or ""))
            for row in items
        ):
            return {"ok": True, "cand": cand, "items": items}
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
    raise SkillDeliveryError(
        "no composer + control opened a Skills menu — tried="
        f"{tried!r}"
    )


async def _click_skill_slug(page: Page, slug: str) -> None:
    for pat in _slug_matchers(slug):
        try:
            await _click_menu_text(page, pat, what=f"skill:{slug}")
            return
        except SkillDeliveryError:
            continue
    items = await _open_menu_items(page)
    raise SkillDeliveryError(
        f"skill {slug!r} not in Skills list — items={items[:30]!r}"
    )


async def attach_session_skills(page: Page, slugs: list[str], *, composer) -> list[str]:
    """Attach each Customize skill via + → Skills → pick. Returns attached slugs."""
    if not slugs:
        return []
    require_compose_surface(page)
    attached: list[str] = []
    for slug in slugs:
        require_compose_surface(page)
        await composer.click(force=True)
        await page.wait_for_timeout(350)
        await _open_plus_skills_menu(page)
        await _click_skills_entry(page)
        await page.wait_for_timeout(600)
        await _click_skill_slug(page, slug)
        await page.wait_for_timeout(450)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        attached.append(slug)
    return attached
