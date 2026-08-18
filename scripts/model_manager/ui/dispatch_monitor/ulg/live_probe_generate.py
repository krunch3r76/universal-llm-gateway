"""Kwargs helper for live board falsifier / dual-stamp probe generates.

Every live ``team_dispatch(op=generate, seat=cursor-sdk, …)`` probe MUST opt out
of auto-review — otherwise a spawned gpt-5.5 review child can survive Stargate
restart and paint as an idle board ghost (6164 dual-stamp incident).
"""

from __future__ import annotations

from typing import Any


def live_probe_generate_kwargs(**overrides: Any) -> dict[str, Any]:
    """Kwargs for ``team_dispatch(op=generate, seat=cursor-sdk, …)`` live board probes.

    Always forces ``auto_review_child=False``. Defaults ``lane="A"`` (bind-only
    probe). Callers may override other keys but not the auto-review flag —
    ``False`` is applied last and cannot be overridden.

    Example::

        team_dispatch(
            op="generate",
            seat="cursor-sdk",
            contract="light-bounded",
            dispatch_thread_id=thread_id,
            prompt=prompt,
            **live_probe_generate_kwargs(),  # includes lane="A"
        )
    """
    kwargs = {"lane": "A"}
    kwargs.update(overrides)
    kwargs["auto_review_child"] = False
    return kwargs
