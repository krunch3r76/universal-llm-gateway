"""Pipeline source maps for functional settle (affected pipeline diff)."""

from __future__ import annotations

import inspect
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_SEARCH_PATHS = ("pipelines", "pipelines.local")
_EXCLUDED_YAML = frozenset({"prompts.yaml", "models.yaml", "categories.yaml"})


@dataclass(frozen=True)
class FunctionalSettlePipelineMaps:
    pipeline_sources: dict[str, tuple[str, ...]]
    step_type_modules: dict[str, str]
    pipeline_step_types: dict[str, tuple[str, ...]]


def _git_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    return proc.returncode == 0


def provider_land_in_scope(
    provider_land: str,
    consumer_land: str,
    *,
    source_repo: Path | None,
) -> bool:
    """True when *provider_land* is equal to or an ancestor of *consumer_land*."""
    if provider_land == consumer_land:
        return True
    if source_repo is None:
        return False
    return _git_is_ancestor(source_repo, provider_land, consumer_land)


def functional_settle_pipeline_maps(
    source_repo: Path,
    *,
    search_paths: Iterable[str] | None = None,
) -> FunctionalSettlePipelineMaps:
    """Build settle-gate pipeline maps from on-disk pipeline YAML and handlers."""
    repo = source_repo.expanduser().resolve()
    paths = tuple(search_paths or _DEFAULT_SEARCH_PATHS)
    pipeline_sources: dict[str, list[str]] = {}
    pipeline_step_types: dict[str, list[str]] = {}
    step_types_seen: set[str] = set()

    for search_path in paths:
        root = (repo / search_path).resolve()
        if not root.is_dir():
            continue
        for domain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if domain_dir.name.startswith(".") or domain_dir.name == "__pycache__":
                continue
            for yaml_file in sorted(domain_dir.rglob("*.yaml")):
                if not yaml_file.is_file() or yaml_file.name in _EXCLUDED_YAML:
                    continue
                try:
                    rel_yaml = yaml_file.relative_to(repo).as_posix()
                    data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    continue
                if not isinstance(data, dict):
                    continue
                pipeline_data = data.get("pipeline", data)
                if not isinstance(pipeline_data, dict):
                    continue
                pipeline_id = pipeline_data.get("id")
                if not isinstance(pipeline_id, str) or not pipeline_id.strip():
                    continue
                pid = pipeline_id.strip()
                sources = pipeline_sources.setdefault(pid, [])
                if rel_yaml not in sources:
                    sources.append(rel_yaml)
                raw_steps = pipeline_data.get("steps") or []
                if not isinstance(raw_steps, list):
                    continue
                step_types: list[str] = []
                for step in raw_steps:
                    if not isinstance(step, dict):
                        continue
                    st = step.get("type") or step.get("step_type")
                    if isinstance(st, str) and st.strip():
                        step_types.append(st.strip())
                        step_types_seen.add(st.strip())
                if step_types:
                    existing = pipeline_step_types.setdefault(pid, [])
                    for st in step_types:
                        if st not in existing:
                            existing.append(st)

    step_type_modules = _step_type_module_paths(repo, paths, step_types_seen)
    return FunctionalSettlePipelineMaps(
        pipeline_sources={k: tuple(v) for k, v in pipeline_sources.items()},
        step_type_modules=step_type_modules,
        pipeline_step_types={k: tuple(v) for k, v in pipeline_step_types.items()},
    )


def _step_type_module_paths(
    repo: Path,
    search_paths: tuple[str, ...],
    step_types: set[str],
) -> dict[str, str]:
    if not step_types:
        return {}
    try:
        from systems.pipeline.core.domain_router import get_domain_router
        from systems.pipeline.core.handlers.registry import HandlerRegistry
        from systems.pipeline.user_handlers import load_user_handlers
    except ImportError:
        return {}

    HandlerRegistry._ensure_initialized()
    router = get_domain_router()
    for search_path in search_paths:
        base = (repo / search_path).resolve()
        if base.is_dir():
            load_user_handlers(base)

    out: dict[str, str] = {}
    for step_type in step_types:
        handler_class = router.find_handler_class_by_step_type(step_type)
        if handler_class is None:
            continue
        try:
            module_path = Path(inspect.getfile(handler_class)).resolve()
            rel = module_path.relative_to(repo).as_posix()
        except (TypeError, OSError, ValueError):
            continue
        out[step_type] = rel
    return out


__all__ = [
    "FunctionalSettlePipelineMaps",
    "functional_settle_pipeline_maps",
    "provider_land_in_scope",
]
