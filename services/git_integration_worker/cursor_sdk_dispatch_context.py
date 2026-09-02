"""Per-dispatch tree and identity context for the cursor-sdk admit→sync seam.

One carrier threaded from the two producers (HTTP admit, queue promote) into
``_run_sdk_dispatch_gated`` and ``_run_sdk_sync``. It replaces the scattered
``source_repo`` / ``binding`` / ``dispatch_workspace`` / ``contract`` params,
which had drifted into three different things all named ``source_repo``.

``CaptureBinding`` remains the sole authority for *which tree*: this record
derives the write surface from it rather than storing a second copy, so a
producer cannot construct a context whose workspace root disagrees with the
binding that will account for the dispatch's effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding


@dataclass(frozen=True, slots=True, kw_only=True)
class SdkDispatchContext:
    """Tree + identity context for one cursor-sdk dispatch.

    Attributes:
        dispatch_id: Ledger dispatch identifier.
        thread_id: agent-bus thread carrying this dispatch.
        handoff_contract: Contract resolved once at admit/promote — never raw
            ``req.handoff_contract``. Gates baseline capture, the MCP env
            filter, and the effects manifest.
        hub: ``cfg.source_repo`` as configured (unresolved). The MCP bridge and
            IDE-parity anchor; also the base for ``packet_path`` resolution,
            effects-manifest relativization, the live-run registry, and the
            closeout receipt tree. Never the write surface.
        dispatch_workspace: ``local.cwd`` and ``launch_bridge(workspace=)``.
            Has its own authority (``resolve_admit_binding`` /
            ``resolve_promoted_workspace``) and is not derivable from the
            binding: for hub Lane-A it is the shared projects root.
        capture_binding: Sole authority for write/receipt trees. Mandatory.
    """

    dispatch_id: str
    thread_id: str
    handoff_contract: str
    hub: Path
    dispatch_workspace: Path
    capture_binding: CaptureBinding

    @property
    def workspace_root(self) -> Path:
        """Write surface for this dispatch — drives ``local.dirs`` policy."""
        return self.capture_binding.write_tree

    @property
    def lane(self) -> Literal["A", "B"]:
        """Capture lane: ``"A"`` shared checkout, ``"B"`` per-dispatch worktree."""
        return self.capture_binding.lane

    def __post_init__(self) -> None:
        """Fail construction when the hub is not the binding's receipt tree.

        Both lanes build ``receipt_tree`` from ``cfg.source_repo``; a mismatch
        means a producer passed a write-tree value where a hub value was meant
        — the exact drift class this carrier exists to prevent. Both sides are
        canonicalized because ``CaptureBinding`` is hand-constructible in tests.
        """
        if self.hub.resolve() != self.capture_binding.receipt_tree.resolve():
            raise ValueError(
                "SdkDispatchContext.hub must equal capture_binding.receipt_tree: "
                f"hub={self.hub} receipt_tree={self.capture_binding.receipt_tree}"
            )
