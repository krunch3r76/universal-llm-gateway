"""
Base metadata extraction classes and dataclasses.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def _default_capabilities() -> dict[str, Any]:
    """Default capabilities dict for catalog metadata."""
    return {
        "input_schema": "messages",
        "modalities": {"input": ["text"], "output": ["text"]},
        "interaction": {"chat_template": False},
        "reasoning": {"supports_thinking": False},
        "limits": {},
        "provenance": {},
    }


@dataclass
class CatalogMetadata:
    """Extracted metadata for catalog entry."""

    # Core identity
    name: str
    format: str
    family: str | None = None
    arch: str | None = None

    # Model specs
    quant: str | None = None
    parameters_m: int | None = None

    # Capabilities (structured dict)
    capabilities: dict[str, Any] = field(default_factory=_default_capabilities)

    # Extra data
    extra: dict[str, Any] = field(default_factory=dict)

    def to_catalog_metadata(self) -> dict[str, Any]:
        """Convert to catalog metadata section format."""
        result: dict[str, Any] = {
            "name": self.name,
            "format": self.format,
        }

        # Only include family/arch if explicitly extracted from metadata
        if self.family:
            result["family"] = self.family
        if self.arch:
            result["arch"] = self.arch
        if self.quant:
            result["quant"] = self.quant
        if self.parameters_m:
            result["parameters_m"] = self.parameters_m

        result["capabilities"] = self.capabilities

        return result


class MetadataExtractor:
    """Extract metadata from model files."""

    def extract(self, path: Path, format_type: str) -> CatalogMetadata:
        """
        Extract metadata from model based on format.

        Args:
            path: Path to model file or directory
            format_type: Format type (gguf, hf, awq, gptq, exl3)

        Returns:
            Extracted metadata
        """
        path = Path(path)

        if format_type == "gguf":
            from .gguf import extract_gguf

            return extract_gguf(path)
        elif format_type in ("hf", "awq", "gptq"):
            from .hf import extract_hf

            return extract_hf(path, format_type)
        elif format_type == "exl3":
            from .exl3 import extract_exl3

            return extract_exl3(path)
        else:
            return CatalogMetadata(
                name=path.stem if path.is_file() else path.name,
                format=format_type,
            )
