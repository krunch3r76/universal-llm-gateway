"""Parse closeout verification commands for route-probe observe (Leg E2).

observe-only: never refuses, rewrites, delays or fails a closeout
"""

from __future__ import annotations

import re
import shlex

PARSE_REFUSALS: tuple[str, ...] = (
    "pipe",
    "redirect",
    "substitution",
    "unparseable",
    "env_prefix",
    "last_segment_not_pytest",
    "executable",
    "module_path_absent",
    "node_id",
    "filter_or_selection",
    "unknown_flag",
)

_BACKSLASH_NEWLINE = re.compile(r"\\\r?\n")
_CHAIN_SPLIT = re.compile(r"&&|\|\||;|\n")
_TRAILING_STDERR = re.compile(r"\s+2>&1$")
_ENV_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PATH_ARG = re.compile(r"^[A-Za-z0-9_./-]+$")
_SELECTION_MO = re.compile(r"^-[moc](?:$|[^-])")


def parse_route_probe_command(command: str, *, test_module: str) -> tuple[bool, str | None]:
    """Return (accepted, refusal); refusal is one of PARSE_REFUSALS or None."""
    module_rel = test_module.removeprefix("libs/")

    text = _BACKSLASH_NEWLINE.sub(" ", command)
    parts = [p.strip() for p in _CHAIN_SPLIT.split(text)]
    segment = ""
    for part in reversed(parts):
        if part:
            segment = part
            break
    if not segment:
        return False, "last_segment_not_pytest"

    segment = _TRAILING_STDERR.sub("", segment, count=1)

    if "|" in segment:
        return False, "pipe"
    if ">" in segment or "<" in segment:
        return False, "redirect"
    if "`" in segment or "$(" in segment:
        return False, "substitution"

    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False, "unparseable"

    if not tokens:
        return False, "last_segment_not_pytest"

    i = 0
    if tokens[0] == "env":
        i = 1
        if i < len(tokens) and tokens[i].startswith("-"):
            return False, "env_prefix"
    while i < len(tokens) and _ENV_VAR.match(tokens[i]):
        if not tokens[i].startswith("PYTHONPATH="):
            return False, "env_prefix"
        i += 1

    if i >= len(tokens):
        return False, "last_segment_not_pytest"

    tok = tokens[i]
    args: list[str]
    if tok == "pytest" or tok.endswith("/pytest"):
        args = tokens[i + 1 :]
    elif tok in ("python", "python3") and tokens[i + 1 : i + 3] == ["-m", "pytest"]:
        args = tokens[i + 3 :]
    else:
        if any(t == "pytest" or t.endswith("/pytest") for t in tokens[i + 1 :]):
            return False, "executable"
        return False, "last_segment_not_pytest"

    path_hits: list[str] = []
    j = 0
    while j < len(args):
        arg = args[j]
        if "::" in arg:
            return False, "node_id"
        if arg.startswith("-"):
            if arg.startswith("-k") or arg.startswith("--deselect"):
                return False, "filter_or_selection"
            if arg in ("--collect-only", "--co"):
                return False, "filter_or_selection"
            if _SELECTION_MO.match(arg):
                return False, "filter_or_selection"
            if arg == "-p":
                if j + 1 >= len(args) or args[j + 1] != "no:cacheprovider":
                    return False, "unknown_flag"
                j += 2
                continue
            if arg in ("-q", "-qq", "-v", "-vv", "-x", "-s", "--no-header"):
                j += 1
                continue
            if re.match(r"^-r[A-Za-z]+$", arg):
                j += 1
                continue
            if re.match(r"^--tb=[a-z]+$", arg):
                j += 1
                continue
            if re.match(r"^--color=[a-z]+$", arg):
                j += 1
                continue
            if re.match(r"^--maxfail=\d+$", arg):
                j += 1
                continue
            if re.match(r"^--durations=\d+$", arg):
                j += 1
                continue
            return False, "unknown_flag"
        if _PATH_ARG.match(arg):
            path_hits.append(arg)
            j += 1
            continue
        return False, "unknown_flag"

    def _path_matches(p: str) -> bool:
        return p == test_module or p.endswith("/" + test_module) or p == module_rel

    if not any(_path_matches(p) for p in path_hits):
        return False, "module_path_absent"

    return True, None