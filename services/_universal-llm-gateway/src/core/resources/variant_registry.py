"""Registry mapping physical worker processes to their model ID variants.

A single llama-server process serves both ``model-8192`` and
``model-8192-hybrid``.  ProcessState (supervisor, socket, PID) is keyed by
process_key (strips -hybrid).  ResourceTracker state machines and
ModelResourceInfo are keyed by tracking_key (preserves -hybrid).

This registry bridges the two key spaces so the unloader can check whether
*any* variant still has in-flight inference before tearing down the shared
process.
"""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)


class VariantRegistry:
    """Bidirectional mapping between process keys and tracking keys.

    Thread safety: not needed — single-threaded async event loop.
    """

    def __init__(self) -> None:
        self._process_to_variants: dict[str, set[str]] = {}
        self._variant_to_process: dict[str, str] = {}

    def register(self, process_key: str, tracking_key: str) -> None:
        """Register a variant tracking_key under its physical process_key."""
        self._process_to_variants.setdefault(process_key, set()).add(tracking_key)
        self._variant_to_process[tracking_key] = process_key
        if process_key != tracking_key:
            logger.debug(
                "Variant registered: %s → process %s", tracking_key, process_key
            )

    def unregister(self, tracking_key: str) -> None:
        """Remove a variant. Cleans up the process entry when the last variant leaves."""
        pkey = self._variant_to_process.pop(tracking_key, None)
        if pkey is not None:
            variants = self._process_to_variants.get(pkey)
            if variants:
                variants.discard(tracking_key)
                if not variants:
                    del self._process_to_variants[pkey]

    def get_variants(self, process_key: str) -> frozenset[str]:
        """Return all tracking_keys sharing a physical process."""
        return frozenset(self._process_to_variants.get(process_key, ()))

    def get_process_key(self, tracking_key: str) -> str | None:
        """Return the process_key for a variant, or None if not registered."""
        return self._variant_to_process.get(tracking_key)

    def has_variant(self, tracking_key: str) -> bool:
        return tracking_key in self._variant_to_process

    def is_process_in_use(
        self,
        process_key: str,
        state_machines: dict,
        busy_states: frozenset,
    ) -> bool:
        """True if any variant on this process has an in-flight operation.

        Args:
            process_key: The physical process key to check.
            state_machines: The ResourceTracker._state_machines dict.
            busy_states: WorkerState values considered "in use".
        """
        for vkey in self._process_to_variants.get(process_key, ()):
            sm = state_machines.get(vkey)
            if sm is not None and sm.current_state in busy_states:
                return True
        return False

    def describe_busy_variants(
        self,
        process_key: str,
        state_machines: dict,
        busy_states: frozenset,
    ) -> list[str]:
        """Return human-readable descriptions of busy variants for logging."""
        busy: list[str] = []
        for vkey in self._process_to_variants.get(process_key, ()):
            sm = state_machines.get(vkey)
            if sm is not None and sm.current_state in busy_states:
                busy.append(f"{vkey}={sm.current_state.value}")
        return busy
