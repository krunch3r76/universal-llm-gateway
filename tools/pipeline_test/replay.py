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

from .models import ExecutionSnapshot, ReplayOverrides, ReplayResult

DEFAULT_STARGATE_URL = "http://localhost:9999"
_PLACEHOLDER_RE = re.compile(r"\{([\w]+(?:\.[\w]+)*)\}")


def replay_recorded(
    snapshot: ExecutionSnapshot,
    step_name: str,
    call_label: str | None = None,
    overrides: ReplayOverrides | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = 300.0,
) -> ReplayResult:
    """Re-send the exact recorded request_body, with optional overrides."""
    step = _get_step(snapshot, step_name)
    call = _get_call(step.model_calls, call_label)
    overrides = overrides or ReplayOverrides()

    body = dict(call.request_body)
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
        rendered = _try_render_from_yaml(step, call, pipeline_dir, overrides)
        if rendered is not None:
            body = rendered
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


def _try_render_from_yaml(
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
        if prompt_name is None and call.call_label:
            prompt_name = _resolve_prompt_from_config(
                step, call.call_label, pipeline_dir
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

    model = overrides.model or call.request_body.get("model", call.model_id)

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    body: dict[str, Any] = {
        "model": model,
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


def _resolve_prompt_from_config(
    step: Any, call_label: str, pipeline_dir: Path
) -> str | None:
    """Find a prompt name by scanning pipeline YAML for the step's config.

    Handles three patterns:
      assess_N          → step-level prompt_ref (the assess prompt)
      action_X_N        → already handled by _infer_prompt_name
      plain single-call → step-level prompt_ref (fallback for simple steps)
      other             → {call_label}_prompt_ref domain field
    """
    short_name = step.step_name.rsplit("__", 1)[-1]
    for yaml_file in pipeline_dir.rglob("*.yaml"):
        if yaml_file.name == "prompts.yaml":
            continue
        try:
            with yaml_file.open(encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception:
            continue
        for step_cfg in config.get("steps", []):
            if step_cfg.get("name") != short_name:
                continue
            if call_label.startswith("assess_"):
                ref = step_cfg.get("prompt_ref", "")
                if ref:
                    return ref.rsplit(".", 1)[-1]
            else:
                # Check call-specific domain field first (e.g. adjudicate_prompt_ref)
                for key, val in step_cfg.items():
                    if not key.endswith("_prompt_ref") or not isinstance(val, str):
                        continue
                    prefix = key.removesuffix("_prompt_ref")
                    if call_label == prefix or call_label.startswith(prefix + "_"):
                        return val.rsplit(".", 1)[-1]
                # Fallback: plain single-call step uses top-level prompt_ref
                ref = step_cfg.get("prompt_ref", "")
                if ref:
                    return ref.rsplit(".", 1)[-1]
    return None


def _build_template_variables(step: Any, call: Any) -> dict[str, Any]:
    """Build template substitution context from step inputs and call data."""
    variables: dict[str, Any] = {}
    variables.update(step.inputs)
    if call.system_prompt:
        variables["system_prompt"] = call.system_prompt
    if call.user_prompt:
        variables["user_prompt"] = call.user_prompt
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
            return alias
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
