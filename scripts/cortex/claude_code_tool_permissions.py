"""Blanket tool-permission repair for claude.ai Customize connectors.

``set_claude_tool_permissions`` calls this module for policy selection and
blanket-group clicks. ``toys`` (/mcp/life) sets **Other tools** → Always allow.
``ulg-code`` (/mcp/code) sets **Read-only tools** and **Other tools** → Always
allow. Playwright ``locator.click`` is required — DOM ``element.click()`` does
not persist after reload.
"""

from __future__ import annotations

from playwright.async_api import Page

LIFE_BLANKET_GROUPS = ("Other tools",)
CODE_BLANKET_GROUPS = ("Read-only tools", "Other tools")
ALLOWED_POLICY = "Always allow"
NO_TOOLS_MESSAGE = "This connector has no tools available."


def resolve_policy(mcp_url: str, connector_name: str, explicit: str | None) -> str:
    """Select life-blanket or code-blanket without mixing the two connectors."""
    if explicit:
        legacy = {"code-ops": "code-blanket"}
        return legacy.get(explicit, explicit)
    path = mcp_url.split("?", 1)[0].rstrip("/")
    code = path.endswith("/mcp/code") or connector_name == "ulg-code"
    life = path.endswith("/mcp/life") or connector_name == "toys"
    if code and life:
        raise RuntimeError(
            f"Connector identity mixes life and code: {connector_name} {mcp_url}"
        )
    if code:
        return "code-blanket"
    if life:
        return "life-blanket"
    raise RuntimeError(
        "No tool policy for this connector. Pass --policy life-blanket or "
        "code-blanket."
    )


def blanket_groups_for_policy(policy: str) -> tuple[str, ...]:
    if policy == "life-blanket":
        return LIFE_BLANKET_GROUPS
    if policy == "code-blanket":
        return CODE_BLANKET_GROUPS
    raise RuntimeError(f"Unknown blanket policy {policy!r}")


async def _set_blanket_group(page: Page, permission_group: str) -> bool:
    """Set one blanket group to Always allow. Return True when a click was needed."""
    group_heading = page.get_by_text(permission_group, exact=True)
    if await group_heading.count() != 1:
        raise RuntimeError(
            f"Expected exactly one {permission_group!r} permission group"
        )

    group_row = group_heading.locator("xpath=../..")
    policy_button = group_row.get_by_role(
        "button", name="Blanket permission for group"
    )
    if await policy_button.count() != 1:
        raise RuntimeError(
            f"{permission_group!r} blanket permission control not found"
        )

    current = (await policy_button.inner_text()).strip()
    if ALLOWED_POLICY in current:
        return False

    await policy_button.click(force=True)
    await page.wait_for_timeout(300)
    menu = page.locator('[role="menu"][data-open]')
    if await menu.count() != 1:
        raise RuntimeError(f"{permission_group!r} permission menu did not open")

    allowed = menu.get_by_role("menuitemradio").filter(has_text=ALLOWED_POLICY)
    if await allowed.count() != 1:
        raise RuntimeError("Always allow option is not uniquely identifiable")
    await allowed.first.click(force=True)
    await page.wait_for_timeout(700)
    return True


async def _verify_blanket_groups(page: Page, groups: tuple[str, ...]) -> None:
    await page.reload(wait_until="domcontentloaded")
    for permission_group in groups:
        await page.get_by_text(permission_group, exact=True).wait_for(
            state="visible"
        )
        group_heading = page.get_by_text(permission_group, exact=True)
        group_row = group_heading.locator("xpath=../..")
        policy_button = group_row.get_by_role(
            "button", name="Blanket permission for group"
        )
        current = (await policy_button.inner_text()).strip()
        if ALLOWED_POLICY not in current:
            raise RuntimeError(
                f"{permission_group!r} permission did not persist: {current!r}"
            )
    if NO_TOOLS_MESSAGE in await page.locator("body").inner_text():
        raise RuntimeError("Claude still reports that the connector has no tools")


async def apply_blanket_policy(
    page: Page,
    *,
    groups: tuple[str, ...],
    policy_label: str,
    reopen,
) -> str:
    """Apply blanket Always allow for each group, reload-verify, return status."""
    changed = False
    for permission_group in groups:
        if await _set_blanket_group(page, permission_group):
            changed = True
    try:
        await _verify_blanket_groups(page, groups)
    except Exception:
        page = await reopen()
        await _verify_blanket_groups(page, groups)
    status = "changed" if changed else "already_set"
    return f"{status}\n{policy_label} groups={','.join(groups)}"


async def apply_life_blanket(page: Page, *, reopen) -> str:
    return await apply_blanket_policy(
        page,
        groups=LIFE_BLANKET_GROUPS,
        policy_label="life-blanket",
        reopen=reopen,
    )


async def apply_code_blanket(page: Page, *, reopen) -> str:
    return await apply_blanket_policy(
        page,
        groups=CODE_BLANKET_GROUPS,
        policy_label="code-blanket",
        reopen=reopen,
    )
