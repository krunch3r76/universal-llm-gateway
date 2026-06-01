"""
Model discovery module for inference_djinn.

Scans directories for models not yet in the catalog.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class ModelFormat(StrEnum):
    """Supported model formats."""

    GGUF = "gguf"
    HF = "hf"
    AWQ = "awq"
    GPTQ = "gptq"
    EXL3 = "exl3"
    WHISPER = "whisper"
    UNKNOWN = "unknown"


@dataclass
class DiscoveredModel:
    """A discovered model file or directory."""

    path: Path
    format: ModelFormat
    filename: str
    size_bytes: int
    model_id: str = ""
    is_directory: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.model_id:
            self.model_id = self._generate_model_id()

    def _generate_model_id(self) -> str:
        """Generate normalized model_id from filename."""
        name = self.path.stem if self.path.is_file() else self.path.name
        model_id = name.lower().replace(".", "-").replace("_", "-")
        while "--" in model_id:
            model_id = model_id.replace("--", "-")
        return model_id


class ModelDiscovery:
    """Discover models in a directory that are not in the catalog."""

    def __init__(self, catalog_path: Path | None = None):
        """
        Initialize discovery with optional catalog for filtering.

        Args:
            catalog_path: Path to model_catalog.yaml for filtering known models
        """
        self.catalog_models: set[str] = set()
        if catalog_path and catalog_path.exists():
            self._load_catalog(catalog_path)

    def _load_catalog(self, catalog_path: Path) -> None:
        """Load model IDs from catalog."""
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f) or {}
        self.catalog_models = set(catalog.get("models", {}).keys())

    def scan_directory(
        self,
        path: Path,
        recursive: bool = True,
        include_cataloged: bool = False,
    ) -> list[DiscoveredModel]:
        """
        Scan directory for models not in catalog.

        Args:
            path: Directory to scan
            recursive: Whether to scan subdirectories
            include_cataloged: Include models already in catalog

        Returns:
            List of discovered models
        """
        path = Path(path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {path}")

        discovered: list[DiscoveredModel] = []

        # Scan for GGUF files
        pattern = "**/*.gguf" if recursive else "*.gguf"
        for gguf_path in path.glob(pattern):
            model = self._process_gguf(gguf_path)
            if model and (
                include_cataloged or model.model_id not in self.catalog_models
            ):
                discovered.append(model)

        # Scan for vLLM directories (containing config.json)
        # For non-recursive: check current directory only (consistent with GGUF *.gguf)
        # For recursive: check all subdirectories
        vllm_pattern = "**/config.json" if recursive else "config.json"
        for config_path in path.glob(vllm_pattern):
            model_dir = config_path.parent
            # Skip if parent is a GGUF or already processed
            if any(model_dir == d.path for d in discovered):
                continue

            model = self._process_directory(model_dir)
            if model and (
                include_cataloged or model.model_id not in self.catalog_models
            ):
                discovered.append(model)

        return sorted(discovered, key=lambda m: m.filename)

    def _process_gguf(self, path: Path) -> DiscoveredModel | None:
        """Process a GGUF file."""
        try:
            stat = path.stat()
            return DiscoveredModel(
                path=path,
                format=ModelFormat.GGUF,
                filename=path.name,
                size_bytes=stat.st_size,
                is_directory=False,
            )
        except OSError:
            return None

    def _process_directory(self, path: Path) -> DiscoveredModel | None:
        """Process a model directory."""
        format_type = self.identify_format(path)
        if format_type == ModelFormat.UNKNOWN:
            return None

        try:
            # Calculate total size
            total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            return DiscoveredModel(
                path=path,
                format=format_type,
                filename=path.name,
                size_bytes=total_size,
                is_directory=True,
            )
        except OSError:
            return None

    def identify_format(self, path: Path) -> ModelFormat:
        """
        Detect format: gguf, hf, awq, gptq, exl3.

        Args:
            path: Path to model file or directory

        Returns:
            Detected model format
        """
        path = Path(path)

        # Single GGUF file
        if path.is_file() and path.suffix.lower() == ".gguf":
            return ModelFormat.GGUF

        # Directory-based formats
        if not path.is_dir():
            return ModelFormat.UNKNOWN

        # Check for EXL3 files
        if list(path.glob("*.exl3")):
            return ModelFormat.EXL3

        # Check config.json for quantization info
        config_path = path / "config.json"
        if config_path.exists():
            import json

            try:
                with open(config_path) as f:
                    config = json.load(f)

                quant_config = config.get("quantization_config", {})
                quant_method = quant_config.get("quant_method", "").lower()

                if quant_method == "awq":
                    return ModelFormat.AWQ
                if quant_method == "gptq":
                    return ModelFormat.GPTQ
            except (json.JSONDecodeError, OSError):
                pass

        # Check quant_config.json (AWQ specific)
        quant_config_path = path / "quant_config.json"
        if quant_config_path.exists():
            import json

            try:
                with open(quant_config_path) as f:
                    config = json.load(f)
                if config.get("quant_method", "").lower() == "awq":
                    return ModelFormat.AWQ
                if "w_bit" in config and "q_group_size" in config:
                    return ModelFormat.AWQ
            except (json.JSONDecodeError, OSError):
                pass

        # Check quantize_config.json (GPTQ specific)
        gptq_config_path = path / "quantize_config.json"
        if gptq_config_path.exists():
            import json

            try:
                with open(gptq_config_path) as f:
                    config = json.load(f)
                if config.get("quant_method") == "gptq":
                    return ModelFormat.GPTQ
                if "bits" in config:
                    return ModelFormat.GPTQ
            except (json.JSONDecodeError, OSError):
                pass

        # Standard HF model (has config.json and weights)
        if config_path.exists():
            has_weights = (
                list(path.glob("*.safetensors"))
                or list(path.glob("*.bin"))
                or list(path.glob("*.pt"))
            )
            if has_weights:
                return ModelFormat.HF

        return ModelFormat.UNKNOWN

    def scan_single(self, path: Path) -> DiscoveredModel | None:
        """
        Discover a single model file or directory.

        Args:
            path: Path to model file or directory

        Returns:
            DiscoveredModel or None if not a valid model
        """
        path = Path(path).expanduser().resolve()

        if path.is_file() and path.suffix.lower() == ".gguf":
            return self._process_gguf(path)

        if path.is_dir():
            return self._process_directory(path)

        return None
