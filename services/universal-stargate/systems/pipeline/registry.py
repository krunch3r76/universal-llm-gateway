"""
Pipeline registry - loads and manages pipeline configurations.

Validates all configurations at load time (fail-fast).
Pipeline loading filtered by model availability across connected gateways.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

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
            search_paths: List of directories to search for pipelines
                          (later overrides earlier). Relative paths resolved relative to config_base_dir.
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

        # Two-tier model alias storage
        # Root: "qwen" → ModelRef (pipelines.local/models.yaml)
        # Domain: "consensus.qwen" → ModelRef (pipelines.local/{domain}/models.yaml)
        self._root_models: dict[str, ModelRef] = {}
        self._domain_models: dict[str, ModelRef] = {}

        # Unified view for backward compatibility (domain overrides root)
        self.models: dict[str, ModelRef] = {}

    def load(self) -> None:
        """
        Load all configurations from search paths.

        Invariant: ∀ path ∈ search_paths, later paths override earlier
        Pre: search_paths ≠ ∅
        Post: pipelines ∪ models ∪ prompts loaded ∧ validated

        Validation is always enabled (fail-fast).
        Later paths override earlier for same pipeline ID.

        Raises:
            PipelineConfigError: If validation errors found
        """
        self._validation_errors = []

        # Load from all search paths (later override earlier)
        for search_path in self._search_paths:
            try:
                # Expand user home directory (~)
                expanded = Path(search_path).expanduser()

                # Resolve relative paths relative to config_base_dir
                # Absolute paths and paths starting with ~ are left as-is
                if not expanded.is_absolute():
                    resolved = (self._config_base_dir / expanded).resolve()
                else:
                    resolved = expanded.resolve()

                logger.info(f"🔍 Searching pipeline path: '{search_path}' → {resolved}")

                # Load root models first (pipelines.local/models.yaml)
                self._load_root_models(resolved)

                # Load domains (which loads domain-specific models.yaml)
                self._load_from_search_path(resolved)
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
        Clears existing pipelines/models/prompts and reloads from disk.

        Returns:
            Tuple of (old_count, new_count) for pipeline counts
        """
        # Clear existing state
        old_pipeline_count = len(self.pipelines)
        old_model_count = len(self.models)
        old_prompt_count = len(self.prompts)

        self.pipelines.clear()
        self.models.clear()
        self._root_models.clear()
        self._domain_models.clear()
        self.prompts.clear()
        self._validation_errors = []

        # Reload everything from all search paths
        for search_path in self._search_paths:
            try:
                # Expand user home directory (~)
                expanded = Path(search_path).expanduser()

                # Resolve relative paths relative to config_base_dir
                # Absolute paths and paths starting with ~ are left as-is
                if not expanded.is_absolute():
                    resolved = (self._config_base_dir / expanded).resolve()
                else:
                    resolved = expanded.resolve()

                if resolved.exists():
                    # Load root models first
                    self._load_root_models(resolved)
                    # Load domains
                    self._load_from_search_path(resolved)
            except Exception as e:
                logger.warning(f"Failed to resolve search path '{search_path}': {e}")

        # Merge root + domain models
        self._merge_models()

        # Re-validate
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
                f"❌ Pipeline validation failed during reload for {invalid_count} pipeline(s) "
                f"with {len(self._validation_errors)} total error(s):"
            )
            for error in self._validation_errors:
                logger.error(f"  • {error}")

            removed_pipelines = self._remove_invalid_pipelines()
            if removed_pipelines:
                logger.warning(
                    f"⚠️  Removed {len(removed_pipelines)} invalid pipeline(s) during reload: "
                    f"{', '.join(sorted(removed_pipelines))}"
                )

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

        # Create domain-bound resolver
        def resolve_for_domain(ref: str) -> ModelRef:
            return self.get_model_config(ref, domain=pipeline.domain)

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

            # 1. Check handler exists
            handler_class = HandlerRegistry.get_class(pipeline.type, step.type)
            if handler_class is None:
                errors.append(
                    f"Step '{step.id}': No handler for type '{step.type}' "
                    f"in domain '{pipeline.type}'"
                )
                continue

            # 2. Validate step config via handler (if validate method exists)
            handler = handler_class()
            if hasattr(handler, "validate"):
                step_errors = handler.validate(step)
                errors.extend(step_errors)

            # 3. Check model_ref exists
            if step.model_ref:
                # TRY domain-aware lookup instead of direct dict check
                try:
                    self.get_model_config(step.model_ref, domain=pipeline.domain)
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

        return errors

    def _load_from_search_path(self, search_path: Path) -> None:
        """Load all domains from a search path."""
        if not search_path.exists():
            logger.info(f"⚠️  Search path does not exist: {search_path}")
            return

        logger.info(f"📂 Loading from: {search_path}")

        # Load each domain directory
        for domain_dir in sorted(search_path.iterdir()):
            if (
                domain_dir.is_dir()
                and not domain_dir.name.startswith(".")
                and domain_dir.name != "__pycache__"
            ):
                self._load_domain(domain_dir, search_path)

    def _load_domain(self, domain_dir: Path, search_path: Path) -> None:
        """
        Load all components of a domain.

        Structure:
            {domain}/
                *.yaml              - Pipeline specs (root level)
                prompts.yaml        - Domain prompts (root level, namespaced by domain)
                models.yaml         - Domain model refs
                handlers/           - Domain handlers (loaded separately)
                {subdir}/
                    prompts.yaml    - Subdomain prompts (namespaced as domain.subdir)
                    *.yaml          - Pipeline specs in subdirectory
        """
        domain_name = domain_dir.name
        logger.info(f"  📁 Loading domain: {domain_name}")

        # Load models (models.yaml in domain root)
        models_file = domain_dir / "models.yaml"
        if models_file.exists():
            old_count = len(self._domain_models)
            self._load_models(models_file, domain_name)
            new_count = len(self._domain_models) - old_count
            logger.info(
                f"    📄 Loaded {new_count} model ref(s) from {domain_name}/models.yaml"
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

        # Load pipeline specs
        # Exclude: prompts.yaml, models.yaml, categories.yaml
        # Recursively search subdirectories
        for yaml_file in sorted(domain_dir.rglob("*.yaml")):
            excluded = ("prompts.yaml", "models.yaml", "categories.yaml")
            if yaml_file.name not in excluded:
                self._load_pipeline(yaml_file)

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

    def _load_root_models(self, search_path: Path) -> None:
        """
        Load root namespace models from search_path/models.yaml.

        Root models are stored with unqualified keys (e.g., "qwen").
        Domain models can override these by defining the same key.
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

            for ref_name, ref_config in models_data.items():
                model_id = ref_config.get("model")
                if not model_id:
                    raise PipelineConfigError(
                        f"Root model ref '{ref_name}' missing 'model' field"
                    )

                # Validate model ID format
                error = validate_model_id(model_id)
                if error:
                    raise PipelineConfigError(
                        f"Root model ref '{ref_name}' has invalid model ID: {error}"
                    )

                # Store WITHOUT domain prefix (root namespace)
                self._root_models[ref_name] = ModelRef(**ref_config)

            logger.info(
                f"  📄 Loaded {len(models_data)} root model ref(s) "
                f"from {search_path.name}/models.yaml"
            )
        except PipelineConfigError:
            raise
        except Exception as e:
            logger.warning(f"Failed to load root models from {root_models_file}: {e}")

    def _load_models(self, models_file: Path, domain: str) -> None:
        """Load pipeline model references with domain namespacing."""
        with open(models_file) as f:
            data = yaml.safe_load(f)

        for ref_name, ref_config in data.get("models", {}).items():
            model_id = ref_config.get("model")
            if not model_id:
                raise PipelineConfigError(
                    f"Model ref '{domain}.{ref_name}' missing 'model' field"
                )

            # Validate model ID format
            error = validate_model_id(model_id)
            if error:
                raise PipelineConfigError(
                    f"Model ref '{domain}.{ref_name}' has invalid model ID: {error}"
                )

            # Namespace by domain, store in domain models
            qualified_ref = f"{domain}.{ref_name}"
            self._domain_models[qualified_ref] = ModelRef(**ref_config)

    def _merge_models(self) -> None:
        """
        Merge root and domain models into unified view.

        Domain models override root models with the same base name.
        Called after all loading is complete.
        """
        # Start with root models
        self.models = dict(self._root_models)
        # Add domain models (may have same keys as root - that's fine, domain wins on lookup)
        self.models.update(self._domain_models)

    def _load_pipeline(self, path: Path) -> None:
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

            # Parse steps
            steps = []
            for step_data in pipeline_data.get("steps", []):
                steps.append(StepConfig(**step_data))

            pipeline_data["steps"] = steps

            # Merge config defaults with pipeline-specific options
            # Pipeline-specific options take precedence (override defaults)
            if self._config_defaults:
                pipeline_options = pipeline_data.get("options", {})
                merged_options = {**self._config_defaults, **pipeline_options}
                pipeline_data["options"] = merged_options

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
            logger.info(f"    ✅ Loaded pipeline '{pipeline.id}' from {path.name}")

        except Exception as e:
            logger.error(f"Failed to load pipeline from {path}: {e}")

    def get_pipeline(self, pipeline_id: str) -> PipelineSpec:
        """Get pipeline by ID."""
        if pipeline_id not in self.pipelines:
            raise KeyError(f"Pipeline '{pipeline_id}' not found")
        return self.pipelines[pipeline_id]

    def get_model_config(
        self, model_ref: str, *, domain: str | None = None
    ) -> ModelRef:
        """
        Get model configuration with two-tier resolution.

        Resolution order:
        1. Domain-qualified: "{domain}.{ref}" in _domain_models
        2. Root namespace: "{ref}" in _root_models
        3. Explicit qualified: "{ref}" in _domain_models (cross-domain access)
        4. KeyError with helpful guidance

        Args:
            model_ref: Reference like "qwen" or "translation.qwen"
            domain: Pipeline domain for auto-resolution (e.g., "consensus")

        Returns:
            ModelRef configuration

        Raises:
            KeyError: If model ref not found, with available options
        """
        # 1. Try domain-local resolution (domain.ref in _domain_models)
        if domain:
            qualified = f"{domain}.{model_ref}"
            if qualified in self._domain_models:
                return self._domain_models[qualified]

        # 2. Try root namespace (ref in _root_models)
        if model_ref in self._root_models:
            return self._root_models[model_ref]

        # 3. Try as explicit qualified ref (e.g., "translation.qwen" in _domain_models)
        if model_ref in self._domain_models:
            return self._domain_models[model_ref]

        # 4. Not found - provide helpful error with guidance
        self._raise_model_not_found(model_ref, domain)

    def _raise_model_not_found(self, model_ref: str, domain: str | None) -> None:
        """Raise KeyError with actionable guidance."""
        # Collect available refs by category
        root_refs = sorted(self._root_models.keys())
        domain_refs = (
            sorted(
                k.split(".", 1)[1]
                for k in self._domain_models
                if k.startswith(f"{domain}.")
            )
            if domain
            else []
        )
        all_domains = sorted(set(k.split(".")[0] for k in self._domain_models))

        # Build helpful message
        msg_parts = [f"Model ref '{model_ref}' not found"]
        if domain:
            msg_parts.append(f" in domain '{domain}'")
        msg_parts.append(".\n")

        # Suggest where to define
        if domain:
            msg_parts.append(
                f"  Define in: pipelines.local/{domain}/models.yaml "
                f"or pipelines.local/models.yaml\n"
            )
        else:
            msg_parts.append(
                "  Define in: pipelines.local/models.yaml (root namespace)\n"
            )

        # Show available
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
