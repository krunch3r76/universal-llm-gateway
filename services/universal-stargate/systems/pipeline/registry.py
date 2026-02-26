"""
Pipeline registry - loads and manages pipeline configurations.

Validates all configurations at load time (fail-fast).
Pipeline loading filtered by model availability across connected gateways.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Never

import yaml
from model_id import validate_model_id
from universal_logging import get_logger

from .availability import are_models_available, get_pipeline_required_models
from .core.schemas import PipelineSpec, PromptConfig, StepConfig
from .loader import resolve_sub_pipelines
from .schemas import ModelRef

logger = get_logger(__name__)


class PipelineConfigError(Exception):
    """Raised when pipeline configuration validation fails."""

    pass


def _collect_prompt_refs(
    data: dict[str, Any], prefix: str = "",
) -> list[tuple[str, str]]:
    """Recursively collect (path, value) for prompt_ref* keys."""
    refs: list[tuple[str, str]] = []
    for key, val in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if key.startswith("prompt_ref") and isinstance(val, str):
            refs.append((path, val))
        elif isinstance(val, dict):
            refs.extend(_collect_prompt_refs(val, path))
    return refs


class PipelineRegistry:
    """
    Registry for pipeline configurations.

    Loads pipelines, models, and prompts from YAML configuration.
    Validates all configurations at load time (fail-fast).

    Filtering: Pipeline p loaded ⟺ required_models(p) ⊆ available_models(gateways)
    """

    def __init__(
        self,
        search_paths: list[str] | None = None,
        get_gateway_catalogs: Callable[[], list[set[str]]] | None = None,
        config_defaults: dict[str, Any] | None = None,
        config_base_dir: Path | None = None,
    ):
        """
        Initialize registry.

        Args:
            search_paths: List of directories to search for pipelines.
                          Each path is an isolated model namespace.
                          Later paths override earlier for same pipeline ID.
                          Relative paths resolved relative to config_base_dir.
            get_gateway_catalogs: Callable returning Iterable[set[str]] of model sets
                                  from connected gateways. None disables filtering.
            config_defaults: Default pipeline options from stargate_config.yaml
                            (pipeline-specific options override these)
            config_base_dir: Base directory for resolving relative search paths.
                            Defaults to current working directory if None.
        """
        self._search_paths = search_paths or ["config"]
        self._get_gateway_catalogs = get_gateway_catalogs
        self._config_defaults = config_defaults or {}
        self._config_base_dir = config_base_dir or Path.cwd()
        self.pipelines: dict[str, PipelineSpec] = {}
        self.prompts: dict[str, Any] = {}
        self._validation_errors: list[str] = []

        # Search-path-scoped model storage (isolation: no cross-path fallback)
        # Outer key = search path name (e.g. "pipelines", "pipelines.local")
        # Root: path_name → {"qwen": ModelRef, ...}
        # Domain: path_name → {"consensus.qwen": ModelRef, ...}
        self._root_models: dict[str, dict[str, ModelRef]] = {}
        self._domain_models: dict[str, dict[str, ModelRef]] = {}

        # Flattened view for log counts (rebuilt by _merge_models)
        self.models: dict[str, ModelRef] = {}

    def load(self) -> None:
        """
        Load all configurations from search paths.

        Invariant: each search path is an isolated model namespace.
        Pipelines resolve models only from their own search path.
        Later paths override earlier for same pipeline ID only.

        Pre: search_paths ≠ ∅
        Post: pipelines ∪ models ∪ prompts loaded ∧ validated

        Raises:
            PipelineConfigError: If validation errors found
        """
        self._validation_errors = []

        for search_path in self._search_paths:
            try:
                expanded = Path(search_path).expanduser()

                if not expanded.is_absolute():
                    resolved = (self._config_base_dir / expanded).resolve()
                else:
                    resolved = expanded.resolve()

                path_name = resolved.name
                logger.info(f"🔍 Searching pipeline path: '{search_path}' → {resolved}")

                self._load_root_models(resolved, path_name)
                self._load_from_search_path(resolved, path_name)
            except Exception as e:
                logger.warning(f"Failed to resolve search path '{search_path}': {e}")

        # Merge root + domain models into unified view
        self._merge_models()

        # Validate all pipelines (non-fatal - log errors but don't block initialization)
        self._validate_all_pipelines()

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

            # Remove invalid pipelines instead of blocking initialization
            removed_pipelines = self._remove_invalid_pipelines()

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
        complete new state — never an empty/partial dict.  The clear-then-repopulate
        pattern would expose an empty window between clear() and the first add(), which
        causes "Pipeline not found" under concurrent requests (race confirmed in prod).

        Returns:
            Tuple of (old_count, new_count) for pipeline counts
        """
        old_pipeline_count = len(self.pipelines)
        old_model_count = len(self.models)
        old_prompt_count = len(self.prompts)

        # Build all new state in a fresh registry — never touches self.* during load
        fresh = PipelineRegistry(
            search_paths=self._search_paths,
            get_gateway_catalogs=self._get_gateway_catalogs,
            config_defaults=self._config_defaults,
            config_base_dir=self._config_base_dir,
        )
        fresh.load()

        # Atomic swap: reference assignment is GIL-protected (single bytecode op).
        # Readers see the complete old dict or the complete new dict — never empty.
        self.pipelines = fresh.pipelines
        self.models = fresh.models
        self._root_models = fresh._root_models
        self._domain_models = fresh._domain_models
        self.prompts = fresh.prompts
        self._validation_errors = fresh._validation_errors

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

        If no gateway catalog provider, returns (False, set()) (no filtering).

        Args:
            pipeline: Pipeline specification

        Returns:
            Tuple of (should_filter: bool, required_models: set[str])
        """
        if self._get_gateway_catalogs is None:
            return (False, set())  # No filtering when gateway catalogs unavailable

        def resolve_for_domain(ref: str) -> ModelRef:
            return self.get_model_config(
                ref,
                domain=pipeline.domain,
                search_path=pipeline.source_search_path,
            )

        required = get_pipeline_required_models(
            pipeline,
            resolve_model_ref=resolve_for_domain,
        )

        if not required:
            return (False, required)  # No model requirements = always load

        gateway_catalogs = self._get_gateway_catalogs()
        available = set().union(*gateway_catalogs) if gateway_catalogs else set()

        should_filter = not are_models_available(
            required,
            gateway_catalogs=gateway_catalogs,
        )

        # Log only when filtering occurs (missing models)
        if should_filter:
            missing = required - available
            logger.info(
                f"    🚫 Pipeline '{pipeline.id}' filtered - "
                f"missing models: {sorted(missing)}"
            )

        return (should_filter, required)

    def _validate_all_pipelines(self) -> None:
        """
        Validate all loaded pipelines.

        Checks:
        - Handler exists for (pipeline.type, step.type)
        - depends_on references valid step IDs
        - No cycles in dependencies
        - model_ref exists in models
        - prompt_ref exists in prompts
        """

        for pipeline_id, pipeline in self.pipelines.items():
            errors = self._validate_pipeline(pipeline)
            for error in errors:
                self._validation_errors.append(f"[{pipeline_id}] {error}")

    def _remove_invalid_pipelines(self) -> set[str]:
        """
        Remove pipelines with validation errors.

        Extracts pipeline IDs from validation errors and removes them from registry.
        Allows valid pipelines to remain usable despite some invalid configurations.

        Returns:
            Set of removed pipeline IDs for logging/events
        """
        invalid_pipeline_ids = set()
        for error in self._validation_errors:
            # Extract pipeline ID from error message format: "[pipeline_id] error"
            if error.startswith("[") and "]" in error:
                pipeline_id = error[1 : error.index("]")]
                invalid_pipeline_ids.add(pipeline_id)

        removed_ids = set()
        for pipeline_id in invalid_pipeline_ids:
            if pipeline_id in self.pipelines:
                del self.pipelines[pipeline_id]
                removed_ids.add(pipeline_id)
                logger.debug(f"Removed invalid pipeline from registry: {pipeline_id}")

        return removed_ids

    def _validate_pipeline(self, pipeline: PipelineSpec) -> list[str]:
        """Validate a single pipeline configuration."""
        from .core.dag import DAGBuilder
        from .core.handlers import HandlerRegistry

        errors = []
        step_ids = {step.id for step in pipeline.steps}

        for step in pipeline.steps:
            # sub_pipeline steps are expanded by the DAG builder, not executed
            # by a handler — skip handler/model/prompt checks for them
            if step.type == "sub_pipeline":
                continue

            # 1. Check handler exists (scoped to pipeline's variant)
            handler_class = HandlerRegistry.get_class(
                pipeline.type, step.type, variant=pipeline.source_variant
            )
            if handler_class is None:
                errors.append(
                    f"Step '{step.id}': No handler for type '{step.type}' "
                    f"in domain '{pipeline.type}' variant '{pipeline.source_variant}'"
                )
                continue

            # 2. Validate step config via handler (if validate method exists)
            handler = handler_class()
            if hasattr(handler, "validate"):
                step_errors = handler.validate(step)
                errors.extend(step_errors)

            # 3. Check model_ref exists
            if step.model_ref:
                # optionsNs.<key> — resolve via pipeline options before registry lookup
                model_ref_to_check = step.model_ref
                if model_ref_to_check.startswith("optionsNs."):
                    option_key = model_ref_to_check[len("optionsNs."):]
                    resolved = pipeline.options.get(option_key)
                    if resolved and isinstance(resolved, str):
                        model_ref_to_check = resolved
                    else:
                        errors.append(
                            f"Step '{step.id}': Unknown model_ref '{step.model_ref}' "
                            f"(option '{option_key}' not found in pipeline options)"
                        )
                        model_ref_to_check = None
                if model_ref_to_check:
                    try:
                        self.get_model_config(
                            model_ref_to_check,
                            domain=pipeline.domain,
                            search_path=pipeline.source_search_path,
                        )
                    except KeyError:
                        errors.append(
                            f"Step '{step.id}': Unknown model_ref '{step.model_ref}'"
                        )

            # 4. Check prompt_ref exists (skip templated refs)
            if step.prompt_ref:
                # Skip validation for dynamic/templated prompt refs (e.g., "{{optionsNs.variant}}")
                if not ("{{" in step.prompt_ref and "}}" in step.prompt_ref):
                    try:
                        self.get_prompt(step.prompt_ref)
                    except KeyError:
                        errors.append(
                            f"Step '{step.id}': Unknown prompt_ref '{step.prompt_ref}'"
                        )
                    except ValueError as e:
                        # Prompt format validation errors (e.g., generation_parameters in prompt)
                        errors.append(f"Step '{step.id}': {e}")

            # 5. Check depends_on references
            for dep_id in step.depends_on:
                if dep_id not in step_ids:
                    errors.append(
                        f"Step '{step.id}': depends_on unknown step '{dep_id}'"
                    )

            # 6. Check inputs references (for judge steps)
            if step.inputs:
                for input_id in step.inputs:
                    if input_id not in step_ids:
                        errors.append(
                            f"Step '{step.id}': input '{input_id}' is not a valid step"
                        )

        # 7. Check for cycles
        try:
            DAGBuilder(pipeline.steps, validate_only=True).build()
        except ValueError as e:
            errors.append(f"DAG error: {e}")

        # 8. Check output step exists
        # Output format: {step_name}.json.{field} or {step_name}.text
        # Extract step name (part before first '.')
        output_step_name = (
            pipeline.output.split(".")[0] if "." in pipeline.output else pipeline.output
        )
        if output_step_name not in step_ids:
            errors.append(
                f"Output step '{pipeline.output}' not found in steps (expected step name: '{output_step_name}')"
            )

        # 9. Cross-version prompt_ref isolation
        if pipeline.source_variant:
            expected_prefix = f"{pipeline.type}.{pipeline.source_variant}."
            for step in pipeline.steps:
                if step.type == "sub_pipeline":
                    continue
                all_refs: list[tuple[str, str]] = []
                if step.prompt_ref and "{{" not in step.prompt_ref:
                    all_refs.append(("prompt_ref", step.prompt_ref))
                if step.model_extra:
                    all_refs.extend(_collect_prompt_refs(step.model_extra))
                for field, ref in all_refs:
                    if "{{" in ref:
                        continue
                    if not ref.startswith(expected_prefix):
                        errors.append(
                            f"Step '{step.id}': {field} '{ref}' references "
                            f"outside version namespace '{expected_prefix[:-1]}'"
                        )

        return errors

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
            bucket = self._domain_models.setdefault(path_name, {})
            old_count = len(bucket)
            self._load_models(models_file, domain_name, path_name)
            new_count = len(bucket) - old_count
            logger.info(
                f"    📄 Loaded {new_count} model ref(s) "
                f"from {path_name}/{domain_name}/models.yaml"
            )

        # Load prompts (prompts.yaml in domain root, namespaced by domain)
        prompts_file = domain_dir / "prompts.yaml"
        if prompts_file.exists():
            self._load_domain_prompts(prompts_file, domain_name)

        # Recursively load prompts.yaml from subdirectories
        # Namespace: {domain}.{subdir} (e.g., transformation.romantic)
        for prompts_file in sorted(domain_dir.rglob("prompts.yaml")):
            # Skip root-level prompts.yaml (already loaded above)
            if prompts_file.parent == domain_dir:
                continue

            # Build namespace from path relative to search_path
            # e.g., search_path/transformation/romantic/prompts.yaml →
            # transformation.romantic
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
                # Store prompts in nested dict structure for dotted lookup
                # e.g., namespace="transformation.romantic" creates:
                # self.prompts["transformation"]["romantic"] = prompts_data
                namespace_parts = namespace.split(".")
                current = self.prompts
                for part in namespace_parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]

                current[namespace_parts[-1]] = prompts_data
                logger.info(
                    f"    📄 Loaded {len(prompts_data)} prompt group(s) "
                    f"from {namespace}/prompts.yaml"
                )
        except Exception as e:
            logger.warning(f"Failed to load prompts from {prompts_file}: {e}")

    def _load_root_models(self, search_path: Path, path_name: str) -> None:
        """
        Load root namespace models from search_path/models.yaml.

        Root models are stored with unqualified keys (e.g., "qwen"),
        scoped to the search path namespace.
        """
        root_models_file = search_path / "models.yaml"
        if not root_models_file.exists():
            return

        try:
            with open(root_models_file) as f:
                data = yaml.safe_load(f) or {}

            models_data = data.get("models", {})
            if not models_data:
                return

            bucket = self._root_models.setdefault(path_name, {})
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
        except Exception as e:
            logger.warning(f"Failed to load root models from {root_models_file}: {e}")

    def _load_models(self, models_file: Path, domain: str, path_name: str) -> None:
        """Load pipeline model references with domain namespacing into path scope."""
        with open(models_file) as f:
            data = yaml.safe_load(f)

        bucket = self._domain_models.setdefault(path_name, {})
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
        self.models = {}
        for bucket in self._root_models.values():
            self.models.update(bucket)
        for bucket in self._domain_models.values():
            self.models.update(bucket)

    def _load_pipeline(
        self,
        path: Path,
        path_name: str,
        *,
        domain_dir: Path | None = None,
    ) -> None:
        """Load a single pipeline with availability filtering."""
        try:
            with path.open() as f:
                data = yaml.safe_load(f) or {}

            pipeline_data = data.get("pipeline", data)

            # Sub-pipeline fragments (verify.yaml, veto.yaml, etc.) lack
            # the required ``version`` field — skip them silently since they
            # are loaded on-demand via pipeline_ref resolution.
            if "version" not in pipeline_data and "schema_version" not in pipeline_data:
                return

            steps = []
            for step_data in pipeline_data.get("steps", []):
                steps.append(StepConfig(**step_data))

            pipeline_data["steps"] = steps

            if self._config_defaults:
                pipeline_options = pipeline_data.get("options", {})
                merged_options = {**self._config_defaults, **pipeline_options}
                pipeline_data["options"] = merged_options

            pipeline_data["source_search_path"] = path_name

            # Derive variant from first subdirectory under domain_dir
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

            # Resolve sub-pipeline references (pipeline_ref → SubPipelineSpec)
            # so the DAG builder can expand them during validation and execution
            resolve_sub_pipelines(pipeline.steps, path.parent, visited=set())

            # Filter by model availability
            should_filter, required = self._should_filter_pipeline(pipeline)
            if should_filter:
                # Calculate actually missing models for accurate logging
                if self._get_gateway_catalogs is not None:
                    gateway_catalogs = self._get_gateway_catalogs()
                    available = (
                        set().union(*gateway_catalogs) if gateway_catalogs else set()
                    )
                    missing = required - available
                    logger.info(
                        f"    ⏭️  Skipping pipeline '{pipeline.id}' - "
                        f"missing required models: {sorted(missing)} "
                        f"(required: {len(required)}, available: {len(available)})"
                    )
                return

            self.pipelines[pipeline.id] = pipeline
            logger.info(
                f"    ✅ Loaded pipeline '{pipeline.id}' from {path_name}/{path.name}"
            )

        except Exception as e:
            logger.error(f"Failed to load pipeline from {path}: {e}")

    def get_pipeline(self, pipeline_id: str) -> PipelineSpec:
        """Get pipeline by ID."""
        if pipeline_id not in self.pipelines:
            raise KeyError(f"Pipeline '{pipeline_id}' not found")
        return self.pipelines[pipeline_id]

    def get_model_config(
        self,
        model_ref: str,
        *,
        domain: str | None = None,
        search_path: str | None = None,
    ) -> ModelRef:
        """
        Get model configuration with search-path-scoped resolution.

        Invariant: resolution restricted to models from `search_path` only.
        Each search path is an isolated namespace — no cross-path fallback.

        Resolution order (within the given search path):
        1. Domain-qualified: "{domain}.{ref}" in domain models
        2. Root namespace: "{ref}" in root models
        3. Explicit qualified: "{ref}" in domain models (cross-domain)
        4. KeyError with guidance

        Args:
            model_ref: Reference like "qwen" or "translation.qwen"
            domain: Pipeline domain for auto-resolution (e.g., "consensus")
            search_path: Search path name to scope resolution (e.g., "pipelines.local")

        Returns:
            ModelRef configuration

        Raises:
            KeyError: If model ref not found, with available options
        """
        domain_bucket = self._domain_models.get(search_path or "", {})
        root_bucket = self._root_models.get(search_path or "", {})

        if domain:
            qualified = f"{domain}.{model_ref}"
            if qualified in domain_bucket:
                return domain_bucket[qualified]

        if model_ref in root_bucket:
            return root_bucket[model_ref]

        if model_ref in domain_bucket:
            return domain_bucket[model_ref]

        self._raise_model_not_found(model_ref, domain, search_path)

    def _raise_model_not_found(
        self, model_ref: str, domain: str | None, search_path: str | None
    ) -> Never:
        """Raise KeyError with actionable guidance."""
        sp = search_path or ""
        domain_bucket = self._domain_models.get(sp, {})
        root_bucket = self._root_models.get(sp, {})

        root_refs = sorted(root_bucket.keys())
        domain_refs = (
            sorted(
                k.split(".", 1)[1] for k in domain_bucket if k.startswith(f"{domain}.")
            )
            if domain
            else []
        )
        all_domains = sorted(set(k.split(".")[0] for k in domain_bucket))

        msg_parts = [f"Model ref '{model_ref}' not found"]
        if domain:
            msg_parts.append(f" in domain '{domain}'")
        if search_path:
            msg_parts.append(f" (search path: '{search_path}')")
        msg_parts.append(".\n")

        if domain and search_path:
            msg_parts.append(
                f"  Define in: {search_path}/{domain}/models.yaml "
                f"or {search_path}/models.yaml\n"
            )
        elif domain:
            msg_parts.append(f"  Define in: {{search_path}}/{domain}/models.yaml\n")
        else:
            msg_parts.append("  Define in: {search_path}/models.yaml (root)\n")

        if domain_refs:
            msg_parts.append(f"  Available in '{domain}': {domain_refs}\n")
        if root_refs:
            msg_parts.append(f"  Available in root: {root_refs[:10]}")
            if len(root_refs) > 10:
                msg_parts.append(f" ... (+{len(root_refs) - 10} more)")
            msg_parts.append("\n")
        if all_domains:
            msg_parts.append(f"  Other domains: {all_domains}")

        raise KeyError("".join(msg_parts))

    def get_prompt(self, prompt_ref: str) -> PromptConfig:
        """
        Get structured prompt configuration by reference.

        Supports dotted notation: "domain.prompt_name" or "namespace.subkey"

        Args:
            prompt_ref: Reference like "consensus.statement_generation"

        Returns:
            PromptConfig with all prompt metadata

        Raises:
            KeyError: If prompt not found
            ValueError: If prompt uses deprecated flat string format

        Invariants:
        - ∀ ref ∈ valid_prompts: returns PromptConfig
        - ∀ ref ∉ valid_prompts: raises KeyError
        - ∀ prompt as str: raises ValueError (old format rejected)
        - ∀ prompt without 'template': raises ValueError
        """
        parts = prompt_ref.split(".")
        obj: Any = self.prompts

        for part in parts:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                raise KeyError(f"Prompt '{prompt_ref}' not found")

        # CLEAN BREAK: Reject old flat string format with clear guidance
        if isinstance(obj, str):
            raise ValueError(
                f"Prompt '{prompt_ref}' uses deprecated flat string format. "
                f"Convert to structured format with 'template', 'system_prompt', "
                f"and optional 'json_schema' fields. "
                f"Note: generation_parameters must be in step config, not prompts.yaml."
            )

        if not isinstance(obj, dict):
            raise ValueError(
                f"Prompt '{prompt_ref}' must be dict with 'template' field, "
                f"got {type(obj).__name__}"
            )

        if "template" not in obj:
            raise ValueError(
                f"Prompt '{prompt_ref}' missing required 'template' field. "
                f"Structured prompts must have at minimum: template"
            )

        # FAIL FAST: generation_parameters not allowed in prompts
        if "generation_parameters" in obj:
            raise ValueError(
                f"Prompt '{prompt_ref}' contains 'generation_parameters' field. "
                f"Generation parameters must be specified in step config (pipeline YAML), "
                f"not in prompts.yaml. Move generation_parameters to the step definition. "
                f"See: services/universal-stargate/systems/pipeline/README.md#generation_parameters-format"
            )

        return PromptConfig(
            name=prompt_ref.split(".")[-1],
            description=obj.get("description", ""),
            system_prompt=obj.get("system_prompt"),
            template=obj["template"],
            json_schema=obj.get("json_schema"),
        )

    def is_pipeline(self, model_id: str) -> bool:
        """Check if model_id refers to a pipeline."""
        return model_id in self.pipelines
