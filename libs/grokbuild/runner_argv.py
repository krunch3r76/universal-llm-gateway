"""CLI argument and environment builder for the grokbuild async runner."""

from __future__ import annotations

import os

from grokbuild.constants import (
    _XAI_EFFORT_INJECT_MODELS,
    _XAI_GROK43_EFFORT_STANZA,
    MODEL_REGISTRY,
)
from grokbuild.runner_types import RunnerSpec

_READ_ONLY_PREFIX = (
    "MODE: read_only. The operator has invoked you in advisory mode. "
    "Do NOT modify, create, or delete source code files. Do NOT run shell commands "
    "that mutate source files (no editor saves, no code generation that writes outputs). "
    "Git index operations (git stash, git stash pop, git stash apply, git add, "
    "git commit, git merge) ARE permitted when they are the explicit stated purpose "
    "of the dispatch — these are bookkeeping operations, not source edits. "
    "For all other tasks: narrate the changes you would propose — describe the diff "
    "in prose, name the files you would touch, and quote the exact patch hunks. "
    "The operator will review your proposal and re-invoke you in edit mode if they "
    "want the changes applied."
)

# Environment variables inherited from the parent process (pass-through).
# HOME is intentionally absent: it is substituted per-dispatch by runner.py
# via setup_dispatch_home() so the grok subprocess sees the dispatch-scoped
# config.toml rather than the real ~/.grok/config.toml.
_ALLOW = (
    "PATH",
    "LANG",
    "LC_ALL",
    "CORTEX_DB_PATH",
    "TODOS_DB_PATH",
    "GROKBUILD_RECURSION_DEPTH",
)

# Environment variables that are explicitly OVERRIDDEN per dispatch (not
# inherited). Runner code is responsible for setting these in the env dict
# returned by _build_env() before spawning the subprocess.
_OVERRIDE = ("HOME",)


def _build_env() -> dict[str, str]:
    src = os.environ
    env = {k: src[k] for k in _ALLOW if k in src}
    env["TERM"] = "dumb"
    return env


def _build_argv(spec: RunnerSpec) -> list[str]:
    """Compose the full grok CLI invocation for ``spec``.

    Flag emission rules:

    * ``--output-format`` is always ``streaming-json`` (V1 invariant).
    * Resume: ``-r SESSION`` when ``resume_strict=True``; ``-s SESSION``
      when ``resume_strict=False`` and ``session_id`` is set. ``-s`` is
      idempotent — grok creates a new session if SESSION is unknown.
    * ``--reasoning-effort`` / ``--effort`` / ``--max-turns`` /
      ``--best-of-n``: omitted entirely when the corresponding field is
      ``None`` (caller opt-out). The dispatcher's tier resolver ensures
      ``reasoning_effort`` and ``effort`` are non-None for normal use;
      Plain ``None`` here means "do not pass the flag" — used by tests
      and explicit-skip callers.
    * Boolean flags (``check``, ``no_subagents``, ``disable_web_search``)
      emit the named CLI flag iff True.
    """
    read_only_rules = _READ_ONLY_PREFIX if spec.mode == "read_only" else ""
    combined_rules = "\n\n".join(
        part for part in (read_only_rules, spec.system_context or "") if part
    )
    argv = [
        spec.grok_path,
        "-p",
        spec.prompt,
        "--cwd",
        spec.cwd,
        "--output-format",
        "streaming-json",
        "--permission-mode",
        spec.permission_mode,
        "--always-approve",
    ]
    if spec.model:
        # For xAI grok-4.3 dispatches, substitute the base stanza with a tier-specific
        # effort stanza so cloud-proxy can decode __effort_<value> and inject
        # reasoning.effort.  The grok CLI ignores --reasoning-effort for custom stanzas;
        # stanza substitution is the only reliable path for effort injection.
        effective_model = (
            _XAI_GROK43_EFFORT_STANZA.get(spec.tier, spec.model)
            if spec.model in _XAI_EFFORT_INJECT_MODELS
            else spec.model
        )
        argv.extend(["--model", effective_model])
    if combined_rules:
        argv.extend(["--rules", combined_rules])

    # Session resume — strict (-r) vs idempotent (-s).
    if spec.session_id:
        argv.append("-r" if spec.resume_strict else "-s")
        argv.append(spec.session_id)

    # Per-flag capability check: model=None resolves to "grok-build" (CLI default).
    # Unknown models (not in registry) pass both flags through — admission won't
    # block, but the caller may still hit a CLI/API rejection downstream.
    _lookup = spec.model if spec.model is not None else "grok-build"
    _caps = MODEL_REGISTRY.get(_lookup)
    if spec.reasoning_effort is not None and (
        _caps is None or _caps.supports_reasoning_effort
    ):
        argv.extend(["--reasoning-effort", spec.reasoning_effort])
    if spec.effort is not None and (_caps is None or _caps.supports_effort):
        argv.extend(["--effort", spec.effort])
    if spec.max_turns is not None:
        argv.extend(["--max-turns", str(spec.max_turns)])
    if spec.best_of_n is not None:
        argv.extend(["--best-of-n", str(spec.best_of_n)])
    if spec.check:
        argv.append("--check")
    if spec.no_subagents:
        argv.append("--no-subagents")
    if spec.disable_web_search:
        argv.append("--disable-web-search")

    return argv
