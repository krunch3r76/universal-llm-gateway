"""
Catalog Entry Generator - V3 Static-Only Format.

Generates static catalog entries for the Universal LLM Gateway.

V3 Static Schema:
    - catalog_schema: 3
    - schema: Engine reference (llama-cpp, vllm, etc.)
    - metadata: Model info (format, family, etc.) - NO engine field
    - download: HuggingFace source info

Generated entries contain NO loader or devices sections.
Those are written to the local catalog (~/.gateway/catalog/) after measurement.

Usage:
    from inference_djinn.catalog.generator import generate_catalog_entry

    entry = generate_catalog_entry(model_path)
    # Returns V3 static format entry (metadata-only)
"""

from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .discovery import DiscoveredModel, ModelDiscovery, ModelFormat
from .extractor import CatalogMetadata, MetadataExtractor
from .tracer import HFSource, SourceTracer

# Import HfHubHTTPError if available (huggingface_hub is optional)
try:
    from huggingface_hub.errors import HfHubHTTPError
except ImportError:
    HfHubHTTPError = None  # type: ignore[misc, assignment]

logger = get_logger(__name__)

# Local mapping (no cross-domain import from Gateway)
FORMAT_TO_SCHEMA = {
    ModelFormat.GGUF: "llama-cpp",
    ModelFormat.HF: "vllm",
    ModelFormat.AWQ: "vllm",
    ModelFormat.GPTQ: "vllm",
    ModelFormat.EXL3: "exllamav3",
    ModelFormat.WHISPER: "faster-whisper",
}


