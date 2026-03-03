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

from .models import ExecutionSnapshot, ReplayOverrides, ReplayResult, StepConfigMatch

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
    """
    step = _get_step(snapshot, step_name)
    call = _get_call(step.model_calls, call_label)
    overrides = overrides or ReplayOverrides()

    body = dict(call.request_body)
    _apply_step_yaml_settings(body, snapshot, step, pipeline_dir)
    _apply_overrides(body, overrides)

    return _send_request(
        body=body,
        step_name=step_name,
        call_label=call_label,
        stargate_url=stargate_url,
        timeout=timeout,
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
    """
    step = _get_step(snapshot, step_name)
    call = _get_call(step.model_calls, call_label)
    overrides = overrides or ReplayOverrides()

    if pipeline_dir is not None:
        pipeline_dir = Path(pipeline_dir)
        rendered = _try_render_from_yaml(snapshot, step, call, pipeline_dir, overrides)
        if rendered is not None:
            body = rendered
            _apply_step_yaml_settings(body, snapshot, step, pipeline_dir)
            _apply_overrides(body, overrides)
            return _send_request(
                body=body,
                step_name=step_name,
                call_label=call_label,
                stargate_url=stargate_url,
                timeout=timeout,
            )
        print(
            f"  [warn] Could not re-render prompt for step '{step_name}' "
            f"(call={call_label!r}) from {pipeline_dir} — falling back to recorded request."
        )

    body = dict(call.request_body)
    _apply_step_yaml_settings(body, snapshot, step, pipeline_dir)
    _apply_overrides(body, overrides)
    return _send_request(
        body=body,
        step_name=step_name,
        call_label=call_label,
        stargate_url=stargate_url,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Prompt rendering from YAML
# ---------------------------------------------------------------------------


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
    step: Any,
    call: Any,
    pipeline_dir: Path,
    overrides: ReplayOverrides,
) -> dict[str, Any] | None:
    """Attempt to build a request body from current prompts.yaml + step inputs."""
    prompts_path = pipeline_dir / "prompts.yaml"
    if not prompts_path.exists():
        return None

    with prompts_path.open(encoding="utf-8") as f:
        prompts_data = yaml.safe_load(f) or {}

    prompts = prompts_data.get("prompts", prompts_data)

    prompt_ref = overrides.prompt_ref
    if prompt_ref:
        parts = prompt_ref.rsplit(".", 1)
        prompt_name = parts[-1]
    else:
        prompt_name = _infer_prompt_name(call.call_label, step)
        if prompt_name is None:
            prompt_name = _resolve_prompt_from_config(
                step, call.call_label or "", pipeline_dir, snapshot
            )

    if not prompt_name or prompt_name not in prompts:
        return None

    prompt_config = prompts[prompt_name]
    template = prompt_config.get("template", "")
    system_prompt_template = prompt_config.get("system_prompt", "")

    variables = _build_template_variables(step, call)

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
    for param in ("temperature", "max_tokens", "top_p", "response_format"):
        if param in orig_body:
            body[param] = orig_body[param]

    return body


def _infer_prompt_name(call_label: str, step: Any) -> str | None:
    """Infer which prompt to load based on call_label within an assess_loop."""
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
    step: Any,
    pipeline_dir: Path,
    snapshot: ExecutionSnapshot | None,
) -> StepConfigMatch | None:
    """Return the pipeline + step config dict for this step, if discoverable."""
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
                config = yaml.safe_load(f) or {}
        except Exception:
            continue
        for step_cfg in config.get("steps", []):
            if step_cfg.get("name") == short_name:
                return StepConfigMatch(pipeline_config=config, step_config=step_cfg)
    return None


def _resolve_prompt_from_config(
    step: Any,
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
    """Resolve optionsNs.* references against the pipeline's options block."""
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
    """Resolve model_ref from step config through namespace + alias lookup."""
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
    step: Any,
    pipeline_dir: Path | str | None,
) -> None:
    """Overlay model + generation parameters from step YAML onto request body.

    Applied between snapshot defaults and CLI overrides in the precedence chain.
    """
    if pipeline_dir is None:
        return
    pipeline_dir = Path(pipeline_dir)
    is_map_step = "__map_" in step.step_name
    if is_map_step:
        print(
            f"  [warn] Map step detected: '{step.step_name}'. "
            "Preserving recorded per-iteration model; applying only generation parameters."
        )
    match = _find_step_config(step, pipeline_dir, snapshot)
    if match is None:
        return
    if not is_map_step:
        yaml_model = _resolve_model_from_config(match, pipeline_dir)
        if yaml_model:
            body["model"] = yaml_model
    gen_params = match.step_config.get("generation_parameters")
    if isinstance(gen_params, dict):
        for key, value in gen_params.items():
            if key not in _ALLOWED_GEN_PARAMS:
                print(f"  [warn] Ignoring unsupported generation parameter: {key}")
                continue
            body[key] = value


def _build_template_variables(step: Any, call: Any) -> dict[str, Any]:
    """Build template substitution context from step inputs only.

    Re-render must use the same inputs the live pipeline would use; do not
    inject the previous render's system_prompt/user_prompt or templates
    may incorrectly reuse prior output.
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
    """Regex-based template rendering matching PromptBuilder behavior."""

    def replacer(match: re.Match[str]) -> str:
        path = match.group(1)
        value = _resolve_path(path, variables)
        return str(value) if value is not None else match.group(0)

    return _PLACEHOLDER_RE.sub(replacer, template)


def _resolve_path(path: str, context: dict[str, Any]) -> Any | None:
    """Navigate dot-separated path through nested dicts/objects."""
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
) -> ReplayResult:
    """POST to /v1/chat/completions and return a ReplayResult."""
    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"

    start = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body)
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


def _apply_overrides(body: dict[str, Any], overrides: ReplayOverrides) -> None:
    """Mutate request body with CLI overrides."""
    if overrides.model:
        body["model"] = overrides.model
    if overrides.temperature is not None:
        body["temperature"] = overrides.temperature
    if overrides.max_tokens is not None:
        body["max_tokens"] = overrides.max_tokens
    for k, v in overrides.extra_params.items():
        body[k] = v


def _get_step(snapshot: ExecutionSnapshot, step_name: str) -> Any:
    """Resolve step by full or short name."""
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


def _get_call(model_calls: list[Any], call_label: str | None) -> Any:
    """Find a specific model call by label, or return the first/last one."""
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
