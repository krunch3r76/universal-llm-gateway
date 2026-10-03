"""
Core PipelineRegistry orchestration.

Orchestrates loading, validation, and access components.
Contains main initialization, reload logic, and pipeline filtering.
Part of the pipeline registry package.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..availability import (
    are_models_available,
    get_pipeline_required_models,
    missing_models,
)
from ..core.schemas import PipelineSpec
from .access import PipelineAccessor
from .loader import PipelineLoader
from .validator import PipelineValidator

if TYPE_CHECKING:
    from ..schemas import ModelRef

logger = get_logger(__name__)

_UNREGISTERED_HANDLER_MARKER = "No handler for type"
_UNKNOWN_STEP_TYPE_RE = re.compile(r"No handler for type '([^']+)'")


def _validation_errors_for_pipeline(
    validation_errors: list[str], pipeline_id: str
) -> list[str]:
    prefix = f"[{pipeline_id}] "
    return [
        err[len(prefix) :]
        for err in validation_errors
        if err.startswith(prefix)
    ]


def _only_unregistered_handler_errors(messages: list[str]) -> bool:
    if not messages:
        return False
    return all(_UNREGISTERED_HANDLER_MARKER in msg for msg in messages)


def _extract_unknown_step_type(messages: list[str]) -> str | None:
    for msg in messages:
        match = _UNKNOWN_STEP_TYPE_RE.search(msg)
        if match:
            return match.group(1)
    return None


def _apply_keep_last_good_on_reload(
    previous_pipelines: dict[str, PipelineSpec],
    fresh: "PipelineRegistry",
) -> None:
    """Restore pipelines dropped only for unregistered step types (hot-reload)."""
    removed_ids = set(previous_pipelines) - set(fresh.pipelines)
    for pipeline_id in removed_ids:
        pipeline_errors = _validation_errors_for_pipeline(
            fresh._validation_errors, pipeline_id
        )
        if not _only_unregistered_handler_errors(pipeline_errors):
            continue
        prior = previous_pipelines[pipeline_id]
        should_filter, _ = fresh._should_filter_pipeline(prior)
        if should_filter:
            continue
        revalidation_errors = fresh._validator._validate_pipeline(prior)
        if revalidation_errors:
            continue
        unknown_type = _extract_unknown_step_type(pipeline_errors) or "unknown"
        fresh.pipelines[pipeline_id] = prior
        error_prefix = f"[{pipeline_id}] "
        fresh._validation_errors = [
            err
            for err in fresh._validation_errors
            if not err.startswith(error_prefix)
        ]
        logger.warning(
            "Keeping last good pipeline '%s' after reload: unregistered step type '%s'",
            pipeline_id,
            unknown_type,
        )


class PipelineRegistry:
    """
    Registry for pipeline configurations.

    Loads pipelines, models, and prompts from YAML configuration.
    Validates all configurations at load time (fail-fast).

    Filtering: Pipeline p loaded ⟺ each required model passes *is_model_available*.
    """

    def __init__(
        self,
        search_paths: list[str] | None = None,
        is_model_available: Callable[[str], bool] | None = None,
        config_defaults: dict[str, Any] | None = None,
        config_base_dir: Path | None = None,
        snapshot_dir: Path | None = None,
    ):
        """
        Initialize registry.

        Args:
            search_paths: List of directories to search for pipelines.
                          Each path is an isolated model namespace.
                          Later paths override earlier for same pipeline ID.
                          Relative paths resolved relative to config_base_dir.
            is_model_available: Per-model availability (ModelId-aware catalog +
                registered pipeline IDs). None disables filtering.
            config_defaults: Default pipeline options from stargate_config.yaml
                            (pipeline-specific options override these)
            config_base_dir: Base directory for resolving relative search paths.
                            Defaults to current working directory if None.
        """

        self._search_paths = search_paths or ["config"]
        self._is_model_available = is_model_available
        self._config_defaults = config_defaults or {}
        self._config_base_dir = config_base_dir or Path.cwd()
        self._snapshot_dir = snapshot_dir
        self.last_load_was_full = False
        self.pipelines: dict[str, PipelineSpec] = {}
        self.prompts: dict[str, Any] = {}
        self._validation_errors: list[str] = []
        self._catalog_skips: list[dict[str, str]] = []
        self._deferred_pipelines: list[tuple[Path, str, Path | None]] = []
        self._permanently_unavailable: list[tuple[str, list[str]]] = []

        self._root_models: dict[str, dict[str, ModelRef]] = {}
        self._domain_models: dict[str, dict[str, ModelRef]] = {}

        self.models: dict[str, ModelRef] = {}

        self._loader = PipelineLoader(self)
        self._validator = PipelineValidator(self)
        self._accessor = PipelineAccessor(self)

    def load(self) -> None:
        """
        Load all configurations from search paths.

        Invariant: each search path is an isolated model namespace.
        Pipelines resolve models only from their own search path.
        Later paths override earlier for same pipeline ID only.

        When a snapshot dir is set (constructor or
        ``STARGATE_PIPELINE_REGISTRY_SNAPSHOT_DIR``), the source fingerprint
        and the definition fingerprint are taken before the YAML walk and
        again after it. The snapshot is persisted under the before-walk values
        only when both pairs still match, so a file written during the walk
        is not masked by a snapshot of the incomplete build. A process restart
        reuses the last build if the definition fingerprint and the availability
        decisions from that build still hold. The definition fingerprint covers
        (b) handler code identity as the bytes of ``systems/pipeline`` ``*.py``
        files plus ``*.py`` under each handler class module directory (MRO,
        site-packages excluded), so a ``validate()`` body change is visible
        without a step_type rename, and (c) sub-pipeline files the walk opens
        via ``pipeline_ref``, including paths outside the search-path
        ``*.yaml`` rglob. Helpers under ``libs/`` that ``validate`` calls are
        not covered. A byte-identical restore after an edit made mid-walk
        still matches the before-walk fingerprints (low, accepted).
        ``last_load_was_full`` is True only for the YAML walk.

        Pre: search_paths ≠ ∅
        Post: pipelines ∪ models ∪ prompts loaded ∧ validated

        Raises:
            PipelineConfigError: If validation errors found
        """
        from .snapshot import (
            definition_fingerprint,
            persist,
            snapshot_dir_for,
            source_fingerprint,
            try_restore,
        )

        snapshot_dir = snapshot_dir_for(self)
        if snapshot_dir is not None and try_restore(self, snapshot_dir):
            self.last_load_was_full = False
            return
        pinned: tuple[str, str] | None = None
        if snapshot_dir is not None:
            try:
                pinned = (source_fingerprint(self), definition_fingerprint(self))
            except OSError:
                pinned = None
        decisions = self._load_from_sources()
        self.last_load_was_full = True
        if snapshot_dir is not None and pinned is not None:
            persist(
                self,
                snapshot_dir,
                decisions,
                source=pinned[0],
                definition=pinned[1],
            )

    def _load_from_sources(self) -> list[tuple[str, bool]]:
        """Walk search paths. Return availability decisions taken during the walk."""
        decisions: dict[str, bool] = {}
        original = self._is_model_available
        if original is not None:

            def _recording(model_id: str) -> bool:
                if model_id not in decisions:
                    decisions[model_id] = bool(original(model_id))
                return decisions[model_id]

            self._is_model_available = _recording
        try:
            self._load_from_sources_body()
        finally:
            self._is_model_available = original
        return sorted(decisions.items())

    def _load_from_sources_body(self) -> None:
        self._validation_errors = []
        self._catalog_skips = []
        self._permanently_unavailable = []

        for search_path in self._search_paths:
            try:
                expanded = Path(search_path).expanduser()

                if not expanded.is_absolute():
                    resolved = (self._config_base_dir / expanded).resolve()
                else:
                    resolved = expanded.resolve()

                path_name = resolved.name or search_path.strip() or "config"
                logger.info(f"🔍 Searching pipeline path: '{search_path}' → {resolved}")

                self._loader._load_root_models(resolved, path_name)
                self._loader._load_from_search_path(resolved, path_name)
            except (OSError, ValueError) as e:
                logger.warning(f"Failed to resolve search path '{search_path}': {e}")
            except Exception:
                logger.exception(
                    f"Unexpected error processing search path '{search_path}'"
                )
                raise

        self._loader._process_deferred_pipelines()
        self._loader._merge_models()
        self._validator._validate_all_pipelines()

        if self._validation_errors:
            invalid_count = len(
                set(
                    err[1 : err.index("]")]
                    for err in self._validation_errors
                    if err.startswith("[") and "]" in err
                )
            )
            logger.error(
                f"❌ Pipeline validation failed for {invalid_count} pipeline(s) "
                f"with {len(self._validation_errors)} total error(s). "
                f"These pipelines will be unavailable. See errors below:"
            )
            for error in self._validation_errors:
                logger.error(f"  • {error}")

            removed_pipelines = self._validator._remove_invalid_pipelines()

            if removed_pipelines:
                logger.warning(
                    f"⚠️  Removed {len(removed_pipelines)} invalid pipeline(s): "
                    f"{', '.join(sorted(removed_pipelines))}"
                )

        valid_count = len(self.pipelines)
        logger.info(
            f"✅ Pipeline registry initialized: {valid_count} valid pipeline(s), "
            f"{len(self.models)} model ref(s), "
            f"{len(self.prompts)} prompt namespace(s)"
        )

    def reload_pipelines(self) -> tuple[int, int]:
        """
        Reload pipelines with current model availability.

        Called when gateway catalog changes (event-driven) OR hot-reload file change.
        Builds new state in a fresh instance, then atomically swaps dict references.

        Invariant: ∀ concurrent readers: see either the complete old state or the
        complete new state — never an empty/partial dict.

        Returns:
            Tuple of (old_count, new_count) for pipeline counts
        """
        old_pipeline_count = len(self.pipelines)
        old_model_count = len(self.models)
        old_prompt_count = len(self.prompts)
        previous_pipelines = dict(self.pipelines)

        fresh = PipelineRegistry(
            search_paths=self._search_paths,
            is_model_available=self._is_model_available,
            config_defaults=self._config_defaults,
            config_base_dir=self._config_base_dir,
            snapshot_dir=self._snapshot_dir,
        )
        fresh.load()
        _apply_keep_last_good_on_reload(previous_pipelines, fresh)

        self.pipelines = fresh.pipelines
        self.models = fresh.models
        self._root_models = fresh._root_models
        self._domain_models = fresh._domain_models
        self.prompts = fresh.prompts
        self._validation_errors = fresh._validation_errors
        self._catalog_skips = fresh._catalog_skips
        self._permanently_unavailable = fresh._permanently_unavailable

        new_pipeline_count = len(self.pipelines)
        new_model_count = len(self.models)
        new_prompt_count = len(self.prompts)

        if (
            old_pipeline_count != new_pipeline_count
            or old_model_count != new_model_count
            or old_prompt_count != new_prompt_count
        ):
            logger.info(
                f"🔄 Pipeline reload: "
                f"pipelines {old_pipeline_count}→{new_pipeline_count}, "
                f"models {old_model_count}→{new_model_count}, "
                f"prompts {old_prompt_count}→{new_prompt_count}"
            )

        return (old_pipeline_count, new_pipeline_count)

    def _should_filter_pipeline(self, pipeline: PipelineSpec) -> tuple[bool, set[str]]:
        """
        Determine if pipeline should be filtered out and return required models.

        Returns (should_filter, required_models) where:
        - should_filter = True iff required_models(pipeline) ⊄
          available_models(gateways)
        - required_models = set of model IDs needed by pipeline

        If no availability checker, returns (False, set()) (no filtering).
        """
        if self._is_model_available is None:
            return (False, set())

        def resolve_for_domain(ref: str):
            return self._accessor.get_model_config(
                ref,
                domain=pipeline.domain,
                search_path=pipeline.source_search_path,
            )

        required = get_pipeline_required_models(
            pipeline,
            resolve_model_ref=resolve_for_domain,
        )

        if not required:
            return (False, required)

        is_available = self._is_model_available
        should_filter = not are_models_available(required, is_available=is_available)

        if should_filter:
            missing = missing_models(required, is_available=is_available)
            logger.info(
                f"    🚫 Pipeline '{pipeline.id}' filtered - "
                f"missing models: {sorted(missing)}"
            )

        return (should_filter, required)

    def get_pipeline(self, pipeline_id: str) -> PipelineSpec:
        """Get pipeline by ID."""
        return self._accessor.get_pipeline(pipeline_id)

    def get_model_config(
        self,
        model_ref: str,
        *,
        domain: str | None = None,
        search_path: str | None = None,
    ):
        """Get model configuration with search-path-scoped resolution."""
        return self._accessor.get_model_config(
            model_ref, domain=domain, search_path=search_path
        )

    def get_prompt(self, prompt_ref: str):
        """Get structured prompt configuration by reference."""
        return self._accessor.get_prompt(prompt_ref)

    @property
    def catalog_skips(self) -> list[dict[str, str]]:
        """Structured drops from unknown model_ref / missing domain models.yaml."""
        return self._accessor.catalog_skips

    @property
    def unavailable_pipelines(self) -> list[tuple[str, list[str]]]:
        """Pipelines permanently dropped after retry."""
        return self._accessor.unavailable_pipelines

    def is_pipeline(self, model_id: str) -> bool:
        """Check if model_id refers to a pipeline."""
        return self._accessor.is_pipeline(model_id)
