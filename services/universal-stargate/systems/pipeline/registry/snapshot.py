"""On-disk snapshot of a built pipeline registry.

A stargate health restart stop/starts the process. ``PipelineRegistry.load``
walks every pipeline YAML, and ``reload_pipelines`` does that walk again on a
fresh instance. The snapshot is reused when the source fingerprint matches and
the availability decisions recorded at build time still hold, so a flap does
not pay one full build per restart.

Bump ``SNAPSHOT_VERSION`` when the persisted state shape or load semantics
change. A mismatch falls through to a full build.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..core.schemas import PipelineSpec
from ..schemas import ModelRef

if TYPE_CHECKING:
    from .core import PipelineRegistry

logger = get_logger(__name__)

SNAPSHOT_VERSION = 1
_MAX_AVAILABILITY_ENTRIES = 4
_ENV_SNAPSHOT_DIR = "STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR"


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


def try_restore(registry: PipelineRegistry, snapshot_dir: Path) -> bool:
    """Install a prior build when sources and availability decisions still hold."""
    path = _snapshot_path(registry, snapshot_dir)
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
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
) -> None:
    """Write this build under its source fingerprint. Failure leaves the build live."""
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = _snapshot_path(registry, snapshot_dir)
        existing = _read_entries(path)
        signature = _availability_signature(availability)
        kept = [entry for entry in existing if _entry_signature(entry) != signature]
        fresh = {
            "availability": [[model_id, bit] for model_id, bit in availability],
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


def _entry_signature(entry: dict[str, Any]) -> str:
    recorded = _parse_availability(entry.get("availability"))
    if recorded is None:
        return ""
    return _availability_signature(recorded)


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
