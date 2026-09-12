"""Arm-time contract for bus watchers: producer linkage is declared, never implied.

Why: on 2026-09-11 a watcher armed without ``--execution-id`` polled a thread for
six hours after the producer (a CDP generate) had already failed delivery — the
poller had no producer to project, so ``producer.state`` stayed ``unknown`` and no
stall-pop ever fired (friction a:33160). The failure was decided at arm time; the
check belongs there, not six hours later.
"""

from __future__ import annotations

NO_PRODUCER_HELP = (
    "declare that this thread has no dispatch producer to project "
    "(human/web reply watch); required when --execution-id is absent"
)


def require_producer_declaration(execution_id: str, *, no_producer: bool) -> str | None:
    """Return an error message when neither ``--execution-id`` nor ``--no-producer`` is given.

    Exactly one posture is admitted: a pinned producer (execution_id) whose death the
    poller can detect, or an explicit declaration that no producer exists. Silence is
    the mis-arm class this guards against.
    """
    has_execution = bool(execution_id.strip())
    if has_execution and no_producer:
        return "mis-arm: --execution-id and --no-producer are mutually exclusive"
    if not has_execution and not no_producer:
        return (
            "mis-arm: pass --execution-id <id> (from the team_dispatch payload) or "
            "--no-producer to declare a producer-less watch; an undeclared producer "
            "cannot stall-pop when the dispatch dies"
        )
    return None
