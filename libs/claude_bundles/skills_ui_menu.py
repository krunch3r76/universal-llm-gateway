"""claude.ai Skills Add → Upload menu — inventory snapshot, selection, click.

Callers: ``run_preflight``, ``_open_upload_dialog``, ``diagnose_upload_menu_session``.
Selection is a pure function over a JS snapshot so label/portal variants stay
testable without CDP. The Add control is a Base UI menu trigger
(``aria-haspopup`` + ``aria-controls`` + ``data-popup-open``); the popup is a
portal, not a Radix ``data-state=open`` child.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import Locator, Page

_UPLOAD_CANON = re.compile(r"upload a skill", re.I)
_UPLOAD_DRIFT = re.compile(r"upload", re.I)

_SNAPSHOT_JS = """(btn) => {
  const el = btn || document.querySelector('button[aria-label="Add skill"]')
    || document.querySelector('button[aria-haspopup="menu"]');
  const trigger = {
    found: !!el,
    aria_label: el ? (el.getAttribute('aria-label') || '') : '',
    aria_haspopup: el ? (el.getAttribute('aria-haspopup') || '') : '',
    aria_expanded: el ? (el.getAttribute('aria-expanded') || '') : '',
    aria_controls: el ? (el.getAttribute('aria-controls') || '') : '',
    data_popup_open: !!(el && el.hasAttribute('data-popup-open')),
    id: el ? (el.id || '') : '',
  };
  const menuId = trigger.aria_controls;
  let root = menuId ? document.getElementById(menuId) : null;
  let found_by = root ? 'aria-controls' : 'none';
  if (!root) {
    const menus = [...document.querySelectorAll('[role=menu]')].filter((m) => {
      const st = getComputedStyle(m);
      return st.visibility !== 'hidden' && st.display !== 'none'
        && m.getClientRects().length;
    });
    root = menus.length ? menus[menus.length - 1] : null;
    if (root) found_by = 'role-menu';
  }
  const nodes = root ? [...root.querySelectorAll('[role=menuitem]')] : [];
  const items = nodes.map((e, index) => {
    const st = getComputedStyle(e);
    const visible = st.visibility !== 'hidden' && st.display !== 'none'
      && e.getClientRects().length > 0;
    return {
      index,
      text: (e.innerText || '').trim(),
      role: e.getAttribute('role') || 'menuitem',
      aria_haspopup: e.getAttribute('aria-haspopup') || '',
      aria_disabled: e.getAttribute('aria-disabled') || '',
      visible,
      id: e.id || '',
    };
  });
  return {
    trigger,
    popup: {
      found_by,
      id: root ? (root.id || menuId || '') : '',
      mounted: !!root,
      visible: !!(root && getComputedStyle(root).visibility !== 'hidden'),
      menuitem_count: items.length,
    },
    items,
  };
}"""

_CLICK_INDEX_JS = """([el, index]) => {
  const btn = el || document.querySelector('button[aria-label="Add skill"]')
    || document.querySelector('button[aria-haspopup="menu"]');
  const menuId = btn && btn.getAttribute('aria-controls');
  let root = menuId && document.getElementById(menuId);
  if (!root) {
    const menus = [...document.querySelectorAll('[role=menu]')].filter((m) => {
      const st = getComputedStyle(m);
      return st.visibility !== 'hidden' && st.display !== 'none'
        && m.getClientRects().length;
    });
    root = menus.length ? menus[menus.length - 1] : null;
  }
  const items = root ? [...root.querySelectorAll('[role=menuitem]')] : [];
  const target = items[index];
  if (!target) return {ok: false, n: items.length};
  target.click();
  return {ok: true, n: items.length};
}"""


@dataclass(frozen=True)
class MenuItem:
    """One ``[role=menuitem]`` in the Add popup; ``index`` is snapshot order."""

    index: int
    text: str
    role: str
    aria_haspopup: str
    aria_disabled: str
    visible: bool
    id: str


@dataclass(frozen=True)
class MenuTrigger:
    """Add-button attrs that key the Base UI popup portal."""

    found: bool
    aria_label: str = ""
    aria_haspopup: str = ""
    aria_expanded: str = ""
    aria_controls: str = ""
    data_popup_open: bool = False
    id: str = ""


@dataclass(frozen=True)
class MenuPopup:
    """Resolved popup root: ``aria-controls`` id, else visible ``[role=menu]``."""

    found_by: str
    id: str = ""
    mounted: bool = False
    visible: bool = False
    menuitem_count: int = 0


@dataclass(frozen=True)
class UploadSelection:
    """Pure result of ``select_upload_item`` — no Playwright."""

    status: str
    index: int | None = None
    text: str = ""
    reason: str = ""
    drift: bool = False


@dataclass
class MenuInventory:
    """Observe-only snapshot of the Add → menu portal (no clicks)."""

    url: str
    trigger: MenuTrigger
    popup: MenuPopup
    items: list[MenuItem] = field(default_factory=list)
    captured_at: str = ""

    @property
    def portal_mounted(self) -> bool:
        return self.popup.mounted

    @property
    def menuitem_count(self) -> int:
        return self.popup.menuitem_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "url": self.url,
            "trigger": asdict(self.trigger),
            "popup": asdict(self.popup),
            "items": [asdict(item) for item in self.items],
        }


class PreflightMenuError(RuntimeError):
    """Add is visible but Upload is not selectable — preflight fail-closed.

    Carries the last ``MenuInventory`` so CLI / run reports can write
    ``preflight.json`` without a second CDP snapshot.
    """

    def __init__(self, message: str, inventory: MenuInventory) -> None:
        super().__init__(message)
        self.inventory = inventory


class MenuDiscoveryError(RuntimeError):
    """Upload dialog open failed after the existing 5-attempt loop.

    ``inventory`` is the last Add-menu snapshot so failure evidence can write
    ``menu.json`` instead of an empty Radix overlay dump.
    """

    def __init__(self, message: str, inventory: MenuInventory) -> None:
        super().__init__(message)
        self.inventory = inventory


def _item_disabled(item: MenuItem) -> bool:
    return (item.aria_disabled or "").lower() in ("true", "1")


def select_upload_item(inv: MenuInventory) -> UploadSelection:
    """Pick the Upload menuitem from a snapshot. Refuse to guess on ≥2 matches."""
    if not inv.popup.mounted:
        return UploadSelection(status="none", reason="portal_not_mounted")
    visible = [i for i in inv.items if i.visible and not _item_disabled(i)]
    canon = [i for i in visible if _UPLOAD_CANON.search(i.text)]
    if len(canon) == 1:
        return UploadSelection(
            status="found", index=canon[0].index, text=canon[0].text, reason="canonical"
        )
    if len(canon) > 1:
        return UploadSelection(status="ambiguous", reason="multiple_canonical")
    drift = [i for i in visible if _UPLOAD_DRIFT.search(i.text)]
    if len(drift) == 1:
        return UploadSelection(
            status="drift",
            index=drift[0].index,
            text=drift[0].text,
            reason="label_drift",
            drift=True,
        )
    if len(drift) > 1:
        return UploadSelection(status="ambiguous", reason="multiple_upload")
    submenu = [i for i in visible if (i.aria_haspopup or "").lower() == "menu"]
    if submenu:
        return UploadSelection(
            status="needs_submenu",
            index=submenu[0].index,
            text=submenu[0].text,
            reason="nested_menu",
        )
    return UploadSelection(status="none", reason="no_upload_item")


def assert_preflight_selection(inv: MenuInventory) -> UploadSelection:
    """Raise if this snapshot cannot reach Upload (found, drift, or one submenu)."""
    sel = select_upload_item(inv)
    if sel.status in ("found", "drift", "needs_submenu"):
        return sel
    raise PreflightMenuError(
        f"Preflight failed: Upload menuitem not selectable ({sel.reason}) "
        f"mounted={inv.portal_mounted} menuitem_count={inv.menuitem_count}",
        inv,
    )


def diagnose_payload(
    inv: MenuInventory,
    *,
    panel_visible: bool,
    rows: int,
    composer_chips: bool,
    selection: UploadSelection,
    evidence_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """JSON contract shared by preflight / diagnose / failure ``menu.json``."""
    payload = inv.to_dict()
    payload["panel_visible"] = panel_visible
    payload["rows"] = rows
    payload["selection"] = asdict(selection)
    payload["composer_chips"] = composer_chips
    payload["evidence_paths"] = evidence_paths or {}
    return payload


def inventory_from_raw(raw: dict[str, Any], url: str) -> MenuInventory:
    """Hydrate ``MenuInventory`` from ``snapshot_add_menu`` JS and stamp ``captured_at``."""
    trig = raw.get("trigger") or {}
    pop = raw.get("popup") or {}
    items = [
        MenuItem(
            index=int(item.get("index", i)),
            text=str(item.get("text") or ""),
            role=str(item.get("role") or "menuitem"),
            aria_haspopup=str(item.get("aria_haspopup") or ""),
            aria_disabled=str(item.get("aria_disabled") or ""),
            visible=bool(item.get("visible")),
            id=str(item.get("id") or ""),
        )
        for i, item in enumerate(raw.get("items") or [])
    ]
    return MenuInventory(
        url=url,
        captured_at=datetime.now(UTC).isoformat(),
        trigger=MenuTrigger(
            found=bool(trig.get("found")),
            aria_label=str(trig.get("aria_label") or ""),
            aria_haspopup=str(trig.get("aria_haspopup") or ""),
            aria_expanded=str(trig.get("aria_expanded") or ""),
            aria_controls=str(trig.get("aria_controls") or ""),
            data_popup_open=bool(trig.get("data_popup_open")),
            id=str(trig.get("id") or ""),
        ),
        popup=MenuPopup(
            found_by=str(pop.get("found_by") or "none"),
            id=str(pop.get("id") or ""),
            mounted=bool(pop.get("mounted")),
            visible=bool(pop.get("visible")),
            menuitem_count=int(pop.get("menuitem_count") or len(items)),
        ),
        items=items,
    )


def empty_inventory(url: str, *, reason: str = "no_add_button") -> MenuInventory:
    """Inventory when Add is absent — diagnose still emits a JSON shape."""
    del reason
    return MenuInventory(
        url=url,
        captured_at=datetime.now(UTC).isoformat(),
        trigger=MenuTrigger(found=False),
        popup=MenuPopup(found_by="none"),
    )


async def add_menu_expanded(add_btn: Locator) -> bool:
    """True when Add's ``aria-expanded`` is true — a second click would toggle shut."""
    try:
        expanded = await add_btn.get_attribute("aria-expanded", timeout=2_000)
    except Exception:
        return False
    return (expanded or "").lower() == "true"


