"""CLI argument and environment builder for the cursorbuild async runner.

Retargets ``grokbuild.runner_argv`` to the ``cursor-agent`` CLI. Key shape
differences from grok, all confirmed against ``cursor-agent --help``
(version 2026.05.28-a70ca7c):

* The prompt is a trailing **positional** argument, not ``-p <prompt>``.
  ``--print`` is a separate boolean that streams responses to the console.
* Read-only execution is native: ``--mode plan`` (or ``ask``) makes the agent
  non-mutating, so there is no read-only rules *prefix* to inject (grok had
  no such mode and relied on ``--rules``).
* cursor-agent has no ``--rules`` flag, so ``system_context`` is folded into
  the head of the positional prompt rather than passed as its own flag.
* Workspace selection is ``--workspace <path>`` (no ``--cwd``); the cwd lock
  is enforced by the runner via the spawn cwd, not an argv flag.

The environment builder is identical to grok's: a parent-env allow-list with
``HOME`` overridden per-dispatch by ``cursorbuild.home`` so the spawned
cursor-agent sees the isolated ``.cursor`` config rather than the real one.
"""

from __future__ import annotations

import os
from pathlib import Path

from universal_logging import get_logger

from cursorbuild.constants import HEADLESS_MCP_FLAGS, OUTPUT_FORMAT
from cursorbuild.runner_types import RunnerSpec

logger = get_logger(__name__)

# Environment variables inherited from the parent process (pass-through).
# HOME is intentionally absent: it is substituted per-dispatch by runner.py
# via setup_dispatch_home() so the cursor-agent subprocess sees the
# dispatch-scoped .cursor/ config rather than the real ~/.cursor/.
_ALLOW = (
    "PATH",
    "LANG",
    "LC_ALL",
    "CORTEX_DB_PATH",
    "TODOS_DB_PATH",
    "CURSORBUILD_RECURSION_DEPTH",
)

# Environment variables explicitly OVERRIDDEN per dispatch (not inherited).
# Runner code sets these in the env dict returned by _build_env() before
# spawning the subprocess.
_OVERRIDE = ("HOME",)


_VENV_ROOT = os.path.join(os.path.expanduser("~"), ".venvs", "universal")
_VENV_BIN = os.path.join(_VENV_ROOT, "bin")
# libs/cursorbuild/argv.py -> repo root; used for PYTHONPATH/PROJECT_ROOT so
# inner cursor-agent shell commands see the same import surface as an
# activated venv. The stargate entry uses the hyphenated directory name
# (services/universal-stargate), NOT the importable module name.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTHONPATH = os.pathsep.join(
    (
        str(_REPO_ROOT),
        str(_REPO_ROOT / "libs"),
        str(_REPO_ROOT / "services" / "universal-stargate"),
    )
)


def _build_env() -> dict[str, str]:
    src = os.environ
    env = {k: src[k] for k in _ALLOW if k in src}
    env["TERM"] = "dumb"
    env["VIRTUAL_ENV"] = _VENV_ROOT
    env["PROJECT_ROOT"] = str(_REPO_ROOT)
    env["PYTHONPATH"] = _PYTHONPATH
    parent_path = env.get("PATH", "")
    if _VENV_BIN not in parent_path.split(os.pathsep):
        env["PATH"] = (
            f"{_VENV_BIN}{os.pathsep}{parent_path}" if parent_path else _VENV_BIN
        )
    return env


def build_argv(spec: RunnerSpec) -> list[str]:
    """Compose the full cursor-agent CLI invocation for ``spec``.

    Flag emission rules:

    * Head is always ``--print --output-format stream-json`` so the runner
      gets machine-parseable streamed events.
    * ``read_only`` mode emits ``--mode <plan|ask>`` and nothing that could
      mutate the tree. ``plan`` wins over ``force``: if a read-only spec also
      sets ``force=True`` the flag is dropped (with a warning) rather than
      silently escalating to a mutating run.
    * ``edit`` mode emits the headless MCP permission triple
      (``--approve-mcps --force --trust``) when ``mcp_enabled``; otherwise the
      ``--force --trust`` pair so the agent can write without interactive
      prompts but without standing up the MCP proxy.
    * ``--workspace <cwd>`` selects the repo; there is no ``--cwd``.
    * Worktree / resume / streaming flags are emitted iff their field is set.
      ``session_id`` (``--resume``) takes precedence over ``continue_session``
      (``--continue``).
    * The prompt is appended LAST as a positional, with ``system_context``
      folded into its head (cursor-agent has no ``--rules`` flag).
    """
    argv: list[str] = [
        spec.cursor_agent_bin,
        "--print",
        "--output-format",
        OUTPUT_FORMAT,
    ]

    if spec.mode == "read_only":
        argv.extend(["--mode", spec.read_only_mode])
        if spec.force:
            logger.warning(
                "build_argv: force=True ignored in read_only mode "
                "(--mode %s wins); dispatch_id=%s",
                spec.read_only_mode,
                spec.dispatch_id,
            )
    else:
        argv.extend(HEADLESS_MCP_FLAGS if spec.mcp_enabled else ("--force", "--trust"))

    if spec.model:
        argv.extend(["--model", spec.model])

    argv.extend(["--workspace", spec.cwd])

    if spec.worktree_name:
        argv.extend(["-w", spec.worktree_name])
    if spec.worktree_base:
        argv.extend(["--worktree-base", spec.worktree_base])
    if spec.skip_worktree_setup:
        argv.append("--skip-worktree-setup")

    if spec.session_id:
        argv.extend(["--resume", spec.session_id])
    elif spec.continue_session:
        argv.append("--continue")

    if spec.stream_partial_output:
        argv.append("--stream-partial-output")

    prompt = (
        f"{spec.system_context}\n\n{spec.prompt}"
        if spec.system_context
        else spec.prompt
    )
    argv.append(prompt)

    return argv
