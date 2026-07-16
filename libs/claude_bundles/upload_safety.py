"""CLI safety gates for claude.ai Skills upload.

Fleet-wide ``--all --replace`` is never a default — require an explicit
``--force-replace-all`` confirmation flag.
"""

from __future__ import annotations

from collections.abc import Callable


def reject_unsafe_replace_all(
    *,
    all_: bool,
    replace: bool,
    force: bool,
    error: Callable[[str], None],
) -> None:
    """Refuse fleet-wide re-upload unless explicitly forced.

    ``--all`` alone still means upload *new* catalog targets only
    (``skip_existing=True``). Pairing ``--all`` with ``--replace`` is the
    blast-radius footgun — never a default; require ``--force-replace-all``.
    """
    if all_ and replace and not force:
        error(
            "--all --replace is refused by default (fleet-wide re-upload). "
            "Prefer: --slugs SLUG[,SLUG…] --replace. "
            "To override intentionally: --all --replace --force-replace-all"
        )
