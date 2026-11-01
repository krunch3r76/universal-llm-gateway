"""Recover induction slug lists when Context rail scrape refuses at submit time.

Callers at the post-submit induction boundary use this instead of aborting
harvest when Cowork has no Context header yet. ``project_ask.send_prompt`` is
the primary consumer after ``wait_for_induction_panel`` raises.
"""

from __future__ import annotations

from collections.abc import Sequence


def recover_induction_slugs_on_rail_error(
    exc: BaseException,
    *,
    claude_surface: str | None,
    attached: Sequence[str] | None,
    induction_slugs: Sequence[str],
) -> list[str] | None:
    """Return slug list to trust when rail scrape fails, or None to propagate.

    On ``claude_surface`` code, delegates to ``project_ask._code_rail_induction_fallback``
    (not-on-the-rail, still-collapsed, and no-toggle are all recoverable there).

    On every other surface, including ``None`` (Cowork), only the absent-header
    refusal is recoverable: ``ChatContextSkillsError`` whose message contains
    ``not on the rail``. A present but collapsed header must still refuse scrape
    (still-collapsed / no-toggle sentences), so those are not matched here.
    Bare Cowork ``/new`` with no Context header must not abort harvest the way
    an unrecoverable disclosure failure would.

    Does not sleep, poll, or touch Playwright.
    """
    from claude_bundles.chat_context_skills import ChatContextSkillsError

    surface = (claude_surface or "").strip().lower()
    if surface == "code":
        from claude_bundles.project_ask import _code_rail_induction_fallback

        if _code_rail_induction_fallback(exc):
            return list(attached or induction_slugs)
        return None

    if isinstance(exc, ChatContextSkillsError) and "not on the rail" in str(exc):
        return list(attached or induction_slugs)
    return None