async def stability_guarded_add_click(add_btn: Locator) -> None:
    """Open Add menu; skip click when already expanded (re-click toggles shut)."""
    await add_btn.scroll_into_view_if_needed()
    await add_btn.wait_for(state="visible", timeout=3_000)
    if await add_menu_expanded(add_btn):
        return
    await add_btn.click(timeout=3_000)


async def snapshot_add_menu(page: Page, add_btn: Locator | None) -> MenuInventory:
    """Observe-only JS snapshot of the Add trigger + popup menuitems."""
    handle = await add_btn.element_handle() if add_btn is not None else None
    raw = await page.evaluate(_SNAPSHOT_JS, handle)
    return inventory_from_raw(raw or {}, page.url)


async def wait_menu_idle(
    page: Page, add_btn: Locator | None, *, timeout_ms: int = 4_000
) -> MenuInventory:
    """Poll until portal is mounted and menuitem count is stable across two ticks."""
    deadline = timeout_ms
    step = 200
    last: MenuInventory | None = None
    prev_count: int | None = None
    prev_mounted = False
    while deadline > 0:
        last = await snapshot_add_menu(page, add_btn)
        if last.popup.mounted and last.menuitem_count == prev_count and prev_mounted:
            return last
        prev_count = last.menuitem_count
        prev_mounted = last.popup.mounted
        await page.wait_for_timeout(step)
        deadline -= step
    return last or empty_inventory(page.url)


