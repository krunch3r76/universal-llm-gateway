"""Regex search over text and converted-document files (cortex sandbox).

Phase 1: single-file search with the unified response envelope. Directory
search and the workspaces-parity refactor land in Phase 2, which grows this
module with ``search_directory_impl`` and the shared cost-bound constants.

Sidecar-first text loading (``load_searchable_text``) satisfies
``decision:mcp-fs-timeout-observability`` (agent-bus:962).
"""

from __future__ import annotations

import re
from typing import Any

from mcp_events import record

from .._file_helpers import load_searchable_text
from .._search_helpers import SearchBudgetState, load_text_for_search_file
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


def search_file_impl(
    path: str,
    pattern: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Search a single cortex file (text or converted document).

    Sidecar-first text load via ``load_searchable_text``. Returns the unified
    search envelope with ``mode="file"`` (the ``file`` per-match key is omitted
    in this mode; ``path`` and ``extraction_method`` are top-level).
    """
    src = safe_path(path)
    if not src.exists():
        raise FileNotFoundError(f"File not found: {path!r}")
    if not src.is_file():
        raise ValueError(f"Path is not a file: {path!r}")

    compiled = compile_pattern(pattern)
    text, method = load_searchable_text(src)
    matches: list[dict[str, Any]] = []
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
    )
    return {
        "path": path,
        "mode": "file",
        "matches": matches,
        "truncated": truncated,
        "skipped_converted": 0,
        "extraction_method": extraction_method,
    }


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
    counted in ``skipped_converted`` and not extracted (F1). Truly-binary
    files are skipped via ``SEARCH_BINARY_SUFFIXES`` (converted formats are
    searched first). Native text is searched unbounded (cheap).
    """
    base = safe_path(path) if path else SANDBOX_ROOT
    if not base.is_dir():
        raise ValueError(f"Path is not a directory: {path!r}")

    compiled = compile_pattern(pattern)
    matches: list[dict[str, Any]] = []
    state = SearchBudgetState()
    truncated = False

    for fpath in sorted(base.rglob("*")):
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
            break

    record(
        "mcp.fs.search.directory",
        sandbox="cortex",
        path=path or ".",
        matches=len(matches),
        converted_extracted=state.converted_extracted,
        skipped_converted=state.skipped_converted,
        truncated=truncated,
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
    if state.skipped_converted:
        response["_warning"] = (
            f"{state.skipped_converted} converted document(s) were NOT "
            "searched (extraction budget/cap) — results are NOT exhaustive "
            "over this tree. Do not certify 'zero remaining hits' from this "
            "response. (friction 23000)"
        )
    return response


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
