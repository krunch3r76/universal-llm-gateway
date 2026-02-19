"""
Load pipeline configuration from YAML files.

Supports recursive sub-pipeline loading: when a step has ``pipeline_ref``,
the referenced YAML is loaded as a ``SubPipelineSpec`` and attached to the
step for later DAG expansion.
"""

from pathlib import Path

import yaml
from universal_hot_reload import read_text_preserving_timestamps
from universal_logging import get_logger

from .schemas import PipelineSpec, SharedModels, SharedPrompts, SubPipelineSpec

logger = get_logger(__name__)


class PipelineConfigLoader:
    """
    Loader for pipeline models, prompts, and pipeline specs.
    """

    def __init__(self, config_dir: str = "config") -> None:
        self.config_dir = Path(config_dir)

    def load_all(
        self,
    ) -> tuple[SharedModels, SharedPrompts, dict[str, PipelineSpec]]:
        models = self._load_models()
        prompts = self._load_prompts()
        pipelines = self._load_pipelines()
        logger.info(
            f"Loaded {len(models.models)} model refs, "
            + f"{len(prompts.prompts)} prompts, "
            + f"{len(pipelines)} pipelines"
        )
        return models, prompts, pipelines

    def _load_models(self) -> SharedModels:
        models_path = self.config_dir / "pipeline_models.yaml"
        if not models_path.exists():
            raise FileNotFoundError(f"Missing pipeline models file: {models_path}")
        # Read without triggering editor change notifications
        content = read_text_preserving_timestamps(models_path)
        data = yaml.safe_load(content) or {}
        return SharedModels(**data)

    def _load_prompts(self) -> SharedPrompts:
        """Load prompts from all pipeline_prompts*.yaml files and merge."""
        all_prompts = {}

        # Load all pipeline_prompts*.yaml files
        prompt_files = sorted(self.config_dir.glob("pipeline_prompts*.yaml"))

        if not prompt_files:
            raise FileNotFoundError(
                f"No pipeline_prompts*.yaml files found in {self.config_dir}"
            )

        for prompts_path in prompt_files:
            # Read without triggering editor change notifications
            content = read_text_preserving_timestamps(prompts_path)
            data = yaml.safe_load(content) or {}
            prompts_data = data.get("prompts", {})

            # Extract suffix from filename for namespacing
            # e.g., pipeline_prompts_transformation.yaml -> transformation
            stem = prompts_path.stem  # "pipeline_prompts_transformation"
            suffix = stem.replace("pipeline_prompts", "").lstrip("_")

            if suffix:
                # Add namespaced prompts (e.g., transformation.neutral.direct)
                # Top-level namespace is the suffix (matches directory structure)
                if suffix not in all_prompts:
                    all_prompts[suffix] = {}

                for category, category_prompts in prompts_data.items():
                    if isinstance(category_prompts, dict):
                        # Create nested namespace under suffix
                        if category not in all_prompts[suffix]:
                            all_prompts[suffix][category] = {}
                        # Add prompts under the namespace
                        for prompt_name, prompt_text in category_prompts.items():
                            all_prompts[suffix][category][prompt_name] = prompt_text
                            logger.debug(
                                f"Loaded {suffix}.{category}.{prompt_name} "
                                + f"from {prompts_path.name}"
                            )
            else:
                # Default prompts file (no suffix) - add without namespace
                for category, category_prompts in prompts_data.items():
                    if category not in all_prompts:
                        all_prompts[category] = {}
                    if isinstance(category_prompts, dict):
                        all_prompts[category].update(category_prompts)

            logger.info(f"Loaded prompts from {prompts_path.name}")

        return SharedPrompts(prompts=all_prompts)

    def _load_pipelines(self) -> dict[str, PipelineSpec]:
        pipelines_root = self.config_dir / "pipelines.d"
        if not pipelines_root.exists():
            raise FileNotFoundError(f"Missing pipelines directory: {pipelines_root}")

        specs: dict[str, PipelineSpec] = {}
        for path in pipelines_root.rglob("*.yaml"):
            # Read without triggering editor change notifications
            content = read_text_preserving_timestamps(path)
            data = yaml.safe_load(content) or {}
            try:
                spec = PipelineSpec(**data.get("pipeline", data))
            except Exception as exc:
                raise ValueError(f"Invalid pipeline spec {path}: {exc}") from exc

            self._resolve_sub_pipelines(spec.steps, path.parent, visited=set())
            specs[spec.id] = spec
            logger.info(f"Loaded pipeline '{spec.id}' from {path}")
        return specs

    def _resolve_sub_pipelines(
        self,
        steps: list,
        yaml_dir: Path,
        visited: set[str],
    ) -> None:
        """Recursively load sub-pipeline YAMLs referenced by ``pipeline_ref``.

        Attaches loaded ``SubPipelineSpec`` to the step via ``_sub_pipeline_spec``
        in model_extra so the DAG builder can expand it.

        Args:
            steps: Steps to scan for pipeline_ref fields.
            yaml_dir: Directory containing the parent YAML (for relative resolution).
            visited: Paths already being loaded (cycle detection).
        """
        for step in steps:
            pipeline_ref: str | None = step.get_domain_field("pipeline_ref")
            if not pipeline_ref:
                continue

            sub_path = (yaml_dir / pipeline_ref).resolve()
            path_key = str(sub_path)
            if path_key in visited:
                raise ValueError(
                    f"Cycle in sub-pipeline references: "
                    f"{step.name} -> {pipeline_ref} (already loading {path_key})"
                )

            if not sub_path.exists():
                raise FileNotFoundError(
                    f"Sub-pipeline '{pipeline_ref}' referenced by step "
                    f"'{step.name}' not found at {sub_path}"
                )

            visited.add(path_key)
            content = read_text_preserving_timestamps(sub_path)
            data = yaml.safe_load(content) or {}
            try:
                sub_spec = SubPipelineSpec(**data.get("pipeline", data))
            except Exception as exc:
                raise ValueError(
                    f"Invalid sub-pipeline spec {sub_path}: {exc}"
                ) from exc

            # Recurse into the sub-pipeline's own steps
            self._resolve_sub_pipelines(sub_spec.steps, sub_path.parent, visited)

            step.__pydantic_extra__["_sub_pipeline_spec"] = sub_spec
            visited.discard(path_key)
            logger.info(
                f"Loaded sub-pipeline '{sub_spec.id}' for step '{step.name}' "
                f"from {sub_path}"
            )
