"""Replay service: re-execute individual model calls against running Stargate.

Two modes:
  1. Recorded replay: re-send exact request_body from snapshot
  2. YAML re-render: load current prompts.yaml, render template, build new request

Both send POST /v1/chat/completions to Stargate on the configured URL.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from .http_helpers import PIPELINE_TEST_HEADERS, PIPELINE_TEST_PARAMS
from .models import (
    ExecutionSnapshot,
    ModelCall,
    ReplayOverrides,
    ReplayResult,
    StepConfigMatch,
    StepSnapshot,
)

DEFAULT_STARGATE_URL = "http://localhost:9999"
_PLACEHOLDER_RE = re.compile(r"\{([\w]+(?:\.[\w]+)*)\}")


def replay_recorded(
    snapshot: ExecutionSnapshot,
    step_name: str,
    call_label: str | None = None,
    overrides: ReplayOverrides | None = None,
    pipeline_dir: Path | str | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
) -> ReplayResult:
    """Re-send the exact recorded request_body, with optional overrides.

    When pipeline_dir is provided, model and generation parameters from
    the step YAML are applied between snapshot defaults and CLI overrides.
    If None, only snapshot defaults and CLI overrides are used.

    Args:
        snapshot: Execution snapshot containing recorded data.
        step_name: Name of the step to replay.
        call_label: Optional label for a specific model call within the step.
        overrides: Optional request parameter overrides.
        pipeline_dir: Optional path to pipeline directory; enables YAML model/gen params.
        stargate_url: Stargate base URL.
        timeout: Request timeout in seconds.

    Returns:
        ReplayResult with response and metadata.
    """
    step = _get_step(snapshot, step_name)
    call = _get_call(step.model_calls, call_label)
    overrides = overrides or ReplayOverrides()

    body = dict(call.request_body)
    model_profile = _apply_step_yaml_settings(body, snapshot, step, pipeline_dir)
    _apply_overrides(body, overrides)

    return _send_request(
        body=body,
        step_name=step_name,
        call_label=call_label,
        stargate_url=stargate_url,
        timeout=timeout,
        model_profile=model_profile,
    )


def replay_rerender(
    snapshot: ExecutionSnapshot,
    step_name: str,
    call_label: str | None = None,
    overrides: ReplayOverrides | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    pipeline_dir: Path | str | None = None,
    timeout: float = 300.0,
) -> ReplayResult:
    """Re-render prompt from current YAML, build fresh request, send to Stargate.

    Falls back to recorded replay if pipeline_dir is not provided or
    prompt files cannot be found.

    Args:
        snapshot: Execution snapshot containing recorded data.
        step_name: Name of the step to replay.
        call_label: Optional label for a specific model call within the step.
        overrides: Optional request parameter overrides.
        stargate_url: Stargate base URL.
        pipeline_dir: Optional path to pipeline directory; if missing or prompts
            unfound, falls back to recorded replay.
        timeout: Request timeout in seconds.

    Returns:
        ReplayResult with response and metadata.
    """
    step = _get_step(snapshot, step_name)
    call = _get_call(step.model_calls, call_label)
    overrides = overrides or ReplayOverrides()

    if pipeline_dir is not None:
        pipeline_dir = Path(pipeline_dir)
        rendered = _try_render_from_yaml(snapshot, step, call, pipeline_dir, overrides)
        if rendered is not None:
            body = rendered
            model_profile = _apply_step_yaml_settings(body, snapshot, step, pipeline_dir)
            _apply_overrides(body, overrides)
            return _send_request(
                body=body,
                step_name=step_name,
                call_label=call_label,
                stargate_url=stargate_url,
                timeout=timeout,
                model_profile=model_profile,
            )
        print(
            f"  [warn] Could not re-render prompt for step '{step_name}' "
            f"(call={call_label!r}) from {pipeline_dir} — falling back to recorded request."
        )

    body = dict(call.request_body)
    model_profile = _apply_step_yaml_settings(body, snapshot, step, pipeline_dir)
    _apply_overrides(body, overrides)
    return _send_request(
        body=body,
        step_name=step_name,
        call_label=call_label,
        stargate_url=stargate_url,
        timeout=timeout,
        model_profile=model_profile,
    )


# ---------------------------------------------------------------------------
# Prompt rendering from YAML
# ---------------------------------------------------------------------------


def _inject_scope_options(
    pipeline_dir: Path,
    snapshot: ExecutionSnapshot,
    variables: dict[str, Any],
) -> None:
    """If pipeline YAML has options.scope_options, inject into variables for template render."""
    pipeline_id = getattr(snapshot, "pipeline_id", None) or "rag-context"
    root = _find_pipeline_root(pipeline_dir, pipeline_id)
    if root is None:
        return
    for yaml_file in root.rglob("*.yaml"):
        if yaml_file.name == "prompts.yaml":
            continue
        try:
            with yaml_file.open(encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            if config.get("id") == pipeline_id:
                opts = config.get("options") or {}
                so = opts.get("scope_options")
                if isinstance(so, str):
                    variables["scope_options"] = so
                elif isinstance(so, list):
                    variables["scope_options"] = "\n".join(f'    "{x}"' for x in so)
                break
        except Exception:
            continue


def _find_pipeline_root(pipeline_dir: Path, pipeline_id: str) -> Path | None:
    """Return the directory containing the YAML with id == pipeline_id, or None."""
    for yaml_file in pipeline_dir.rglob("*.yaml"):
        if yaml_file.name == "prompts.yaml":
            continue
        try:
            with yaml_file.open(encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            if config.get("id") == pipeline_id:
                return yaml_file.parent
        except Exception:
            continue
    return None


def _try_render_from_yaml(
    snapshot: ExecutionSnapshot,
    step: StepSnapshot,
    call: ModelCall,
    pipeline_dir: Path,
    overrides: ReplayOverrides,
) -> dict[str, Any] | None:
    """Attempt to build a request body from current prompts.yaml + step inputs."""
    prompts_path = pipeline_dir / "prompts.yaml"
    if not prompts_path.exists():
        return None

    with prompts_path.open(encoding="utf-8") as f:
        prompts_loaded = yaml.safe_load(f)
    if not isinstance(prompts_loaded, dict):
        return None
    prompts_data: dict[str, Any] = prompts_loaded

    prompts_raw = prompts_data.get("prompts", prompts_data)
    if not isinstance(prompts_raw, dict):
        return None
    prompts = prompts_raw

    prompt_name = (
        overrides.prompt_ref.rsplit(".", 1)[-1]
        if overrides.prompt_ref
        else _infer_prompt_name(call.call_label, step)
        or _resolve_prompt_from_config(
            step, call.call_label or "", pipeline_dir, snapshot
        )
    )
    if not prompt_name or prompt_name not in prompts:
        return None

    prompt_config = prompts[prompt_name]
    if not isinstance(prompt_config, dict):
        return None
    template = prompt_config.get("template", "")
    system_prompt_template = prompt_config.get("system_prompt", "")

    variables = _build_template_variables(step, call)
    # Fallback for steps that don't have inputs in snapshot (e.g. rag-context analyze_scope / generate_rewrites)
    if not variables.get("text") and getattr(call, "user_prompt", None):
        variables["text"] = call.user_prompt
    if not variables.get("scope_options"):
        _inject_scope_options(pipeline_dir, snapshot, variables)

    user_prompt = _render_template(template, variables)
    system_prompt = (
        _render_template(system_prompt_template, variables)
        if system_prompt_template
        else call.system_prompt
    )

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    body: dict[str, Any] = {
        "model": call.request_body.get("model", call.model_id),
        "messages": messages,
        "stream": False,
    }

    orig_body = call.request_body
    body.update(
        {
            p: orig_body[p]
            for p in ("temperature", "max_tokens", "top_p", "response_format")
            if p in orig_body
        }
    )
    return body


def _infer_prompt_name(call_label: str, step: StepSnapshot) -> str | None:
    """Infer which prompt to load based on call_label within an assess_loop.

    Args:
        call_label: Label of the model call (e.g. action_rewrite_0).
        step: Step snapshot (unused; kept for signature consistency).

    Returns:
        Prompt name for action_* labels (e.g. rewrite from action_rewrite_0); None otherwise.
    """
    if not call_label:
        return None
    if call_label.startswith("assess_"):
        return None
    if call_label.startswith("action_"):
        action = call_label.split("_", 1)[1] if "_" in call_label else ""
        parts = action.rsplit("_", 1)
        return parts[0] if len(parts) > 1 else action
    return None


def _find_step_config(
    step: StepSnapshot,
    pipeline_dir: Path,
    snapshot: ExecutionSnapshot | None,
) -> StepConfigMatch | None:
    """Return the pipeline + step config dict for this step, if discoverable.

    Args:
        step: Step snapshot (name may include __map_ or __ suffix).
        pipeline_dir: Root to search for pipeline YAML.
        snapshot: Optional execution snapshot for pipeline_id scoping.

    Returns:
        StepConfigMatch with pipeline_config and step_config, or None.
    """
    raw_name = step.step_name
    if "__map_" in raw_name:
        raw_name = raw_name.split("__map_", 1)[0]
    short_name = raw_name.rsplit("__", 1)[-1]
    search_dir = pipeline_dir
    if snapshot and snapshot.pipeline_id:
        root = _find_pipeline_root(pipeline_dir, snapshot.pipeline_id)
        if root is not None:
            search_dir = root
        else:
            print(
                f"  [warn] No YAML with id '{snapshot.pipeline_id}' under {pipeline_dir}; "
                "using full directory for step config lookup."
            )
    for yaml_file in search_dir.rglob("*.yaml"):
        if yaml_file.name in ("prompts.yaml", "models.yaml"):
            continue
        try:
            with yaml_file.open(encoding="utf-8") as f:
                config_loaded = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(config_loaded, dict):
            continue
        config: dict[str, Any] = config_loaded
        steps = config.get("steps", [])
        if not isinstance(steps, list):
            continue
        for step_cfg in steps:
            if not isinstance(step_cfg, dict):
                continue
            if step_cfg.get("name") == short_name:
                return StepConfigMatch(pipeline_config=config, step_config=step_cfg)
    return None


def _resolve_prompt_from_config(
    step: StepSnapshot,
    call_label: str,
    pipeline_dir: Path,
    snapshot: ExecutionSnapshot | None = None,
) -> str | None:
    """Find a prompt name from the step's YAML config.

    Handles three patterns:
      assess_N          -> step-level prompt_ref (the assess prompt)
      action_X_N        -> already handled by _infer_prompt_name
      plain single-call -> step-level prompt_ref (fallback for simple steps)
      other             -> {call_label}_prompt_ref domain field
    """
    match = _find_step_config(step, pipeline_dir, snapshot)
    if match is None:
        return None
    step_cfg = match.step_config
    if call_label.startswith("assess_"):
        ref = step_cfg.get("prompt_ref", "")
        return ref.rsplit(".", 1)[-1] if ref else None
    for key, val in step_cfg.items():
        if not key.endswith("_prompt_ref") or not isinstance(val, str):
            continue
        prefix = key.removesuffix("_prompt_ref")
        if call_label == prefix or call_label.startswith(prefix + "_"):
            return val.rsplit(".", 1)[-1]
    ref = step_cfg.get("prompt_ref", "")
    return ref.rsplit(".", 1)[-1] if ref else None


# ---------------------------------------------------------------------------
# Step YAML config resolution (model + generation parameters)
# ---------------------------------------------------------------------------

_ALLOWED_GEN_PARAMS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "stop",
        "response_format",
    }
)


def _resolve_namespaced_value(ref: str, pipeline_config: dict[str, Any]) -> Any:
    """Resolve optionsNs.* references against the pipeline's options block.

    Args:
        ref: Reference string (e.g. optionsNs.rerank_model).
        pipeline_config: Full pipeline config containing options.

    Returns:
        Resolved value from options, or ref unchanged if not optionsNs.*.
    """
    if not isinstance(ref, str):
        return ref
    if ref.startswith("optionsNs."):
        options = pipeline_config.get("options", {})
        path = ref.split(".", 1)[1] if "." in ref else ""
        return _resolve_path(path, options) if path else options
    return ref


def _resolve_model_from_config(
    match: StepConfigMatch, pipeline_dir: Path
) -> str | None:
    """Resolve model_ref from step config through namespace + alias lookup.

    Args:
        match: StepConfigMatch with pipeline and step config.
        pipeline_dir: Used for models.yaml alias resolution.

    Returns:
        Resolved model ID string, or None if unset or not a string.
    """
    ref = match.step_config.get("model_ref") or match.step_config.get("model")
    if not ref:
        return None
    resolved = _resolve_namespaced_value(ref, match.pipeline_config)
    if not isinstance(resolved, str):
        return None
    model_id = resolve_model_alias(resolved, pipeline_dir)
    if model_id != resolved:
        print(f"  YAML model: '{ref}' -> '{resolved}' -> '{model_id}'")
    elif ref != resolved:
        print(f"  YAML model: '{ref}' -> '{resolved}'")
    return model_id


def _apply_step_yaml_settings(
    body: dict[str, Any],
    snapshot: ExecutionSnapshot,
    step: StepSnapshot,
    pipeline_dir: Path | str | None,
) -> str | None:
    """Overlay model + generation parameters from step YAML onto request body.

    Applied between snapshot defaults and CLI overrides in the precedence chain.

    Returns:
        The model profile string (e.g. 'qwen3-instruct') if one is declared in
        models.yaml for this step's model alias, or None if absent.  Callers use
        this to decide between ?filter=<profile> and ?disable_profile=true.
    """
    if pipeline_dir is None:
        return None
    pipeline_dir = Path(pipeline_dir)
    is_map_step = "__map_" in step.step_name
    if is_map_step:
        print(
            f"  [warn] Map step detected: '{step.step_name}'. "
            "Preserving recorded per-iteration model; applying only generation parameters."
        )
    match = _find_step_config(step, pipeline_dir, snapshot)
    if match is None:
        return None
    model_profile: str | None = None
    if not is_map_step:
        yaml_model = _resolve_model_from_config(match, pipeline_dir)
        if yaml_model:
            body["model"] = yaml_model
        ref = match.step_config.get("model_ref") or match.step_config.get("model")
        if ref:
            resolved_alias = _resolve_namespaced_value(ref, match.pipeline_config)
            if isinstance(resolved_alias, str):
                model_profile = resolve_model_profile(resolved_alias, pipeline_dir)
    gen_params = match.step_config.get("generation_parameters")
    if isinstance(gen_params, dict):
        for key, value in gen_params.items():
            if key not in _ALLOWED_GEN_PARAMS:
                print(f"  [warn] Ignoring unsupported generation parameter: {key}")
                continue
            body[key] = value
    return model_profile


def _build_template_variables(step: StepSnapshot, call: ModelCall) -> dict[str, Any]:
    """Build template substitution context from step inputs only.

    Re-render must use the same inputs the live pipeline would use; do not
    inject the previous render's system_prompt/user_prompt or templates
    may incorrectly reuse prior output.

    Args:
        step: Step snapshot (inputs used for placeholders).
        call: Model call (unused; kept for signature consistency).

    Returns:
        Dict of variable names to values for template rendering.
    """
    if len(step.model_calls) > 1:
        print(
            f"  [warn] Step '{step.step_name}' has multiple model calls. "
            "Re-rendering from step-level inputs may be incorrect for this call. "
            "The snapshot does not contain per-call template variables."
        )
    variables: dict[str, Any] = {}
    variables.update(step.inputs)
    return variables


def _render_template(template: str, variables: dict[str, Any]) -> str:
    """Regex-based template rendering matching PromptBuilder behavior.

    Args:
        template: String with {path.to.key} placeholders.
        variables: Context for resolution (nested dicts/objects).

    Returns:
        Template with placeholders replaced; unresolved left as-is.
    """

    def replacer(match: re.Match[str]) -> str:
        path = match.group(1)
        value = _resolve_path(path, variables)
        return str(value) if value is not None else match.group(0)

    return _PLACEHOLDER_RE.sub(replacer, template)


def _resolve_path(path: str, context: dict[str, Any]) -> Any | None:
    """Navigate dot-separated path through nested dicts/objects.

    Args:
        path: Dot-separated key path (e.g. options.rerank_model).
        context: Root dict or object for traversal.

    Returns:
        Value at path, or None if any segment is missing.
    """
    parts = path.split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return None
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# HTTP / request helpers
# ---------------------------------------------------------------------------


def _send_request(
    body: dict[str, Any],
    step_name: str,
    call_label: str | None,
    stargate_url: str,
    timeout: float,
    model_profile: str | None = None,
) -> ReplayResult:
    """POST to /v1/chat/completions and return a ReplayResult.

    ∀ call: if model_profile is set → ?filter=<profile> (chat template applied).
    Otherwise → ?disable_profile=true (mirrors pipeline default for models without
    an explicit profile: entry in models.yaml).

    Args:
        body: Request body (model, messages, stream=False, etc.).
        step_name: Step name for result metadata.
        call_label: Optional call label for result metadata.
        stargate_url: Base URL of Stargate.
        timeout: Request timeout in seconds.
        model_profile: Profile name from models.yaml, or None.

    Returns:
        ReplayResult with response text, usage, and latency.
    """
    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"
    params: dict[str, str] = dict(PIPELINE_TEST_PARAMS)
    if model_profile:
        params["filter"] = model_profile
        print(f"  profile: {model_profile} (?filter={model_profile})")
    else:
        params["disable_profile"] = "true"

    start = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            url, json=body, params=params, headers=PIPELINE_TEST_HEADERS
        )
    elapsed_ms = (time.monotonic() - start) * 1000

    resp.raise_for_status()
    data = resp.json()

    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = data.get("usage", {})

    return ReplayResult(
        step_name=step_name,
        call_label=call_label,
        model_id=data.get("model", body.get("model", "")),
        response_text=message.get("content", ""),
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        latency_ms=elapsed_ms,
        request_body=body,
    )


def resolve_model_alias(alias: str, pipeline_dir: Path | str | None) -> str:
    """Resolve a pipeline model alias (e.g. 'phi4') to a real model ID.

    Searches for models.yaml in the pipeline dir and its parents (up to 3 levels).
    Returns the alias unchanged if no mapping is found.
    """
    if pipeline_dir is None:
        return alias

    search = Path(pipeline_dir)
    for _ in range(4):
        candidate = search / "models.yaml"
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            models = data.get("models", {})
            if alias in models:
                return models[alias].get("model", alias)
            # Alias not in this file; continue searching parent directories.
        if search.parent == search:
            break
        search = search.parent
    return alias


def resolve_model_profile(alias: str, pipeline_dir: Path | str | None) -> str | None:
    """Resolve the profile field for a pipeline model alias from models.yaml.

    ∀ alias ∈ models.yaml: returns profile string if present, else None.
    Callers use None as the signal to send ?disable_profile=true.

    Searches for models.yaml in the pipeline dir and its parents (up to 3 levels).
    """
    if pipeline_dir is None:
        return None

    search = Path(pipeline_dir)
    for _ in range(4):
        candidate = search / "models.yaml"
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            models = data.get("models", {})
            if alias in models:
                return models[alias].get("profile")
        if search.parent == search:
            break
        search = search.parent
    return None


def _apply_overrides(body: dict[str, Any], overrides: ReplayOverrides) -> None:
    """Mutate request body with CLI overrides.

    Args:
        body: Request body to modify in place.
        overrides: Model, temperature, max_tokens, extra_params to apply.
    """
    if overrides.model:
        body["model"] = overrides.model
    if overrides.temperature is not None:
        body["temperature"] = overrides.temperature
    if overrides.max_tokens is not None:
        body["max_tokens"] = overrides.max_tokens
    for k, v in overrides.extra_params.items():
        body[k] = v


def _get_step(snapshot: ExecutionSnapshot, step_name: str) -> StepSnapshot:
    """Resolve step by full or short name.

    Args:
        snapshot: Execution snapshot containing steps.
        step_name: Full step name or short suffix (must be unambiguous).

    Returns:
        StepSnapshot for the step.

    Raises:
        KeyError: Step not found. ValueError: Multiple matches.
    """
    if step_name in snapshot.steps:
        return snapshot.steps[step_name]

    matches = [
        s
        for s in snapshot.steps.values()
        if s.step_name.endswith(f"__{step_name}") or s.step_name == step_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = [m.step_name for m in matches]
        raise ValueError(
            f"Ambiguous step name '{step_name}' matches: {names}. Use full name."
        )
    raise KeyError(
        f"Step '{step_name}' not found. Available: {list(snapshot.steps.keys())}"
    )


def _get_call(model_calls: list[ModelCall], call_label: str | None) -> ModelCall:
    """Find a specific model call by label, or return the first one.

    Args:
        model_calls: List of model calls in the step.
        call_label: Optional label; if None, returns the first call.

    Returns:
        Matching ModelCall.

    Raises:
        ValueError: No model calls. KeyError: call_label not found.
    """
    if not model_calls:
        raise ValueError("Step has no model calls to replay")

    if call_label is None:
        return model_calls[0]

    for call in model_calls:
        if call.call_label == call_label:
            return call

    labels = [c.call_label for c in model_calls]
    raise KeyError(f"Call '{call_label}' not found. Available: {labels}")


def save_replay_result(result: ReplayResult, path: Path | str) -> None:
    """Save replay result to JSON for later comparison."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict

    path.write_text(json.dumps(asdict(result), indent=2, default=str), encoding="utf-8")


def load_replay_result(path: Path | str) -> ReplayResult:
    """Load a saved ReplayResult from JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReplayResult(**data)
