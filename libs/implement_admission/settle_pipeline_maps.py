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


def _commit_unix_time(repo: Path, rev: str) -> int:
    proc = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%ct", rev],
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int(proc.stdout.strip() or "0")
    except ValueError:
        return 0


def newest_in_scope_verdict(
    in_scope: Iterable[tuple[str, str]],
    *,
    source_repo: Path | None,
) -> str | None:
    """Verdict of the newest in-scope land.

    A land is newer when the other is its ancestor. Incomparable lands break
    the tie by committer unix time. Empty input returns ``None``.
    """
    pairs = list(in_scope)
    if not pairs:
        return None
    newest_land, newest_verdict = pairs[0]
    newest_time = (
        _commit_unix_time(source_repo, newest_land) if source_repo is not None else 0
    )
    for land, verdict in pairs[1:]:
        if source_repo is not None and _git_is_ancestor(source_repo, newest_land, land):
            newest_land, newest_verdict = land, verdict
            newest_time = _commit_unix_time(source_repo, land)
            continue
        if source_repo is not None and _git_is_ancestor(source_repo, land, newest_land):
            continue
        land_time = (
            _commit_unix_time(source_repo, land) if source_repo is not None else 0
        )
        if land_time >= newest_time:
            newest_land, newest_verdict = land, verdict
            newest_time = land_time
    return newest_verdict


def _absorb_pipeline_yaml(
    rel_yaml: str,
    text: str,
    pipeline_sources: dict[str, list[str]],
    pipeline_step_types: dict[str, list[str]],
    step_types_seen: set[str],
) -> None:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return
    if not isinstance(data, dict):
        return
    pipeline_data = data.get("pipeline", data)
    if not isinstance(pipeline_data, dict):
        return
    pipeline_id = pipeline_data.get("id")
    if not isinstance(pipeline_id, str) or not pipeline_id.strip():
        return
    pid = pipeline_id.strip()
    sources = pipeline_sources.setdefault(pid, [])
    if rel_yaml not in sources:
        sources.append(rel_yaml)
    raw_steps = pipeline_data.get("steps") or []
    if not isinstance(raw_steps, list):
        return
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        st = step.get("type") or step.get("step_type")
        if not isinstance(st, str) or not st.strip():
            continue
        step_name = st.strip()
        step_types_seen.add(step_name)
        existing = pipeline_step_types.setdefault(pid, [])
        if step_name not in existing:
            existing.append(step_name)


def _maps_from_workdir(
    repo: Path,
    paths: tuple[str, ...],
) -> tuple[dict[str, list[str]], dict[str, list[str]], set[str]]:
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
                    text = yaml_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                _absorb_pipeline_yaml(
                    rel_yaml, text, pipeline_sources, pipeline_step_types, step_types_seen
                )
    return pipeline_sources, pipeline_step_types, step_types_seen


def _maps_from_git_tree(
    repo: Path,
    tree_ish: str,
    paths: tuple[str, ...],
) -> tuple[dict[str, list[str]], dict[str, list[str]], set[str]]:
    """Parse pipeline YAML at *tree_ish*. Missing tree yields empty maps."""
    pipeline_sources: dict[str, list[str]] = {}
    pipeline_step_types: dict[str, list[str]] = {}
    step_types_seen: set[str] = set()
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "--name-only",
            tree_ish,
            "--",
            *paths,
        ],
        capture_output=True,
        text=True,
        timeout=15.0,
    )
    if listed.returncode != 0:
        return pipeline_sources, pipeline_step_types, step_types_seen
    for rel_yaml in listed.stdout.splitlines():
        if not rel_yaml.endswith(".yaml"):
            continue
        if Path(rel_yaml).name in _EXCLUDED_YAML:
            continue
        shown = subprocess.run(
            ["git", "-C", str(repo), "show", f"{tree_ish}:{rel_yaml}"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if shown.returncode != 0:
            continue
        _absorb_pipeline_yaml(
            rel_yaml,
            shown.stdout,
            pipeline_sources,
            pipeline_step_types,
            step_types_seen,
        )
    return pipeline_sources, pipeline_step_types, step_types_seen


def _union_maps(
    left_sources: dict[str, list[str]],
    left_steps: dict[str, list[str]],
    left_seen: set[str],
    right_sources: dict[str, list[str]],
    right_steps: dict[str, list[str]],
    right_seen: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]], set[str]]:
    seen = set(left_seen)
    seen.update(right_seen)
    for pid, srcs in right_sources.items():
        bucket = left_sources.setdefault(pid, [])
        for src in srcs:
            if src not in bucket:
                bucket.append(src)
    for pid, steps in right_steps.items():
        bucket = left_steps.setdefault(pid, [])
        for step in steps:
            if step not in bucket:
                bucket.append(step)
    return left_sources, left_steps, seen


def functional_settle_pipeline_maps(
    source_repo: Path,
    *,
    search_paths: Iterable[str] | None = None,
    merge_sha: str | None = None,
) -> FunctionalSettlePipelineMaps:
    """Build settle-gate pipeline maps from on-disk YAML and the parent commit.

    The working tree is the post-land checkout. When *merge_sha* is set, YAML
    from ``merge_sha^1`` is merged in so a pipeline whose file the land
    renamed, deleted, or left unparseable stays in the map.
    """
    repo = source_repo.expanduser().resolve()
    paths = tuple(search_paths or _DEFAULT_SEARCH_PATHS)
    pipeline_sources, pipeline_step_types, step_types_seen = _maps_from_workdir(
        repo, paths
    )
    if merge_sha:
        parent_sources, parent_steps, parent_seen = _maps_from_git_tree(
            repo, f"{merge_sha}^1", paths
        )
        pipeline_sources, pipeline_step_types, step_types_seen = _union_maps(
            pipeline_sources,
            pipeline_step_types,
            step_types_seen,
            parent_sources,
            parent_steps,
            parent_seen,
        )

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
    "newest_in_scope_verdict",
    "provider_land_in_scope",
]
