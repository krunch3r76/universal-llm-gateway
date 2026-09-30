"""AC10: retired-token scans fail on any hit outside the keep-list.

Encodes Acceptance item 10 by file name and the valued patterns: Search A
(both commands), Search E with valued purpose and role alternations, both
Search D commands, and Search F. Roots are the repo. Exclusions are Search
A's non-test globs. A hit whose file is not on the keep-list fails. The
check is not a residual count at one head.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

# Acceptance item 10 keep-list, repo-relative file names.
_KEEP_LIST = frozenset(
    {
        "services/universal-stargate/systems/frontier_consult/cursor_sdk_alignment.py",
        "services/git_integration_worker/cursor_auto/cse_wake_delivery.py",
        "services/git_integration_worker/routes/cursor_sdk.py",
        "services/universal-stargate/systems/frontier_consult/review_child_spawn_hook.py",
        "cursor-plugins/ulg-ecosystem/skills/liaison/SKILL.md",
        "libs/bus_watch/cse_followup.py",
        "libs/web_chat_relay/claude_leg.py",
        "libs/reasoning_posture_contracts.py",
        "services/universal-stargate/systems/frontier_consult/handoff_reasoning_posture.py",
        "libs/dispatch_knob_policy/dispatch_knob_policy.py",
        "libs/dispatch_knob_policy/__init__.py",
        "cursor-plugins/ulg-ecosystem/skills/hypothesize-simulate/SKILL.md",
        "scripts/model_manager/ui/controller/charter_runner/window_terminal_contract.py",
        "libs/claude_bundles/operator_proxy_mission.py",
        "services/universal-stargate/systems/frontier_consult/cdp_generate.py",
        "services/universal-stargate/systems/frontier_consult/life_dispatch_routes.py",
    }
)

_SURVIVING = (
    "implement|conductor|wrap|sketch|answer|confer|ask|investigate|"
    "verify|execute|propagate|seed|recon"
)
_RETIRED_CONTRACT = "none|pure-mechanical|consult"
_PURPOSE_VALUES = "review|operator-proxy|mission|ask"
_ROLE_VALUES = (
    "reviewer|synthesizer|artisan|skeptic|gatherer|"
    "web-consult|web-implement|cursor-consult|cursor-implement"
)

# Search A retired-token command, Search A dict-literal command, Search E
# (valued purpose and role), both Search D commands, Search F set names.
_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "search_a_retired",
        rf"(contract|handoff_contract)\s*[=:]\s*.?({_RETIRED_CONTRACT})\b|"
        rf"purpose\s*[=:]\s*.?({_PURPOSE_VALUES})\b|"
        rf"role\s*[=:]\s*.?({_ROLE_VALUES})\b",
    ),
    (
        "search_a_dict",
        rf'"contract"\s*:\s*"({_RETIRED_CONTRACT})"|'
        rf'"purpose"\s*:\s*"({_PURPOSE_VALUES})"|'
        rf'"role"\s*:\s*"({_ROLE_VALUES})"',
    ),
    (
        "search_e_valued",
        rf"purpose\s*=\s*.?({_PURPOSE_VALUES})\b|"
        rf"role\s*=\s*.?({_ROLE_VALUES})\b|"
        rf"contract\s*=\s*.?({_RETIRED_CONTRACT})\b",
    ),
    (
        "search_d_team_dispatch",
        rf"team_dispatch.*contract=({_SURVIVING})\b",
    ),
    (
        "search_d_contract_colon",
        rf"contract.{{1,3}}:.{{1,3}}({_SURVIVING})\b",
    ),
    (
        "search_f_set_names",
        "MECHANICAL_CONTRACTS|_MECHANICAL\\b|_VALID_CONTRACTS|"
        "_CONTRACT_ALIASES|_CONTRACT_MCP_FILTER|_IMPLEMENT_CLASS_CONTRACTS|"
        "_IMPLEMENT_CONTRACTS|TEAM_DISPATCH_CONTRACTS|TO_THREAD_CONTRACTS|"
        "HANDOFF_OVERRIDE_CONTRACTS|MATERIALIZER_CONTRACTS|RESIDUAL_CONTRACTS|"
        "REASONING_POSTURE_SKIP_CONTRACTS|HYPOTHESIZE_SIMULATE_CONTRACTS|"
        "FREEFORM_CONTRACTS|DEPRECATED_ALIASES",
    ),
)

_EXCLUSIONS = ("!**/test*", "!**/__pycache__/**")


def _kept(rel: str) -> bool:
    norm = rel.replace("\\", "/").lstrip("./")
    if norm in _KEEP_LIST:
        return True
    return any(norm.endswith("/" + item) for item in _KEEP_LIST)


def _rg(pattern: str) -> list[tuple[str, str, str]]:
    cmd = ["rg", "-n", "--no-heading"]
    for glob in _EXCLUSIONS:
        cmd.extend(["-g", glob])
    cmd.extend([pattern, str(_REPO)])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 1:
        return []
    if proc.returncode != 0:
        raise AssertionError(f"rg failed ({proc.returncode}): {proc.stderr}")
    hits: list[tuple[str, str, str]] = []
    for line in proc.stdout.splitlines():
        # path:line:text — path may contain colons only as the separators rg uses.
        rel_abs, sep, rest = line.partition(":")
        if not sep:
            continue
        lineno, sep2, text = rest.partition(":")
        if not sep2:
            continue
        try:
            rel = str(Path(rel_abs).resolve().relative_to(_REPO))
        except ValueError:
            rel = rel_abs
        hits.append((rel.replace("\\", "/"), lineno, text))
    return hits


def test_retired_token_hits_stay_inside_keep_list() -> None:
    outside: list[str] = []
    for name, pattern in _PATTERNS:
        for rel, lineno, text in _rg(pattern):
            if _kept(rel):
                continue
            snippet = text.strip()
            if len(snippet) > 160:
                snippet = snippet[:157] + "..."
            outside.append(f"{name} {rel}:{lineno}: {snippet}")
    if outside:
        preview = "\n".join(outside[:80])
        extra = ""
        if len(outside) > 80:
            extra = f"\n... {len(outside) - 80} more"
        pytest.fail(
            f"{len(outside)} hit(s) outside the keep-list:\n{preview}{extra}"
        )
