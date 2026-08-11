#!/usr/bin/env python3
"""Probe: has Cursor enabled local-agent `get_usage()` for this account yet?

agent-bus:7078 / spike `cursor-sdk-feature-alignment/spike-1.0.27-bump-safety.md`.

Unfiltered local `agent.get_usage()` currently returns
`[feature_unavailable] This feature is not available for your account`. That
wall is the only thing blocking the 1.0.26 -> 1.0.27 bump, whose sole upside is
billed usage + dollar cost for local agents. This probe answers "has it
cleared?" without a human re-running the spike by hand.

Creates a local agent and calls `get_usage()` with no prompt sent, so it costs
no inference. An entitled account returns an empty usage record for a run-less
agent rather than an error.

Requires cursor-sdk >= 1.0.27. On 1.0.26 the server answers `getUsage() is only
supported for cloud agents for now.` before any entitlement check, so that
version cannot observe the entitlement at all and the probe reports
`indeterminate` instead of a false `walled`. To probe from the pinned 1.0.26
venv, point --python at a throwaway 1.0.27 environment:

  python -m venv /tmp/sdk-canary && /tmp/sdk-canary/bin/pip install 'cursor-sdk==1.0.27'

Usage:
  scripts/probes/cursor_sdk_usage_entitlement.py
  scripts/probes/cursor_sdk_usage_entitlement.py --python /tmp/sdk-canary/bin/python

Exit codes:
  0  walled (expected) or indeterminate — nothing to do
  1  entitlement CLEARED — re-probe the 1.0.27 bump
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_SECRETS_ENV = Path.home() / ".gateway" / "secrets.env"
_MIN_VERSION = (1, 0, 27)
_WALLED_MARKER = "feature_unavailable"
_CLOUD_ONLY_MARKER = "only supported for cloud agents"

_VERDICT_ENTITLED = "entitled"
_VERDICT_WALLED = "walled"
_VERDICT_INDETERMINATE = "indeterminate"


def parse_version(raw: str) -> tuple[int, ...]:
    """Best-effort numeric version tuple; non-numeric segments stop the parse."""
    parts: list[int] = []
    for segment in raw.split("."):
        digits = ""
        for char in segment:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def resolve_api_key() -> str:
    """Env first, then the fleet secrets file GIW's unit is fed from."""
    from_env = os.environ.get("CURSOR_API_KEY", "").strip()
    if from_env:
        return from_env
    if not _SECRETS_ENV.is_file():
        return ""
    found = ""
    for raw_line in _SECRETS_ENV.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == "CURSOR_API_KEY":
            found = value.strip().strip("'\"")
    return found


def classify_outcome(
    *,
    ok: bool,
    error_message: str | None,
) -> tuple[str, str]:
    """Map a get_usage() outcome to (verdict, human reason)."""
    if ok:
        return (
            _VERDICT_ENTITLED,
            "get_usage() returned a usage record — the account wall is gone",
        )
    message = error_message or ""
    if _WALLED_MARKER in message:
        return (
            _VERDICT_WALLED,
            "account still lacks the local-agent usage entitlement",
        )
    if _CLOUD_ONLY_MARKER in message:
        return (
            _VERDICT_INDETERMINATE,
            "server answered cloud-only before any entitlement check "
            "(sdk too old to observe this)",
        )
    return (
        _VERDICT_INDETERMINATE,
        f"unrecognized failure, not an entitlement signal: {message}",
    )


def _probe_in_process() -> dict[str, Any]:
    """Create a run-less local agent and call get_usage(). No inference spend."""
    from importlib.metadata import version as pkg_version

    installed = pkg_version("cursor-sdk")
    result: dict[str, Any] = {"sdk_version": installed}

    if parse_version(installed) < _MIN_VERSION:
        verdict, reason = (
            _VERDICT_INDETERMINATE,
            f"cursor-sdk {installed} predates "
            f"{'.'.join(str(part) for part in _MIN_VERSION)}; "
            "the cloud-only wall masks the entitlement state",
        )
        result.update({"verdict": verdict, "reason": reason, "probed": False})
        return result

    api_key = resolve_api_key()
    if not api_key:
        result.update(
            {
                "verdict": _VERDICT_INDETERMINATE,
                "reason": (
                    "no CURSOR_API_KEY in env or "
                    f"{_SECRETS_ENV} — cannot reach the GetUsage RPC"
                ),
                "probed": False,
            }
        )
        return result

    from cursor_sdk import Agent, LocalAgentOptions

    with tempfile.TemporaryDirectory(prefix="sdk-entitlement-canary-") as workdir:
        agent = Agent.create(
            model="composer-2.5",
            api_key=api_key,
            local=LocalAgentOptions(cwd=workdir),
        )
        try:
            usage = agent.get_usage()
            ok, error_message = True, None
            result["usage_repr"] = repr(usage)[:400]
        except Exception as exc:
            ok, error_message = False, str(exc)
            result["error_type"] = type(exc).__name__
            result["error"] = error_message[:400]
        finally:
            agent.close()

    verdict, reason = classify_outcome(ok=ok, error_message=error_message)
    result.update({"verdict": verdict, "reason": reason, "probed": True})
    return result


def _probe_via_interpreter(python: str) -> dict[str, Any]:
    """Re-exec this probe under another interpreter and read back its JSON."""
    completed = subprocess.run(
        [python, __file__, "--_emit-json"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    stdout = completed.stdout.strip()
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "verdict": _VERDICT_INDETERMINATE,
            "reason": f"child interpreter produced no JSON verdict (exit {completed.returncode})",
            "probed": False,
            "stderr": completed.stderr.strip()[:400],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--python",
        help="Interpreter with cursor-sdk >= 1.0.27 (default: this one)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the verdict without failing when the entitlement cleared",
    )
    parser.add_argument(
        "--_emit-json",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    if args.python:
        result = _probe_via_interpreter(args.python)
    else:
        try:
            result = _probe_in_process()
        except Exception as exc:
            result = {
                "verdict": _VERDICT_INDETERMINATE,
                "reason": f"probe could not run: {type(exc).__name__}: {exc}",
                "probed": False,
            }

    if args._emit_json:
        print(json.dumps(result))
        return 0

    print("cursor_sdk_usage_entitlement (agent-bus:7078)")
    print(json.dumps(result, indent=2))

    if result.get("verdict") == _VERDICT_ENTITLED:
        print(
            "\nACTION: local-agent get_usage is live. Re-probe the 1.0.27 bump "
            "and the wire-cost path in dispatch economics."
        )
        return 0 if args.dry_run else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
