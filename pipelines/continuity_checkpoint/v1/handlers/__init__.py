"""continuity_checkpoint v1 handlers — resolve, seal, tape, pre_consolidate, post."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from .post import ContinuityCheckpointPostHandler
from .pre_consolidate import ContinuityCheckpointPreConsolidateHandler
from .resolve import ContinuityCheckpointResolveHandler
from .seal import ContinuityCheckpointSealHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def _tape_handler_class():
    tape_init = (
        Path(__file__).resolve().parents[3]
        / "continuity_tape_read"
        / "v1"
        / "handlers"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("_continuity_tape_handlers", tape_init)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load tape handlers from {tape_init}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ContinuityTapeReadHandler


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "continuity_checkpoint",
        "continuity_checkpoint_resolve_v1",
        ContinuityCheckpointResolveHandler,
    )
    router.register_domain_handler_class(
        "continuity_checkpoint",
        "continuity_checkpoint_seal_v1",
        ContinuityCheckpointSealHandler,
    )
    router.register_domain_handler_class(
        "continuity_checkpoint",
        "continuity_checkpoint_pre_consolidate_v1",
        ContinuityCheckpointPreConsolidateHandler,
    )
    router.register_domain_handler_class(
        "continuity_checkpoint",
        "continuity_checkpoint_post_v1",
        ContinuityCheckpointPostHandler,
    )
    router.register_domain_handler_class(
        "continuity_checkpoint",
        "continuity_tape_read_v1",
        _tape_handler_class(),
    )


__all__ = [
    "ContinuityCheckpointPostHandler",
    "ContinuityCheckpointPreConsolidateHandler",
    "ContinuityCheckpointResolveHandler",
    "ContinuityCheckpointSealHandler",
    "register_handlers",
]
