"""Request-time step enablement for any pipeline.

YAML ``enabled`` is the base. A request may set
``pipeline_options.step_overrides.<step>.enabled`` or
``pipeline_options.skip_steps``. Enable and skip on the same step is a
caller error before the run. An unknown step name is a caller error.
Disabling a step with ``allow_disable: false`` is a caller error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .execution.errors.pipeline_error import PipelineError
from .schemas import PipelineSpec, StepConfig

BANNED_REQUEST_FIELDS: frozenset[str] = frozenset(
    {"hyde_enabled", "rerank_enabled", "catalog_retry_enabled"}
)

LEGACY_ENABLE_FLAGS: dict[str, str] = {
    "hyde_enabled": "generate_hyde",
    "rerank_enabled": "rerank",
    "catalog_retry_enabled": "fetch_scope_catalog_retry",
}


@dataclass
class StepCallerError(PipelineError):
    """Non-retryable caller error (unknown step or refused disable)."""

    step_name: str
    detail: str

    def __str__(self) -> str:
        target = self.step_name or "(request)"
        return f"caller error for step {target}: {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": "StepCallerError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "detail": self.detail,
        }


@dataclass
class StepDefinitionError(PipelineError):
    """Non-retryable definition error that names the step."""

    step_name: str
    detail: str

    def __str__(self) -> str:
        return f"definition error for step {self.step_name}: {self.detail}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": "StepDefinitionError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "detail": self.detail,
        }


def failure_is_retryable(exc: BaseException) -> bool:
    """Caller and definition errors are not retryable. Transport errors are."""
    if isinstance(exc, (StepCallerError, StepDefinitionError)):
        return False
    return bool(getattr(exc, "retryable", False))


RAG_CONTEXT_STEP_CONTROLS_ERROR = "step controls apply to rag-search only"


def finalize_relay_pipeline_options(
    relay_target: str,
    options: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Prepare ``pipeline_options`` for a Stargate relay target.

    ``rag-context`` keeps ``hyde_enabled`` / ``rerank_enabled`` /
    ``catalog_retry_enabled`` as raw keys and rejects ``step_overrides`` or
    ``skip_steps``. ``rag-search`` folds legacy enable flags into
    ``step_overrides``.
    """
    if relay_target == "rag-context":
        if options.get("step_overrides") or options.get("skip_steps"):
            return None, RAG_CONTEXT_STEP_CONTROLS_ERROR
        return dict(options), None
    if relay_target == "rag-search":
        return fold_legacy_enable_flags(options), None
    return dict(options), None


def fold_legacy_enable_flags(options: dict[str, Any]) -> dict[str, Any]:
    """Map hyde/rerank/catalog_retry flags onto step_overrides.enabled, then drop the legacy keys."""
    folded = dict(options)
    overrides = dict(folded.get("step_overrides") or {})
    for flag, step_name in LEGACY_ENABLE_FLAGS.items():
        if flag not in folded:
            continue
        enabled = bool(folded.pop(flag))
        current = dict(overrides.get(step_name) or {})
        current["enabled"] = enabled
        overrides[step_name] = current
    if overrides:
        folded["step_overrides"] = overrides
    elif "step_overrides" in folded and not folded["step_overrides"]:
        folded.pop("step_overrides", None)
    return folded


def apply_request_step_controls(
    pipeline: PipelineSpec,
    steps: list[StepConfig],
    runtime_options: dict[str, Any],
) -> list[StepConfig]:
    """Apply step_overrides and skip_steps for any pipeline."""
    if pipeline.id == "rag-search":
        for banned in BANNED_REQUEST_FIELDS:
            if banned in runtime_options:
                raise StepCallerError(
                    "",
                    f"option {banned} is rejected; use step_overrides",
                )

    by_name = {step.id: step for step in steps}
    overrides = runtime_options.get("step_overrides") or {}
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise StepCallerError("", "step_overrides must be an object")
    skip = runtime_options.get("skip_steps") or []
    if not isinstance(skip, list):
        raise StepCallerError("", "skip_steps must be a list")

    enabled_by_step: dict[str, bool] = {}
    for step_name, payload in overrides.items():
        if step_name not in by_name:
            raise StepCallerError(str(step_name), "unknown step in step_overrides")
        if not isinstance(payload, dict) or "enabled" not in payload:
            raise StepCallerError(str(step_name), "step_overrides entry needs enabled")
        enabled_by_step[str(step_name)] = bool(payload["enabled"])

    for step_name in skip:
        if step_name not in by_name:
            raise StepCallerError(str(step_name), "unknown step in skip_steps")
        if enabled_by_step.get(str(step_name)) is True:
            raise StepCallerError(
                str(step_name),
                "enabled in step_overrides and listed in skip_steps",
            )
        enabled_by_step[str(step_name)] = False

    updated: list[StepConfig] = []
    for step in steps:
        if step.id not in enabled_by_step:
            updated.append(step)
            continue
        want = enabled_by_step[step.id]
        if step.type == "sub_pipeline":
            raise StepCallerError(
                step.id,
                "sub_pipeline steps cannot be toggled; they expand after step controls",
            )
        base = step.get_domain_field("enabled", True)
        if want is False and not step.get_domain_field("allow_disable", False):
            raise StepCallerError(step.id, "allow_disable is false")
        if bool(base) == want and "enabled" not in (step.model_extra or {}):
            updated.append(step)
            continue
        data = step.model_dump()
        data["enabled"] = want
        updated.append(StepConfig.model_validate(data))
    return updated
