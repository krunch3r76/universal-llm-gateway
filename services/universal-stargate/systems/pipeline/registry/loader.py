"""
Pipeline loading logic for the registry subsystem.

Loads pipeline configurations, models, and prompts from the file system.
Traverses search paths, loads domains, and processes deferred pipelines.
Part of the pipeline registry package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml
from model_id import validate_model_id
from universal_logging import get_logger

from ..availability import missing_models
from ..core.schemas import PipelineSpec, StepConfig
from ..loader import resolve_sub_pipelines
from ..schemas import ModelRef

if TYPE_CHECKING:
    from pathlib import Path
    from .core import PipelineRegistry

logger = get_logger(__name__)


class PipelineLoader:
    """
    Loads pipeline configurations from the file system.

    Manages search path traversal, domain loading, model/prompt loading,
    and deferred pipeline retry.
    """

    def __init__(self, registry_instance: PipelineRegistry) -> None:
        self._registry = registry_instance

    def _load_from_search_path(self, search_path: Path, path_name: str) -> None:
        """Load all domains from a search path."""
        if not search_path.exists():
            logger.info(f"⚠️  Search path does not exist: {search_path}")
            return

        logger.info(f"📂 Loading from: {search_path}")

        for domain_dir in sorted(search_path.iterdir()):
            if (
                domain_dir.is_dir()
                and not domain_dir.name.startswith(".")
                and domain_dir.name != "__pycache__"
            ):
                self._load_domain(domain_dir, search_path, path_name)

    def _load_domain(self, domain_dir: Path, search_path: Path, path_name: str) -> None:
        """
        Load all components of a domain.

        Structure:
            {domain}/
                *.yaml              - Pipeline specs (root level)
                prompts.yaml        - Domain prompts (root level, namespaced by domain)
                models.yaml         - Domain model refs (scoped to this search path)
                handlers/           - Domain handlers (loaded separately)
                {subdir}/
                    prompts.yaml    - Subdomain prompts (namespaced as domain.subdir)
                    *.yaml          - Pipeline specs in subdirectory
        """
        domain_name = domain_dir.name
        logger.info(f"  📁 Loading domain: {domain_name}")

        models_file = domain_dir / "models.yaml"
        if models_file.exists():
            bucket = self._registry._domain_models.setdefault(path_name, {})
            old_count = len(bucket)
            self._load_models(models_file, domain_name, path_name)
            new_count = len(bucket) - old_count
            logger.info(
                f"    📄 Loaded {new_count} model ref(s) "
                f"from {path_name}/{domain_name}/models.yaml"
            )

        prompts_file = domain_dir / "prompts.yaml"
        if prompts_file.exists():
            self._load_domain_prompts(prompts_file, domain_name)

        for prompts_file in sorted(domain_dir.rglob("prompts.yaml")):
            if prompts_file.parent == domain_dir:
                continue

            relative_path = prompts_file.relative_to(search_path)
            namespace = ".".join(relative_path.parent.parts)

            self._load_domain_prompts(prompts_file, namespace)

        for yaml_file in sorted(domain_dir.rglob("*.yaml")):
            excluded = ("prompts.yaml", "models.yaml", "categories.yaml")
            if yaml_file.name not in excluded:
                self._load_pipeline(yaml_file, path_name, domain_dir=domain_dir)

    def _load_domain_prompts(self, prompts_file: Path, namespace: str) -> None:
        """Load prompts from domain prompts.yaml with namespace."""
        try:
            with prompts_file.open() as f:
                data = yaml.safe_load(f) or {}

            prompts_data = data.get("prompts", {})

            if prompts_data:
                namespace_parts = namespace.split(".")
                current = self._registry.prompts
                for part in namespace_parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

                current[namespace_parts[-1]] = prompts_data
                logger.info(
                    f"    📄 Loaded {len(prompts_data)} prompt group(s) "
                    f"from {namespace}/prompts.yaml"
                )
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Failed to load prompts from {prompts_file}: {e}")
        except Exception:
            logger.exception(f"Unexpected error loading prompts from {prompts_file}")
            raise

    def _load_root_models(self, search_path: Path, path_name: str) -> None:
        """
        Load root namespace models from search_path/models.yaml.

        Root models are stored with unqualified keys (e.g., "qwen"),
        scoped to the search path namespace.
        """
        from .validator import PipelineConfigError

        root_models_file = search_path / "models.yaml"
        if not root_models_file.exists():
            return

        try:
            with open(root_models_file) as f:
                data = yaml.safe_load(f) or {}

            models_data = data.get("models", {})
            if not models_data:
                return

            bucket = self._registry._root_models.setdefault(path_name, {})
            for ref_name, ref_config in models_data.items():
                model_id = ref_config.get("model")
                if not model_id:
                    raise PipelineConfigError(
                        f"Root model ref '{ref_name}' missing 'model' field"
                    )

                error = validate_model_id(model_id)
                if error:
                    raise PipelineConfigError(
                        f"Root model ref '{ref_name}' has invalid model ID: {error}"
                    )

                bucket[ref_name] = ModelRef(**ref_config)

            logger.info(
                f"  📄 Loaded {len(models_data)} root model ref(s) "
                f"from {path_name}/models.yaml"
            )
        except PipelineConfigError:
            raise
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Failed to load root models from {root_models_file}: {e}")
        except Exception:
            logger.exception(
                f"Unexpected error loading root models from {root_models_file}"
            )
            raise

    def _load_models(self, models_file: Path, domain: str, path_name: str) -> None:
        """Load pipeline model references with domain namespacing into path scope."""
        from .validator import PipelineConfigError

        with open(models_file) as f:
            data = yaml.safe_load(f)

        bucket = self._registry._domain_models.setdefault(path_name, {})
        for ref_name, ref_config in data.get("models", {}).items():
            model_id = ref_config.get("model")
            if not model_id:
                raise PipelineConfigError(
                    f"Model ref '{domain}.{ref_name}' missing 'model' field"
                )

            error = validate_model_id(model_id)
            if error:
                raise PipelineConfigError(
                    f"Model ref '{domain}.{ref_name}' has invalid model ID: {error}"
                )

            qualified_ref = f"{domain}.{ref_name}"
            bucket[qualified_ref] = ModelRef(**ref_config)

    def _merge_models(self) -> None:
        """
        Flatten scoped model dicts into unified view for log counts.

        The flattened view is NOT used for resolution (get_model_config
        resolves within a single search path). It exists only so that
        len(self.models) reports total model refs across all paths.
        """
        self._registry.models = {}
        for bucket in self._registry._root_models.values():
            self._registry.models.update(bucket)
        for bucket in self._registry._domain_models.values():
            self._registry.models.update(bucket)

    def _process_deferred_pipelines(self) -> None:
        """Retry pipelines deferred due to missing pipeline-as-service dependencies.

        Called once after all domains are loaded. By then, all pipeline IDs are
        registered in self.pipelines, so pipeline-as-service refs resolve correctly.
        ∀ deferred pipeline p: retried with defer=False to prevent infinite loops.
        """
        if not self._registry._deferred_pipelines:
            return
        deferred = self._registry._deferred_pipelines[:]
        self._registry._deferred_pipelines.clear()
        logger.info(f"  🔄 Retrying {len(deferred)} deferred pipeline(s)")
        for path, path_name, domain_dir in deferred:
            self._load_pipeline(path, path_name, domain_dir=domain_dir, _defer=False)

    def _load_pipeline(
        self,
        path: Path,
        path_name: str,
        *,
        domain_dir: Path | None = None,
        _defer: bool = True,
    ) -> None:
        """Load a single pipeline with availability filtering."""
        from pydantic import ValidationError

        from .validator import PipelineConfigError

        try:
            with path.open() as f:
                data = yaml.safe_load(f) or {}

            pipeline_data = data.get("pipeline", data)

            if "version" not in pipeline_data and "schema_version" not in pipeline_data:
                return

            steps = []
            for step_data in pipeline_data.get("steps", []):
                steps.append(StepConfig(**step_data))

            pipeline_data["steps"] = steps

            if self._registry._config_defaults:
                pipeline_options = pipeline_data.get("options", {})
                merged_options = {
                    **self._registry._config_defaults,
                    **pipeline_options,
                }
                pipeline_data["options"] = merged_options

            pipeline_data["source_search_path"] = path_name

            source_variant = ""
            if domain_dir is not None:
                try:
                    relative = path.relative_to(domain_dir)
                    if len(relative.parts) > 1:
                        source_variant = relative.parts[0]
                except ValueError:
                    pass
            pipeline_data["source_variant"] = source_variant

            pipeline = PipelineSpec(**pipeline_data)

            resolve_sub_pipelines(pipeline.steps, path.parent, visited=set())

            should_filter, required = self._registry._should_filter_pipeline(pipeline)
            if should_filter:
                if self._registry._is_model_available is not None:
                    missing = missing_models(
                        required,
                        is_available=self._registry._is_model_available,
                    )
                    if _defer:
                        self._registry._deferred_pipelines.append(
                            (path, path_name, domain_dir)
                        )
                        logger.info(
                            f"    ⏳ Deferred pipeline '{pipeline.id}' - "
                            f"missing: {sorted(missing)} (retry after domains load)"
                        )
                    else:
                        missing_list = sorted(missing)
                        self._registry._permanently_unavailable.append(
                            (pipeline.id, missing_list)
                        )
                        logger.warning(
                            f"    ⏭️  Skipping pipeline '{pipeline.id}' - "
                            f"missing required models: {missing_list} "
                            f"(required: {len(required)}, missing: {len(missing_list)})"
                        )
                return

            self._registry.pipelines[pipeline.id] = pipeline
            logger.info(
                f"    ✅ Loaded pipeline '{pipeline.id}' from {path_name}/{path.name}"
            )

        except (yaml.YAMLError, OSError) as e:
            logger.error(f"Failed to load pipeline from {path}: {e}")
        except ValidationError as e:
            logger.error(f"Pipeline schema validation failed for {path}: {e}")
        except PipelineConfigError as e:
            logger.error(f"Pipeline config error for {path}: {e}")
        except Exception:
            logger.exception(f"Unexpected error loading pipeline from {path}")
            raise
