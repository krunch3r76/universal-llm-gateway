"""Read Customize → Skills user library after the table UI was removed.

The live panel is a Yours / Discover segmented control. Discover is the
default and has no user slugs. User skills are cards under the
``Created by you`` section, not ``table tbody tr``.
"""

from __future__ import annotations

import re

from playwright.async_api import Page

# A count badge ("55") matches a bare alnum slug. Require a letter.
_SLUG_RE = re.compile(r"^(?=.*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*$")

_SELECT_YOURS_JS = """
() => {
  const yours = [...document.querySelectorAll('[role="radio"]')].find(
    (el) => (el.innerText || '').trim() === 'Yours'
  );
  if (!yours) return 'absent';
  if (yours.getAttribute('aria-checked') === 'true') return 'checked';
  yours.click();
  return 'clicked';
}
"""

_CARD_LABELS_JS = """
() => {
  const sections = [...document.querySelectorAll('section')];
  const created = sections.find((section) => {
    const heading = section.querySelector('h3');
    return heading && /^created by you$/i.test((heading.innerText || '').trim());
  });
  if (!created) return null;
  // Card title only. A broader span walk also matches filter chips
  // (new, yesterday, engineering) that are not library slugs.
  return [...created.querySelectorAll('span.truncate.text-body-medium.text-primary')]
    .map((el) => (el.innerText || '').trim());
}
"""


def slugs_from_card_labels(labels: list[str]) -> set[str]:
    """Keep slug-shaped card titles; drop the section count badge."""
    out: set[str] = set()
    for raw in labels:
        name = raw.strip().split("\n")[0].strip().lower()
        if _SLUG_RE.fullmatch(name):
            out.add(name)
    return out


async def yours_created_slugs(page: Page) -> set[str] | None:
    """Slugs under Created by you, or None only when the Yours radio is absent.

    None lets the caller fall back to the legacy HTML table. An empty set
    means the section is present and lists no user skills. Once Yours is
    selected, a missing section raises: the table fallback would report
    ``on_ui=0`` on a page that has no table.
    """
    state = await page.evaluate(_SELECT_YOURS_JS)
    if state == "absent":
        return None
    if state not in {"clicked", "checked"}:
        raise RuntimeError(f"unexpected Yours radio state: {state!r}")
    if state == "clicked":
        await page.get_by_role("heading", name="Created by you").wait_for(
            state="visible", timeout=3_000
        )
    labels = await page.evaluate(_CARD_LABELS_JS)
    if labels is None:
        raise RuntimeError(
            "Yours is selected but the Created by you section is missing"
        )
    return slugs_from_card_labels(labels)
