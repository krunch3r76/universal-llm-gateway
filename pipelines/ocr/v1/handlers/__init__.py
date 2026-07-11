"""ocr v1 handlers — register list + per-file extract steps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ocr_extract import OcrExtractV1Handler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "ocr",
        "ocr_extract_v1",
        OcrExtractV1Handler,
    )
