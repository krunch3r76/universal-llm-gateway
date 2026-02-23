"""Catalog state - reads static + local model catalogs."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DOMAINS = ("text_llm", "audio", "translation", "graphics", "visual", "embedding")


@dataclass(slots=True, kw_only=True)
class ModelInfo:
    """Unified model info from catalog YAML files."""

    model_id: str
    domain: str
    engine: str
    name: str
    format: str
    schema: str
    parameters_m: int = 0
    quant: str = ""
    training_context_length: int = 0
    activated_gpu_contexts: list[int] = field(default_factory=list)
    activated_cpu_contexts: list[int] = field(default_factory=list)
    hf_repo: str = ""
    hf_file: str | None = None
    hf_mmproj_file: str | None = None
    hf_local_subdir: str | None = None
    size_bytes: int = 0
    has_gpu_profiles: bool = False
    has_cpu_profiles: bool = False
    has_hybrid_profiles: bool = False
    is_vision_model: bool = False
    is_embedding: bool = False
    source_path: Path | None = None
    # True when this entry came from the local catalog (has full operational data)
    is_local: bool = False

    @property
    def display_name(self) -> str:
        return self.name or self.model_id

    @property
    def size_display(self) -> str:
        if self.size_bytes <= 0:
            return "—"
        gb = self.size_bytes / (1024**3)
        if gb >= 1.0:
            return f"{gb:.1f} GB"
        mb = self.size_bytes / (1024**2)
        return f"{mb:.0f} MB"


class CatalogState:
    """
    Discovers models from static and local catalog directories.

    Scan order: static first, then local. Local entries override static
    (same model_id), mirroring the CatalogLoader merge strategy.

    UI-agnostic — does not require Gateway infrastructure.
    """

    def __init__(
        self,
        static_catalog_dir: Path,
        local_catalog_dir: Path | None = None,
    ) -> None:
        self._static_dir = static_catalog_dir
        self._local_dir = local_catalog_dir
        self._models: dict[str, ModelInfo] = {}

    @property
    def models(self) -> dict[str, ModelInfo]:
        return self._models

    def refresh(self) -> None:
        """Rescan catalog directories (static → local, local overrides)."""
        self._models.clear()
        self._scan_directory(self._static_dir, is_local=False)
        if self._local_dir and self._local_dir.exists():
            self._scan_directory(self._local_dir, is_local=True)

    def get(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def list_by_domain(self, domain: str) -> list[ModelInfo]:
        return [m for m in self._models.values() if m.domain == domain]

    def list_by_engine(self, engine: str) -> list[ModelInfo]:
        return [m for m in self._models.values() if m.engine == engine]

    def get_domains(self) -> list[str]:
        return sorted({m.domain for m in self._models.values()})

    def get_engines(self) -> list[str]:
        return sorted({m.engine for m in self._models.values()})

    def _scan_directory(self, root: Path, *, is_local: bool) -> None:
        if not root.exists():
            return
        for yaml_file in sorted(root.rglob("*.yaml")):
            rel = yaml_file.relative_to(root)
            parts = rel.parts
            if len(parts) < 3:
                continue
            domain, engine = parts[0], parts[1]
            model_id = yaml_file.stem
            try:
                info = self._parse_model_file(
                    yaml_file, model_id, domain, engine, is_local=is_local
                )
                self._models[model_id] = info
            except Exception as e:
                logger.warning("Failed to parse %s: %s", yaml_file, e)

    def _parse_model_file(
        self,
        path: Path,
        model_id: str,
        domain: str,
        engine: str,
        *,
        is_local: bool,
    ) -> ModelInfo:
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}

        metadata = data.get("metadata", {})
        download = data.get("download", {})
        hf = download.get("huggingface", {})
        devices = data.get("devices", {})
        loader = data.get("loader", {})

        return ModelInfo(
            model_id=model_id,
            domain=domain,
            engine=engine,
            name=metadata.get("name", model_id),
            format=metadata.get("format", ""),
            schema=data.get("schema", engine),
            parameters_m=metadata.get("parameters_m", 0),
            quant=metadata.get("quant", ""),
            training_context_length=metadata.get("training_context_length", 0),
            activated_gpu_contexts=metadata.get("activated_gpu_contexts", []),
            activated_cpu_contexts=metadata.get("activated_cpu_contexts", []),
            hf_repo=hf.get("repo", ""),
            hf_file=hf.get("file"),
            hf_mmproj_file=hf.get("mmproj_file"),
            hf_local_subdir=hf.get("local_subdir"),
            size_bytes=download.get("size_bytes", 0),
            has_gpu_profiles=bool(devices.get("gpu", {}).get("profiles")),
            has_cpu_profiles=bool(devices.get("cpu", {}).get("profiles")),
            has_hybrid_profiles=bool(devices.get("hybrid", {}).get("profiles")),
            is_vision_model=bool(metadata.get("is_vision_model")),
            is_embedding=loader.get("embedding") is True,
            source_path=path,
            is_local=is_local,
        )
