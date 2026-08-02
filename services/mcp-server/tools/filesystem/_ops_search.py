"""Regex search over text and converted-document files (cortex sandbox).

Phase 1: single-file search with the unified response envelope. Directory
search and the workspaces-parity refactor land in Phase 2, which grows this
module with ``search_directory_impl`` and the shared cost-bound constants.

Sidecar-first text loading (``load_searchable_text``) satisfies
``decision:mcp-fs-timeout-observability`` (agent-bus:962).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from mcp_events import record

from .._search_helpers import (
    SEARCH_SKIP_DIRS,
    SearchBudgetState,
    build_search_warnings,
    load_text_for_search_file,
)
from ._paths import SANDBOX_ROOT, safe_path

DEFAULT_MAX_RESULTS = 50

# Directory-search cost bounds (decision:mcp-fs-timeout-observability — F1).
# Converted-file extraction is the only expensive path; native text is cheap.
# Plaintext PDF extraction is <1s/file, but a tree with hundreds of converted
# files still needs an aggregate cap to stay inside the ~30s remote-MCP window.
SEARCH_CONVERTED_BUDGET_S = (
    20.0  # aggregate wall-clock for converted extraction per call
)
SEARCH_CONVERTED_FILE_CAP = 10  # max converted files extracted per directory search


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile *pattern*, raising ValueError with the workspaces-parity message."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc


def search_in_text(
    text: str,
    pattern: re.Pattern[str],
    matches: list[dict[str, Any]],
    *,
    rel_path: str | None,
    max_results: int,
) -> bool:
    """Append regex line matches from *text* to *matches*.

    ``rel_path`` populates each match's ``file`` key (directory mode); pass
    ``None`` for single-file mode to omit it. Returns True when *max_results*
    is reached so the caller can stop scanning further files.
    """
    for line_num, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            match: dict[str, Any] = {"line": line_num, "text": line.rstrip()}
            if rel_path is not None:
                match["file"] = rel_path
            matches.append(match)
            if len(matches) >= max_results:
                return True
    return False


def _finalize_search_response(
    response: dict[str, Any],
    state: SearchBudgetState,
    *,
    wall_truncated: bool,
) -> dict[str, Any]:
    response["skipped_oversized"] = state.skipped_oversized
    warning = build_search_warnings(state, wall_truncated=wall_truncated)
    if warning:
        response["_warning"] = warning
    matches = response.get("matches")
    if isinstance(matches, list) and not matches:
        response["status"] = "no_matches"
        response["observation"] = (
            "Search completed successfully and found no regex line matches "
            "in the scanned scope. Zero matches ≠ tool failure."
        )
    else:
        response["status"] = "ok"
        if isinstance(matches, list):
            response["match_count"] = len(matches)
    return response


def search_file_impl(
    path: str,
    pattern: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Search a single cortex file (text or converted document).

    Size-cap and converted budget/cap route through ``load_text_for_search_file``.
    Returns the unified search envelope with ``mode="file"``.
    """
    src = safe_path(path)
    if not src.exists():
        raise FileNotFoundError(f"File not found: {path!r}")
    if not src.is_file():
        raise ValueError(f"Path is not a file: {path!r}")

    compiled = compile_pattern(pattern)
    state = SearchBudgetState()
    text, method = load_text_for_search_file(
        src,
        state,
        budget_s=SEARCH_CONVERTED_BUDGET_S,
        file_cap=SEARCH_CONVERTED_FILE_CAP,
    )
    matches: list[dict[str, Any]] = []
    truncated = False
    if text is not None:
        truncated = search_in_text(
            text, compiled, matches, rel_path=None, max_results=max_results
        )
    extraction_method = method or "native_text"
    record(
        "mcp.fs.search.file",
        sandbox="cortex",
        path=path,
        matches=len(matches),
        extraction_method=extraction_method,
        truncated=truncated,
        skipped_oversized=state.skipped_oversized,
    )
    response: dict[str, Any] = {
        "path": path,
        "mode": "file",
        "matches": matches,
        "truncated": truncated,
        "skipped_converted": state.skipped_converted,
        "extraction_method": extraction_method,
    }
    return _finalize_search_response(response, state, wall_truncated=False)


def search_directory_impl(
    path: str,
    pattern: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Search a cortex directory tree (text + converted documents) with cost bounds.

    Converted-file extraction is bounded by an aggregate wall-clock budget
    (``SEARCH_CONVERTED_BUDGET_S``) and a converted-file count cap
    (``SEARCH_CONVERTED_FILE_CAP``); converted files beyond either bound are
    counted in ``skipped_converted`` and not extracted (F1). Native text
    files above ``SEARCH_NATIVE_MAX_BYTES`` increment ``skipped_oversized``.
    Overall enumeration + scan is bounded by ``SEARCH_WALL_BUDGET_S`` (24276).
    ``tmp/`` and ``.runtime/`` are pruned during the walk.
    """
    base = safe_path(path) if path else SANDBOX_ROOT
    if not base.is_dir():
        raise ValueError(f"Path is not a directory: {path!r}")

    compiled = compile_pattern(pattern)
    matches: list[dict[str, Any]] = []
    state = SearchBudgetState()
    truncated = False
    wall_truncated = False
    stop = False

    for dirpath_str, dirnames, filenames in os.walk(base):
        if state.wall_exceeded():
            wall_truncated = True
            truncated = True
            break
        dirnames[:] = [
            d for d in sorted(dirnames) if d not in SEARCH_SKIP_DIRS
        ]
        dirpath = Path(dirpath_str)
        for fname in sorted(filenames):
            if state.wall_exceeded():
                wall_truncated = True
                truncated = True
                stop = True
                break
            fpath = dirpath / fname
            if not fpath.is_file():
                continue
            rel = str(fpath.relative_to(SANDBOX_ROOT))
            text, _method = load_text_for_search_file(
                fpath,
                state,
                budget_s=SEARCH_CONVERTED_BUDGET_S,
                file_cap=SEARCH_CONVERTED_FILE_CAP,
            )
            if text is None:
                continue

            if search_in_text(
                text, compiled, matches, rel_path=rel, max_results=max_results
            ):
                truncated = True
                stop = True
                break
        if stop:
            break

    record(
        "mcp.fs.search.directory",
        sandbox="cortex",
        path=path or ".",
        matches=len(matches),
        converted_extracted=state.converted_extracted,
        skipped_converted=state.skipped_converted,
        skipped_oversized=state.skipped_oversized,
        truncated=truncated,
        wall_truncated=wall_truncated,
    )
    response: dict[str, Any] = {
        "path": path,
        "mode": "directory",
        "matches": matches,
        "truncated": truncated,
        "skipped_converted": state.skipped_converted,
        "extraction_method": "+".join(sorted(state.methods))
        if state.methods
        else "native_text",
    }
    return _finalize_search_response(response, state, wall_truncated=wall_truncated)


def search_path_impl(
    path: str,
    pattern: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Route a cortex search to file or directory mode by stat on the resolved path."""
    src = safe_path(path)
    if not src.exists():
        raise FileNotFoundError(f"Path not found: {path!r}")
    if src.is_dir():
        return search_directory_impl(path, pattern, max_results=max_results)
    return search_file_impl(path, pattern, max_results=max_results)
