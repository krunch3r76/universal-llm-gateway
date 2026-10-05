"""
Sub-pipeline resolution for pipeline YAML.

When a step has ``pipeline_ref``, the referenced YAML is loaded as a
``SubPipelineSpec`` and attached to the step for later DAG expansion.
"""

from collections.abc import Callable
from pathlib import Path

import yaml
from universal_hot_reload import read_text_preserving_timestamps
from universal_logging import get_logger

from .schemas import SubPipelineSpec

logger = get_logger(__name__)


def resolve_sub_pipelines(
    steps: list,
    yaml_dir: Path,
    visited: set[str],
    on_open: Callable[[Path], None] | None = None,
) -> None:
    """Recursively load sub-pipeline YAMLs referenced by ``pipeline_ref``.

    Attaches loaded ``SubPipelineSpec`` to the step via ``_sub_pipeline_spec``
    in model_extra so the DAG builder can expand it.

    Args:
        steps: Steps to scan for pipeline_ref fields.
        yaml_dir: Directory containing the parent YAML (for relative resolution).
        visited: Paths already being loaded (cycle detection).
        on_open: Called with the resolved sub-pipeline path before it is read.
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
        if on_open is not None:
            on_open(sub_path)
        content = read_text_preserving_timestamps(sub_path)
        data = yaml.safe_load(content) or {}
        try:
            sub_spec = SubPipelineSpec(**data.get("pipeline", data))
        except Exception as exc:
            raise ValueError(f"Invalid sub-pipeline spec {sub_path}: {exc}") from exc

        resolve_sub_pipelines(sub_spec.steps, sub_path.parent, visited, on_open)

        step.__pydantic_extra__["_sub_pipeline_spec"] = sub_spec
        visited.discard(path_key)
        logger.info(
            f"Loaded sub-pipeline '{sub_spec.id}' for step '{step.name}' "
            f"from {sub_path}"
        )