class CatalogEntryGenerator:
    """Generate catalog entries from model files."""

    def __init__(
        self,
        discovery: ModelDiscovery | None = None,
        extractor: MetadataExtractor | None = None,
        tracer: SourceTracer | None = None,
    ):
        """
        Initialize generator with optional components.

        Args:
            discovery: Model discovery instance
            extractor: Metadata extractor instance
            tracer: Source tracer instance
        """
        self.discovery = discovery or ModelDiscovery()
        self.extractor = extractor or MetadataExtractor()
        self.tracer = tracer or SourceTracer()

    def generate(
        self,
        model: DiscoveredModel | Path,
        trace_source: bool = True,
        hf_repo: str | None = None,
        hf_file: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate complete catalog entry for a model.

        Args:
            model: DiscoveredModel or path to model
            trace_source: Whether to trace HuggingFace source
            hf_repo: Optional HuggingFace repo ID for verification
            hf_file: Optional HuggingFace filename (for GGUF)

        Returns:
            Complete catalog entry in model_catalog.yaml format
        """
        # Convert path to DiscoveredModel if needed
        if isinstance(model, Path):
            discovered = self.discovery.scan_single(model)
            if not discovered:
                raise ValueError(f"Could not identify model at: {model}")
            model = discovered

        # Extract metadata
        metadata = self.extractor.extract(model.path, model.format.value)

        # Trace HuggingFace source
        hf_source: HFSource | None = None
        if hf_repo:
            # Attempt to verify against specific repo
            try:
                hf_source = self.tracer.verify_against_repo(
                    model.path, hf_repo, hf_file or model.filename
                )
                if hf_source:
                    logger.info(f"✅ Verified {model.path} against {hf_repo}")
                else:
                    # verify_against_repo returned None = hash mismatch or file not found
                    # This is an irrecoverable error when --repo is explicitly specified
                    raise ValueError(
                        f"Hash mismatch: {model.path} does not match {hf_repo}. "
                        "File may be corrupted or from a different source."
                    )
            except ImportError as e:
                # huggingface_hub not installed - graceful fallback
                logger.warning(
                    f"⚠️ Could not verify {model.path} against {hf_repo} "
                    f"(huggingface_hub not installed: {e}). Recording unverified."
                )
                file_hint = model.filename if model.format.value == "gguf" else None
                hf_source = HFSource(
                    repo=hf_repo,
                    file=hf_file or file_hint,
                    size_bytes=model.size_bytes,
                    sha256=None,
                    verified=False,
                )
            except Exception as e:
                # Handle HfHubHTTPError (network/HTTP failures from huggingface_hub)
                # gracefully - record as unverified
                if HfHubHTTPError is not None and isinstance(e, HfHubHTTPError):
                    logger.warning(
                        f"⚠️ Could not verify {model.path} against {hf_repo} "
                        f"(network error: {e}). Recording unverified."
                    )
                    file_hint = model.filename if model.format.value == "gguf" else None
                    hf_source = HFSource(
                        repo=hf_repo,
                        file=hf_file or file_hint,
                        size_bytes=model.size_bytes,
                        sha256=None,
                        verified=False,
                    )
                else:
                    raise  # Re-raise unexpected exceptions
        elif trace_source:
            # Auto-trace source
            hf_source = self.tracer.trace_huggingface(
                model.path, model.format.value, model.filename
            )

        # Build catalog entry
        return self._build_entry(model, metadata, hf_source)

    def generate_batch(
        self,
        models: list[DiscoveredModel],
        trace_source: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """
        Generate catalog entries for multiple models.

        Args:
            models: List of discovered models
            trace_source: Whether to trace HuggingFace sources

        Returns:
            Dictionary of model_id -> catalog entry
        """
        results: dict[str, dict[str, Any]] = {}

        for model in models:
            try:
                entry = self.generate(model, trace_source=trace_source)
                results[model.model_id] = entry
            except Exception as e:
                logger.error(f"Failed to generate entry for {model.path}: {e}")

        return results

    def _get_schema_for_format(self, model_format: ModelFormat) -> str:
        """Get schema name for a model format."""
        return FORMAT_TO_SCHEMA.get(model_format, "llama-cpp")

    def _build_entry(
        self,
        model: DiscoveredModel,
        metadata: CatalogMetadata,
        hf_source: HFSource | None,
    ) -> dict[str, Any]:
        """Build static catalog entry in V3 format (metadata-only, no loader/devices)."""
        meta = metadata.to_catalog_metadata()

        # V3: engine derived from schema, not stored in metadata
        meta.pop("engine", None)

        entry: dict[str, Any] = {
            "catalog_schema": 3,
            "schema": self._get_schema_for_format(model.format),
            "metadata": meta,
            "download": self._build_download(model, hf_source),
        }

        return entry

    def _build_download(
        self,
        model: DiscoveredModel,
        hf_source: HFSource | None,
    ) -> dict[str, Any]:
        """Build download section."""
        if hf_source:
            return hf_source.to_download_section()

        # No HF source - provide placeholder
        result: dict[str, Any] = {
            "huggingface": {
                "repo": None,
            },
            "size_bytes": model.size_bytes,
        }

        if model.format == ModelFormat.GGUF:
            result["huggingface"]["file"] = None

        # Compute local SHA256 for GGUF files
        if model.format == ModelFormat.GGUF and model.path.is_file():
            try:
                sha256 = SourceTracer.compute_local_sha256(model.path)
                result["sha256"] = sha256
            except Exception as e:
                logger.debug(f"Could not compute SHA256: {e}")

        return result

    @classmethod
    def format_yaml(cls, entries: dict[str, dict[str, Any]]) -> str:
        """
        Format catalog entries as YAML string.

        Args:
            entries: Dictionary of model_id -> catalog entry

        Returns:
            YAML formatted string
        """
        import yaml

        class CustomDumper(yaml.SafeDumper):
            """Custom dumper for better YAML formatting."""

            pass

        # Flow style for simple profile dicts
        def represent_dict(dumper: yaml.SafeDumper, data: dict[str, Any]) -> yaml.Node:
            if set(data.keys()) <= {
                "n_gpu_layers",
                "ram_mb",
                "vram_mb",
                "max_model_len",
            }:
                return dumper.represent_mapping(
                    "tag:yaml.org,2002:map", data.items(), flow_style=True
                )
            return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

        # Literal block for multiline strings
        def represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
            if "\n" in data:
                return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
            return dumper.represent_scalar("tag:yaml.org,2002:str", data)

        CustomDumper.add_representer(dict, represent_dict)
        CustomDumper.add_representer(str, represent_str)

        return yaml.dump(
            entries,
            Dumper=CustomDumper,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=100,
        )


def generate_catalog_entry(
    model_path: Path,
    trace_source: bool = True,
    hf_repo: str | None = None,
    hf_file: str | None = None,
) -> dict[str, Any]:
    """
    Convenience function to generate a catalog entry.

    Args:
        model_path: Path to model file or directory
        trace_source: Whether to auto-trace HuggingFace source
        hf_repo: Optional HuggingFace repo ID
        hf_file: Optional HuggingFace filename

    Returns:
        Complete catalog entry dict
    """
    generator = CatalogEntryGenerator()
    return generator.generate(
        Path(model_path),
        trace_source=trace_source,
        hf_repo=hf_repo,
        hf_file=hf_file,
    )
