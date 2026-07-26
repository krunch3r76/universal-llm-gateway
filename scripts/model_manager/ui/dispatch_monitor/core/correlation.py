"""CorrelationIndex -- the only place the three folds learn about each other.

Each fold owns one family and stays ignorant of the rest. The index is the thin
join table that lets ``derive`` answer cross-family questions -- "is this root's
worker leg already terminal?", "which root does this CDP leg belong to?" --
without any fold reaching into another's state.

Correlation is **evidence-only**. Every edge here was asserted by some event
payload or envelope subject. The index never guesses: no timestamp proximity, no
name similarity, no single-in-flight-so-it-must-be-that-one inference. An
unlinked row renders unlinked, which is honest and locally fixable at the
emitter, whereas a wrong link is invisible and corrupts every downstream panel.
"""

from __future__ import annotations


class CorrelationIndex:
    """Bidirectional evidence-backed links among roots, threads and legs."""

    def __init__(self) -> None:
        self._root_by_worker_thread: dict[str, str] = {}
        self._root_by_dispatch: dict[str, str] = {}
        self._dispatches_by_root: dict[str, list[str]] = {}
        self._root_by_cdp: dict[str, str] = {}
        self._cdp_by_root: dict[str, list[str]] = {}
        self._thread_by_dispatch: dict[str, str] = {}

    # --- writes -----------------------------------------------------------
    def link_root_worker_thread(self, root_id: str, worker_thread: str) -> None:
        """Record that ``worker_thread`` carries work admitted for ``root_id``."""
        if root_id and worker_thread:
            self._root_by_worker_thread[str(worker_thread)] = str(root_id)

    def link_dispatch(
        self, dispatch_id: str, root_id: str | None, thread_id: str | None
    ) -> None:
        """Record a dispatch's root and/or thread, whichever the payload supplied.

        Called on every SDK record, so a link that only appears on the terminal
        event still lands. Existing links are not overwritten by a later ``None``.
        """
        if not dispatch_id:
            return
        dispatch_id = str(dispatch_id)
        if thread_id:
            self._thread_by_dispatch[dispatch_id] = str(thread_id)
        resolved = str(root_id) if root_id else self.root_for_thread(thread_id)
        if resolved:
            self._root_by_dispatch[dispatch_id] = resolved
            bucket = self._dispatches_by_root.setdefault(resolved, [])
            if dispatch_id not in bucket:
                bucket.append(dispatch_id)

    def link_cdp_leg(self, execution_id: str, root_id: str | None) -> None:
        """Record which root a CDP leg belongs to, when the payload names one."""
        if not execution_id or not root_id:
            return
        execution_id, root_id = str(execution_id), str(root_id)
        self._root_by_cdp[execution_id] = root_id
        bucket = self._cdp_by_root.setdefault(root_id, [])
        if execution_id not in bucket:
            bucket.append(execution_id)

    # --- reads ------------------------------------------------------------
    def root_for_thread(self, thread_id: str | None) -> str | None:
        """Return the root that admitted ``thread_id``, or ``None`` if unknown."""
        if not thread_id:
            return None
        return self._root_by_worker_thread.get(str(thread_id))

    def root_for_dispatch(self, dispatch_id: str) -> str | None:
        """Return the root behind ``dispatch_id``, resolving via thread if needed."""
        dispatch_id = str(dispatch_id)
        direct = self._root_by_dispatch.get(dispatch_id)
        if direct:
            return direct
        return self.root_for_thread(self._thread_by_dispatch.get(dispatch_id))

    def root_for_cdp(self, execution_id: str) -> str | None:
        """Return the root behind CDP leg ``execution_id``, if one was asserted."""
        return self._root_by_cdp.get(str(execution_id))

    def dispatches_for_root(self, root_id: str) -> tuple[str, ...]:
        """Return every dispatch id linked to ``root_id``, in first-seen order."""
        return tuple(self._dispatches_by_root.get(str(root_id), ()))

    def cdp_legs_for_root(self, root_id: str) -> tuple[str, ...]:
        """Return every CDP execution id linked to ``root_id``, in first-seen order."""
        return tuple(self._cdp_by_root.get(str(root_id), ()))

    def thread_for_dispatch(self, dispatch_id: str) -> str | None:
        """Return the bus thread a dispatch reported, or ``None``."""
        return self._thread_by_dispatch.get(str(dispatch_id))
