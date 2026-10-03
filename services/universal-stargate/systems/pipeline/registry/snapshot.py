"""On-disk snapshot of a built pipeline registry.

A stargate health restart stop/starts the process. ``PipelineRegistry.load``
walks every pipeline YAML, and ``reload_pipelines`` does that walk again on a
fresh instance. The source fingerprint and the definition fingerprint are
taken before that walk and again after it. The snapshot is written under the
before-walk values only when both pairs are equal. A YAML file created during
the walk is therefore not stored under the post-write fingerprints with a
build that omitted it. The snapshot file is reused when the source fingerprint
matches. Inside that file, an entry is reused only when its availability
decisions still hold and its definition fingerprint matches the YAML bytes
the loader walk opens (including ``pipeline_ref`` sub-pipelines) and the
handler code identity of this process. Entries written under the older
availability-only key are skipped, so a flap does not pay one full build
per restart and a stale build cannot replace a walk that includes a
pipeline the stale build omitted.

(b) handler code identity and (c) sub-pipeline files opened by the walk are
covered. A byte-identical restore after a mid-walk edit still matches the
before-walk fingerprints (low, accepted).

Bump ``SNAPSHOT_VERSION`` when the persisted state shape or load semantics
change. A mismatch falls through to a full build.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from universal_logging import get_logger

from ..core.schemas import PipelineSpec
from ..schemas import ModelRef

if TYPE_CHECKING:
    from .core import PipelineRegistry

logger = get_logger(__name__)

SNAPSHOT_VERSION = 1
_MAX_AVAILABILITY_ENTRIES = 4
_ENV_SNAPSHOT_DIR = "STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR"
_EXCLUDED_PIPELINE_YAML = frozenset({"prompts.yaml", "models.yaml", "categories.yaml"})


def default_snapshot_dir() -> Path:
    """Directory for the process-restart snapshot.

    ``STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR`` wins. Otherwise the file lives
    under ``DATA_DIR`` (default ``~/.gateway``).
    """
    raw = os.environ.get(_ENV_SNAPSHOT_DIR)
    if raw:
        return Path(raw).expanduser()
    data = os.environ.get("DATA_DIR")
    base = Path(data).expanduser() if data else Path.home() / ".gateway"
    return base / "pipeline-registry-snapshot"


def snapshot_dir_for(registry: PipelineRegistry) -> Path | None:
    """Explicit constructor dir, else the env override, else no snapshot."""
    if registry._snapshot_dir is not None:
        return registry._snapshot_dir
    raw = os.environ.get(_ENV_SNAPSHOT_DIR)
    if not raw:
        return None
    return Path(raw).expanduser()


def source_fingerprint(registry: PipelineRegistry) -> str:
    """Hash of snapshot version, defaults, and YAML size/mtime under search paths."""
    defaults = json.dumps(
        registry._config_defaults,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    lines = [f"v{SNAPSHOT_VERSION}", defaults]
    for search_path in registry._search_paths:
        expanded = Path(search_path).expanduser()
        if not expanded.is_absolute():
            resolved = (registry._config_base_dir / expanded).resolve()
        else:
            resolved = expanded.resolve()
        if not resolved.exists():
            lines.append(f"missing:{search_path}")
            continue
        files = sorted(path for path in resolved.rglob("*.yaml") if path.is_file())
        if not files:
            lines.append(f"empty:{resolved}")
        for yaml_path in files:
            rel = yaml_path.relative_to(resolved).as_posix()
            stat = yaml_path.stat()
            lines.append(f"{resolved}:{rel}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _resolve_search_path(registry: PipelineRegistry, search_path: str) -> Path:
    expanded = Path(search_path).expanduser()
    if not expanded.is_absolute():
        return (registry._config_base_dir / expanded).resolve()
    return expanded.resolve()


def _is_domain_dir(domain_dir: Path) -> bool:
    return (
        domain_dir.is_dir()
        and not domain_dir.name.startswith(".")
        and domain_dir.name != "__pycache__"
    )


def _sub_pipeline_paths(pipeline_file: Path) -> list[Path]:
    """Paths ``resolve_sub_pipelines`` opens for one pipeline file.

    Resolution uses the loader function, so ``pipeline_ref`` matches runtime
    (``(yaml_dir / pipeline_ref).resolve()``, including ``.yml``). A missing or
    invalid ref does not fail fingerprinting; the parent file is still hashed.
    """
    import systems.pipeline.loader as loader_mod

    from ..core.schemas import StepConfig
    from ..loader import resolve_sub_pipelines

    try:
        data = yaml.safe_load(pipeline_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    pipeline_data = data.get("pipeline", data)
    if not isinstance(pipeline_data, dict):
        return []
    raw_steps = pipeline_data.get("steps") or []
    if not isinstance(raw_steps, list):
        return []
    steps = []
    try:
        for step_data in raw_steps:
            if isinstance(step_data, dict):
                steps.append(StepConfig(**step_data))
    except (TypeError, ValueError):
        return []

    opened: list[Path] = []
    real = loader_mod.read_text_preserving_timestamps

    def _tracking(file_path: Path, encoding: str = "utf-8") -> str:
        opened.append(Path(file_path).resolve())
        return real(file_path, encoding=encoding)

    loader_mod.read_text_preserving_timestamps = _tracking
    try:
        resolve_sub_pipelines(steps, pipeline_file.parent, visited=set())
    except (OSError, ValueError, yaml.YAMLError):
        return opened
    finally:
        loader_mod.read_text_preserving_timestamps = real
    return opened


def discover_definition_yaml_paths(registry: PipelineRegistry) -> list[Path]:
    """YAML the loader walk opens, without running pipeline validation.

    For each resolved search path, domain iteration matches
    ``PipelineLoader._load_from_search_path`` / ``_load_domain``: domain
    ``models.yaml``, domain and nested ``prompts.yaml``, and pipeline
    ``*.yaml`` except prompts, models, and categories. Each pipeline file is
    ``yaml.safe_load``-ed and ``resolve_sub_pipelines`` is run so ``pipeline_ref``
    targets are included, including ``.yml`` and paths outside the search-path
    ``*.yaml`` rglob.
    """
    found: dict[Path, None] = {}

    def add(path: Path) -> None:
        found.setdefault(path.resolve(), None)

    for search_path in registry._search_paths:
        resolved = _resolve_search_path(registry, search_path)
        if not resolved.exists():
            continue
        for domain_dir in sorted(p for p in resolved.iterdir() if _is_domain_dir(p)):
            models_file = domain_dir / "models.yaml"
            if models_file.is_file():
                add(models_file)
            prompts_file = domain_dir / "prompts.yaml"
            if prompts_file.is_file():
                add(prompts_file)
            for nested_prompts in sorted(domain_dir.rglob("prompts.yaml")):
                if nested_prompts.parent == domain_dir:
                    continue
                if nested_prompts.is_file():
                    add(nested_prompts)
            for yaml_file in sorted(domain_dir.rglob("*.yaml")):
                if not yaml_file.is_file() or yaml_file.name in _EXCLUDED_PIPELINE_YAML:
                    continue
                add(yaml_file)
                for sub_path in _sub_pipeline_paths(yaml_file):
                    add(sub_path)
    return list(found)


def _handler_module_digest(path: Path, classes: list[type]) -> str:
    """File bytes plus each class's live ``validate`` code object.

    File bytes cover a ``validate()`` body edit seen by the next process.
    The code object covers an in-process replacement of ``validate`` that
    leaves the step_type name unchanged.
    """
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    for cls in sorted(classes, key=lambda item: item.__qualname__):
        validate = getattr(cls, "validate", None)
        code = getattr(validate, "__code__", None)
        if code is None:
            continue
        hasher.update(cls.__qualname__.encode())
        hasher.update(code.co_code)
        hasher.update(repr(code.co_consts).encode())
    return hasher.hexdigest()


def _handler_code_lines() -> list[str]:
    """Sorted ``handler:{path}:{digest}`` lines for registered handler classes."""
    from ..core.domain_router import get_domain_router
    from ..core.handlers.registry import HandlerRegistry

    HandlerRegistry._ensure_initialized()
    router = get_domain_router()
    classes: list[type] = [
        *HandlerRegistry._generic_handler_classes.values(),
        *router._generic_handler_classes.values(),
        *router._domain_handler_classes.values(),
    ]
    by_path: dict[str, list[type]] = {}
    seen: set[int] = set()
    for cls in classes:
        if id(cls) in seen:
            continue
        seen.add(id(cls))
        try:
            file_path = str(Path(inspect.getfile(cls)).resolve())
        except (TypeError, OSError):
            continue
        by_path.setdefault(file_path, []).append(cls)
    lines: list[str] = []
    for file_path in sorted(by_path):
        digest = _handler_module_digest(Path(file_path), by_path[file_path])
        lines.append(f"handler:{file_path}:{digest}")
    return lines


def definition_fingerprint(registry: PipelineRegistry) -> str:
    """Hash of walk-opened YAML bytes and live handler code identity.

    Handler lines are ``handler:{path}:{digest}`` for each registered class
    file (generic map and domain-router class map). YAML lines are content
    hashes of :func:`discover_definition_yaml_paths`, keyed by resolved path.
    Handler name lists stay in the hash so a new step_type in an
    already-hashed module still moves the fingerprint.
    """
    from ..core.handlers.registry import HandlerRegistry

    HandlerRegistry._ensure_initialized()
    identity = json.dumps(
        HandlerRegistry.list_handlers(),
        sort_keys=True,
        separators=(",", ":"),
    )
    lines = [identity, *_handler_code_lines()]
    discovered = discover_definition_yaml_paths(registry)
    discovered_set = set(discovered)
    for search_path in registry._search_paths:
        resolved = _resolve_search_path(registry, search_path)
        if not resolved.exists():
            lines.append(f"missing:{search_path}")
            continue
        owned = [
            path
            for path in discovered_set
            if path == resolved or path.is_relative_to(resolved)
        ]
        if not owned:
            lines.append(f"empty:{resolved}")
    for yaml_path in sorted(discovered, key=lambda item: str(item)):
        digest = hashlib.sha256(yaml_path.read_bytes()).hexdigest()
        lines.append(f"{yaml_path}:{digest}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def try_restore(registry: PipelineRegistry, snapshot_dir: Path) -> bool:
    """Install a prior build when sources, definitions, and availability still hold."""
    path = _snapshot_path(registry, snapshot_dir)
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        current_definition = definition_fingerprint(registry)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("pipeline registry snapshot unreadable: %s", exc)
        return False
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
        return False
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Availability-only entries (pre-definition key) must not win.
        if not _definition_present(entry):
            continue
        if entry.get("definition") != current_definition:
            logger.info("entry skipped: definition differs")
            continue
        recorded = _parse_availability(entry.get("availability"))
        if recorded is None or not _availability_holds(registry, recorded):
            continue
        state = _decode_state(entry.get("state"))
        if state is None:
            continue
        _apply_state(registry, state)
        logger.info(
            "pipeline registry snapshot reused: %d pipeline(s)",
            len(registry.pipelines),
        )
        return True
    return False


def persist(
    registry: PipelineRegistry,
    snapshot_dir: Path,
    availability: list[tuple[str, bool]],
    *,
    source: str,
    definition: str,
) -> None:
    """Write this build under the before-walk fingerprints when they still match.

    ``source`` and ``definition`` are the fingerprints taken before the walk.
    A tree that changed during the walk is left unsnapshotted. Failure of the
    write itself leaves the in-memory build live.
    """
    try:
        if (
            source_fingerprint(registry) != source
            or definition_fingerprint(registry) != definition
        ):
            return
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = snapshot_dir / f"{source}.json"
        existing = _read_entries(path)
        signature = _entry_key(availability, definition)
        kept = [entry for entry in existing if _entry_signature(entry) != signature]
        fresh = {
            "availability": [[model_id, bit] for model_id, bit in availability],
            "definition": definition,
            "state": _encode_state(registry),
        }
        entries = [fresh, *kept][:_MAX_AVAILABILITY_ENTRIES]
        blob = json.dumps(
            {"version": SNAPSHOT_VERSION, "entries": entries},
            separators=(",", ":"),
        )
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("pipeline registry snapshot not written: %s", exc)


def _snapshot_path(registry: PipelineRegistry, snapshot_dir: Path) -> Path:
    return snapshot_dir / f"{source_fingerprint(registry)}.json"


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _availability_signature(availability: list[tuple[str, bool]]) -> str:
    return json.dumps(availability, separators=(",", ":"))


def _definition_present(entry: dict[str, Any]) -> bool:
    definition = entry.get("definition")
    return isinstance(definition, str) and bool(definition)


def _entry_key(availability: list[tuple[str, bool]], definition: str) -> str:
    return _availability_signature(availability) + "\n" + definition


def _entry_signature(entry: dict[str, Any]) -> str:
    recorded = _parse_availability(entry.get("availability"))
    definition = entry.get("definition")
    if recorded is None or not isinstance(definition, str) or not definition:
        return ""
    return _entry_key(recorded, definition)


def _parse_availability(raw: object) -> list[tuple[str, bool]] | None:
    if not isinstance(raw, list):
        return None
    parsed: list[tuple[str, bool]] = []
    for item in raw:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], bool)
        ):
            return None
        parsed.append((item[0], item[1]))
    return parsed


def _availability_holds(
    registry: PipelineRegistry, recorded: list[tuple[str, bool]]
) -> bool:
    checker = registry._is_model_available
    if checker is None:
        return not recorded
    if not recorded:
        # Built with no availability probe. A live checker might filter.
        return False
    for model_id, expected in recorded:
        if bool(checker(model_id)) != expected:
            return False
    return True


def _encode_state(registry: PipelineRegistry) -> dict[str, Any]:
    return {
        "pipelines": {
            pipeline_id: spec.model_dump(mode="json", by_alias=True)
            for pipeline_id, spec in registry.pipelines.items()
        },
        "prompts": registry.prompts,
        "models": _dump_models(registry.models),
        "root_models": {
            path_name: _dump_models(bucket)
            for path_name, bucket in registry._root_models.items()
        },
        "domain_models": {
            path_name: _dump_models(bucket)
            for path_name, bucket in registry._domain_models.items()
        },
        "validation_errors": list(registry._validation_errors),
        "catalog_skips": list(registry._catalog_skips),
        "permanently_unavailable": [
            [pipeline_id, list(missing)]
            for pipeline_id, missing in registry._permanently_unavailable
        ],
    }


def _dump_models(bucket: dict[str, ModelRef]) -> dict[str, Any]:
    return {
        alias: ref.model_dump(mode="json", by_alias=True)
        for alias, ref in bucket.items()
    }


def _decode_state(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        pipelines = {
            pipeline_id: PipelineSpec.model_validate(spec)
            for pipeline_id, spec in raw["pipelines"].items()
        }
        models = _load_models(raw["models"])
        root_models = {
            path_name: _load_models(bucket)
            for path_name, bucket in raw["root_models"].items()
        }
        domain_models = {
            path_name: _load_models(bucket)
            for path_name, bucket in raw["domain_models"].items()
        }
        unavailable = [
            (pipeline_id, list(missing))
            for pipeline_id, missing in raw["permanently_unavailable"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("pipeline registry snapshot state rejected: %s", exc)
        return None
    return {
        "pipelines": pipelines,
        "prompts": raw.get("prompts") or {},
        "models": models,
        "root_models": root_models,
        "domain_models": domain_models,
        "validation_errors": list(raw.get("validation_errors") or []),
        "catalog_skips": list(raw.get("catalog_skips") or []),
        "permanently_unavailable": unavailable,
    }


def _load_models(raw: object) -> dict[str, ModelRef]:
    if not isinstance(raw, dict):
        raise TypeError("model bucket is not an object")
    return {alias: ModelRef.model_validate(ref) for alias, ref in raw.items()}


def _apply_state(registry: PipelineRegistry, state: dict[str, Any]) -> None:
    registry.pipelines = state["pipelines"]
    registry.prompts = state["prompts"]
    registry.models = state["models"]
    registry._root_models = state["root_models"]
    registry._domain_models = state["domain_models"]
    registry._validation_errors = state["validation_errors"]
    registry._catalog_skips = state["catalog_skips"]
    registry._permanently_unavailable = state["permanently_unavailable"]
    registry._deferred_pipelines = []
