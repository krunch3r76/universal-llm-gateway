"""Pure validation for grokbuild dispatch admission.

Admits the V1 op set and parameter surface; rejects retired V0 surfaces
(``op='dispatch'``, ``output_format='json'``, ``continue_recent``) via
structured ``retired_*`` reason codes per design rationale §1
(see ``tmp/prompts/grokbuild-v1/00-overview.md``).
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

# Cross-module import so the auth preflight subprocess and the runner
# subprocess see the same environment (review W3). Private prefix is
# preserved because _build_env is still an implementation detail of the
# runner; this validator is the only sanctioned external consumer.
from grokbuild.constants import (
    _PERMISSION_BY_MODE,
    _SIDECAR_DIR,
    _VALID_TIERS,
    DEFAULT_TIMEOUT_SECONDS,
)
from grokbuild.runner import _build_env

# Admitted op set. validate_dispatch is called only by the dispatch path
# with op='build' (the v1 rename of the prior 'dispatch' value). It also
# accepts the literal string 'dispatch' so that callers passing the retired
# value get a uniform .rejected envelope rather than a FastMCP schema-level
# rejection (per design rationale §1).
_VALID_OPS = frozenset({"build"})
_RETIRED_OPS = frozenset({"dispatch"})

_VALID_OUTPUT_FORMATS = frozenset({"streaming-json"})
_RETIRED_OUTPUT_FORMATS = frozenset({"json"})

_VALID_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh"}
)
_VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

# Inclusive bounds for integer-range params.
_MAX_TURNS_MIN = 1
_BEST_OF_N_MIN, _BEST_OF_N_MAX = 1, 16
_TIMEOUT_SECONDS_MIN, _TIMEOUT_SECONDS_MAX = 1, 86_400


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    reason: str
    reason_code: str
    permission_mode: str = ""
    grok_path: str = ""
    git_status_pre: str = ""
    dirty_admission: bool = False


@functools.lru_cache(maxsize=1)
def _resolve_grok_path() -> str | None:
    return shutil.which("grok")


class _GrokAuthProbe:
    """Cached probe for ``grok models`` auth health.

    Encapsulates what was previously a module-global mutable ``bool`` plus
    a ``global``-mutating reader and a test-only reset back-door (review
    S4). Behavior is unchanged: caches ONLY ``True`` returns so a transient
    ``False`` on a fresh container process doesn't poison the cache and
    block all dispatches until MCP restarts.

    Subprocess env mirrors the runner via ``_build_env()`` (review W3) so
    the preflight sees the same environment as the runner subprocess;
    otherwise the preflight could pass with credentials/proxy env that
    the stripped runner env lacks, masking real failures.

    Single instance per process: ``_GROK_AUTH_PROBE`` below. The
    module-level ``_grok_models_ok`` / ``_reset_grok_models_cache_for_tests``
    facades preserve the import surface so existing callers (validator
    body, test fixtures) keep working without renames.
    """

    __slots__ = ("_cached_ok",)

    def __init__(self) -> None:
        self._cached_ok: bool = False

    def ok(self) -> bool:
        if self._cached_ok:
            return True
        try:
            proc = subprocess.run(
                ["grok", "models"],
                capture_output=True,
                text=True,
                timeout=30,
                env=_build_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if proc.returncode == 0:
            self._cached_ok = True
            return True
        return False

    def reset(self) -> None:
        """TEST-ONLY: clear the cached True state.

        Used by ``test_support.clear_validator_caches`` and by tests that
        verify the cold-start transient-False contract.
        """
        self._cached_ok = False


_GROK_AUTH_PROBE = _GrokAuthProbe()


def _grok_models_ok() -> bool:
    """Module-level facade — preserves the import surface for callers.

    Delegates to ``_GROK_AUTH_PROBE.ok()``. Kept as a module-level
    function so existing imports (``from grokbuild.validator import
    _grok_models_ok``) and the validator body keep working without
    rename churn.
    """
    return _GROK_AUTH_PROBE.ok()


def _reset_grok_models_cache_for_tests() -> None:
    """TEST-ONLY back-door — delegates to the singleton's reset.

    Preserved as a module-level function so ``test_support`` and the test
    suite keep their existing import paths. Production code MUST NOT call
    this; the probe is self-managing.
    """
    _GROK_AUTH_PROBE.reset()


def _reject(reason_code: str, reason: str) -> ValidationResult:
    return ValidationResult(ok=False, reason=reason, reason_code=reason_code)


def validate_dispatch(  # noqa: PLR0911, PLR0913 — long admission chain by design
    op: str,
    cwd: str,
    mode: Literal["read_only", "edit"],
    session_id: str | None,
    continue_recent: bool,
    output_format: str,
    *,
    tier: str = "thorough",
    reasoning_effort: str | None = None,
    effort: str | None = None,
    max_turns: int | None = None,
    best_of_n: int | None = None,
    timeout_seconds: int | None = DEFAULT_TIMEOUT_SECONDS,
    resume_strict: bool = False,
) -> ValidationResult:
    """Run admission checks; short-circuit on first failure.

    Op + output_format are broadened to ``str`` so retired values
    (``op='dispatch'``, ``output_format='json'``) and the retired
    ``continue_recent`` kwarg can be rejected with structured reason codes
    on the uniform .rejected envelope — see design rationale §1 in
    ``tmp/prompts/grokbuild-v1/00-overview.md``.
    """
    # 1. Retired surfaces (rejected even when otherwise well-formed).
    if op in _RETIRED_OPS:
        return _reject(
            "retired_op",
            f"op={op!r} was retired in V1; use op='build'",
        )
    if op not in _VALID_OPS:
        return _reject("unknown_op", f"unsupported op: {op!r}")

    if output_format in _RETIRED_OUTPUT_FORMATS:
        return _reject(
            "retired_output_format",
            f"output_format={output_format!r} was retired in V1; "
            "use 'streaming-json' (call fetch_result for legacy json shape)",
        )
    if output_format not in _VALID_OUTPUT_FORMATS:
        return _reject(
            "bad_output_format",
            f"output_format must be 'streaming-json', got {output_format!r}",
        )

    if continue_recent:
        return _reject(
            "retired_param",
            "continue_recent was retired in V1 — set session_id explicitly "
            "(idempotent reuse via -s, or strict reuse via resume_strict=True for -r)",
        )

    # 2. Enum validation for tier + reasoning_effort + effort.
    if tier not in _VALID_TIERS:
        return _reject(
            "bad_tier",
            f"tier must be one of {sorted(_VALID_TIERS)!r}, got {tier!r}",
        )
    if (
        reasoning_effort is not None
        and reasoning_effort not in _VALID_REASONING_EFFORTS
    ):
        return _reject(
            "bad_reasoning_effort",
            f"reasoning_effort must be one of {sorted(_VALID_REASONING_EFFORTS)!r} "
            f"or None, got {reasoning_effort!r}",
        )
    if effort is not None and effort not in _VALID_EFFORTS:
        return _reject(
            "bad_effort",
            f"effort must be one of {sorted(_VALID_EFFORTS)!r} or None, got {effort!r}",
        )

    # 3. Integer-range validation.
    if max_turns is not None and max_turns < _MAX_TURNS_MIN:
        return _reject(
            "bad_max_turns",
            f"max_turns must be >= {_MAX_TURNS_MIN} or None, got {max_turns!r}",
        )
    if best_of_n is not None and not (_BEST_OF_N_MIN <= best_of_n <= _BEST_OF_N_MAX):
        return _reject(
            "bad_best_of_n",
            f"best_of_n must be in [{_BEST_OF_N_MIN}, {_BEST_OF_N_MAX}] "
            f"or None, got {best_of_n!r}",
        )
    if timeout_seconds == 0:
        pass  # unlimited — matches dispatch resolution + GrokbuildDispatchRequest
    elif timeout_seconds is not None and not (
        _TIMEOUT_SECONDS_MIN <= timeout_seconds <= _TIMEOUT_SECONDS_MAX
    ):
        return _reject(
            "bad_timeout_seconds",
            f"timeout_seconds must be 0 (no limit), in "
            f"[{_TIMEOUT_SECONDS_MIN}, {_TIMEOUT_SECONDS_MAX}], or None "
            f"(resolved default), got {timeout_seconds!r}",
        )

    # 4. Combination rules.
    if resume_strict and not session_id:
        return _reject(
            "bad_resume_strict_without_session_id",
            "resume_strict=True requires session_id to be a non-empty string",
        )

    # 5. cwd + git preconditions (UNCHANGED from prior implementation).
    if not cwd or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        return _reject(
            "cwd_missing", f"cwd must be an existing absolute directory: {cwd!r}"
        )

    try:
        subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError:
        return _reject(
            "not_a_git_repo", f"cwd is not inside a git working tree: {cwd!r}"
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _reject("git_unreachable", f"git invocation failed for {cwd!r}: {exc}")

    try:
        status_proc = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        return _reject("git_unreachable", f"git status failed for {cwd!r}: {exc}")

    git_status_pre = status_proc.stdout
    dirty = bool(git_status_pre.strip())
    # mode-split: edit needs a clean baseline to produce a meaningful diff;
    # read_only is a scan and admits any tree state with audit_incomplete.
    if dirty and mode == "edit":
        return _reject(
            "working_tree_dirty",
            "working tree must be clean at admission for edit mode — "
            "stash or commit in-flight changes",
        )

    # 6. Toolchain + sidecar preconditions (UNCHANGED).
    grok_path = _resolve_grok_path()
    if not grok_path:
        return _reject("grok_not_in_path", "grok executable not found on PATH")

    if not _grok_models_ok():
        return _reject(
            "missing_grok_auth",
            "grok models preflight failed — run grok login",
        )

    try:
        _SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # PermissionError is a subclass of OSError — catch the parent.
        return _reject("sidecar_unavailable", str(exc))

    return ValidationResult(
        ok=True,
        reason="",
        reason_code="",
        permission_mode=_PERMISSION_BY_MODE[mode],
        grok_path=grok_path,
        git_status_pre=git_status_pre,
        dirty_admission=dirty,
    )
