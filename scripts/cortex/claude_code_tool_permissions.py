"""ulg-code tool allowlist for the claude.ai Customize connector detail.

``set_claude_tool_permissions`` calls this when the connector is ``/mcp/code``
or named ``ulg-code``. Manage Services, Observability, and Team Dispatch stay
on Always allow. Every other tool radio is Blocked. The Yours list and the
detail page can carry a second radiogroup with the same accessible name, so
the click targets the visible ``aria-label`` radio. A DOM ``element.click()``
does not survive reload.
"""

from __future__ import annotations

from playwright.async_api import Page

ALLOWED_POLICY = "Always allow"
BLOCKED_POLICY = "Blocked"
# Claude titles for manage, observability, and team_dispatch.
CODE_OPS_ALLOW = frozenset({
    "Manage Services",
    "Observability",
    "Team Dispatch",
})

_PERMISSION_SNAPSHOT_JS = """() => {
  const legend = [...document.querySelectorAll('legend')].find(
    (el) => (el.textContent || '').trim() === 'Tool permissions'
  );
  if (!legend) return null;
  const section = legend.closest('section');
  if (!section) return null;
  return [...section.querySelectorAll('[role="radiogroup"]')].map((group) => {
    const radios = [...group.querySelectorAll('[role="radio"]')];
    const checked = radios.find(
      (radio) => radio.getAttribute('aria-checked') === 'true'
    );
    return {
      name: (group.getAttribute('aria-label') || '').trim(),
      selected: checked ? (checked.getAttribute('aria-label') || '').trim() : '',
      labels: radios.map(
        (radio) => (radio.getAttribute('aria-label') || '').trim()
      ),
    };
  });
}"""


def resolve_policy(mcp_url: str, connector_name: str, explicit: str | None) -> str:
    """Select life-blanket or code-ops without mixing the two connectors.

    An explicit policy wins. Otherwise ``/mcp/code`` and the name ``ulg-code``
    select this allowlist, and ``/mcp/life`` and ``toys`` select the life
    blanket. A URL and a name that point at different surfaces fail closed.
    """
    if explicit:
        return explicit
    path = mcp_url.split("?", 1)[0].rstrip("/")
    code = path.endswith("/mcp/code") or connector_name == "ulg-code"
    life = path.endswith("/mcp/life") or connector_name == "toys"
    if code and life:
        raise RuntimeError(
            f"Connector identity mixes life and code: {connector_name} {mcp_url}"
        )
    if code:
        return "code-ops"
    if life:
        return "life-blanket"
    raise RuntimeError(
        "No tool policy for this connector. Pass --policy life-blanket or code-ops."
    )


def _desired(name: str) -> str:
    """Return Always allow for the three ops tools and Blocked for the rest."""
    if name in CODE_OPS_ALLOW:
        return ALLOWED_POLICY
    return BLOCKED_POLICY


async def _snapshot(page: Page) -> list[dict[str, str]]:
    """Read tool radios under the Tool permissions legend."""
    raw = await page.evaluate(_PERMISSION_SNAPSHOT_JS)
    if raw is None:
        raise RuntimeError(
            "Tool permissions section not found on the connector detail"
        )
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for row in raw:
        name = str(row.get("name") or "").strip()
        if not name:
            raise RuntimeError("Tool permission radiogroup is missing aria-label")
        if name in seen:
            raise RuntimeError(f"Duplicate tool permission row {name!r}")
        seen.add(name)
        labels = [str(item) for item in row.get("labels") or []]
        if ALLOWED_POLICY not in labels or BLOCKED_POLICY not in labels:
            raise RuntimeError(
                f"Tool row {name!r} is missing Always allow / Blocked"
            )
        rows.append({"name": name, "selected": str(row.get("selected") or "")})
    missing = sorted(CODE_OPS_ALLOW - {row["name"] for row in rows})
    if missing:
        raise RuntimeError(
            "Required ulg-code tools are not on the connector: " + ", ".join(missing)
        )
    return rows


async def _click_visible(page: Page, name: str, target: str) -> None:
    """Click the visible radio. A hidden duplicate is not the saved control."""
    radios = page.locator(
        f'[role="radiogroup"][aria-label="{name}"] '
        f'[role="radio"][aria-label="{target}"]'
    )
    count = await radios.count()
    chosen = None
    for index in range(count):
        item = radios.nth(index)
        if await item.is_visible():
            chosen = item
            break
    if chosen is None:
        raise RuntimeError(
            f"Radio {target!r} for {name!r} is not visible ({count} matches)"
        )
    await chosen.click(force=True)
    await page.wait_for_timeout(400)


async def apply_code_ops(
    page: Page,
    *,
    reopen,
) -> str:
    """Block every ulg-code tool except the three ops verbs, then reload-verify.

    ``reopen`` is an async callable used when reload closes the settings
    modal. The return value is ``changed`` or ``already_set`` plus
    ``code-ops allow=<n> blocked=<n>``.
    """
    changed = False
    for _pass in range(2):
        pending = [
            row for row in await _snapshot(page) if row["selected"] != _desired(row["name"])
        ]
        if not pending:
            break
        changed = True
        for row in pending:
            await _click_visible(page, row["name"], _desired(row["name"]))
    await page.reload(wait_until="domcontentloaded")
    try:
        await page.get_by_text("Tool permissions", exact=True).wait_for(
            state="visible", timeout=5000
        )
    except Exception:
        page = await reopen()
    after = await _snapshot(page)
    mismatches = [
        f"{row['name']}={row['selected'] or 'unset'}"
        for row in after
        if row["selected"] != _desired(row["name"])
    ]
    if mismatches:
        raise RuntimeError("Permission did not persist: " + "; ".join(mismatches))
    allow_n = sum(1 for row in after if row["name"] in CODE_OPS_ALLOW)
    blocked_n = len(after) - allow_n
    status = "changed" if changed else "already_set"
    return f"{status}\ncode-ops allow={allow_n} blocked={blocked_n}"