async def js_click_menuitem_at(page: Page, add_btn: Locator, index: int) -> dict:
    """Click ``items[index]`` inside the same scoped root the snapshot used."""
    handle = await add_btn.element_handle()
    return await page.evaluate(_CLICK_INDEX_JS, [handle, index])


async def resolve_upload_selection(
    page: Page, add_btn: Locator, inv: MenuInventory
) -> tuple[UploadSelection, MenuInventory]:
    """Select; one-level submenu hop then re-snapshot. No inner retry loop."""
    sel = select_upload_item(inv)
    if sel.status == "needs_submenu" and sel.index is not None:
        await js_click_menuitem_at(page, add_btn, sel.index)
        inv = await wait_menu_idle(page, add_btn, timeout_ms=2_000)
        sel = select_upload_item(inv)
        if sel.status == "needs_submenu":
            sel = UploadSelection(status="none", reason="submenu_too_deep")
    return sel, inv


async def assert_add_menu_upload_ready(page: Page, add_btn: Locator) -> MenuInventory:
    """Idle-wait + optional submenu hop; raise if Upload is still not selectable."""
    inv = await wait_menu_idle(page, add_btn)
    assert_preflight_selection(inv)
    sel, inv = await resolve_upload_selection(page, add_btn, inv)
    if sel.status not in ("found", "drift"):
        raise PreflightMenuError(
            f"Preflight failed: Upload menuitem not selectable ({sel.reason}) "
            f"mounted={inv.portal_mounted} menuitem_count={inv.menuitem_count}",
            inv,
        )
    return inv


async def js_click_upload_menuitem(page: Page, add_btn: Locator) -> dict:
    """Snapshot → select → click-by-index. Returns ``{ok, n}`` plus inventory."""
    inv = await snapshot_add_menu(page, add_btn)
    sel = select_upload_item(inv)
    if sel.index is None:
        return {"ok": False, "n": inv.menuitem_count, "inventory": inv.to_dict()}
    result = await js_click_menuitem_at(page, add_btn, sel.index)
    result["inventory"] = inv.to_dict()
    return result


async def wait_upload_menuitem(page: Page, *, timeout_ms: int = 4_000) -> Locator | None:
    """Idle-on-progress wait, then return a locator for the selected item."""
    inv = await wait_menu_idle(page, None, timeout_ms=timeout_ms)
    sel = select_upload_item(inv)
    if sel.status not in ("found", "drift") or not sel.text:
        return None
    loc = page.get_by_role("menuitem", name=re.compile(re.escape(sel.text), re.I))
    if await loc.count():
        return loc.first
    loc = page.locator("[role='menuitem']").filter(has_text=re.compile(re.escape(sel.text), re.I))
    return loc.first if await loc.count() else None
