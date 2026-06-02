"""Shared conversion-aware search classification and text loading.

Used by cortex ``search_directory_impl`` and workspaces ``search_project_files``
so binary skips and converted-file budget/cap logic stay aligned (decision 188).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ._file_helpers import is_converted_format, load_searchable_text

# Truly-binary suffixes for search skips (decision 188). Converted formats
# (.pdf, .docx, …) are handled by ``is_converted_format`` BEFORE this check.
SEARCH_BINARY_SUFFIXES: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".dylib",
        ".o",
        ".a",
        ".whl",
        ".egg",
        ".gz",
        ".tar",
        ".zip",
        ".bz2",
        ".xz",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svg",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".bin",
        ".dat",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".pkl",
        ".pickle",
        ".npy",
        ".npz",
        ".gguf",
        ".ggml",
        ".safetensors",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".pdf",
    }
)


def is_search_binary(path: Path) -> bool:
    """True when *path* should be skipped as truly-binary during search."""
    return path.suffix.lower() in SEARCH_BINARY_SUFFIXES


@dataclass
class SearchBudgetState:
    skipped_converted: int = 0
    converted_extracted: int = 0
    methods: set[str] = field(default_factory=set)
    budget_start: float = field(default_factory=time.monotonic)


def load_text_for_search_file(
    path: Path,
    state: SearchBudgetState,
    *,
    budget_s: float,
    file_cap: int,
) -> tuple[str | None, str | None]:
    """Load searchable text for one candidate, honoring budget/cap on converted files.

    Returns ``(text, method)`` or ``(None, None)`` when the file is skipped.
    """
    if is_converted_format(path):
        over_cap = state.converted_extracted >= file_cap
        over_budget = (time.monotonic() - state.budget_start) >= budget_s
        if over_cap or over_budget:
            state.skipped_converted += 1
            return None, None
        try:
            text, method = load_searchable_text(path)
        except (OSError, TimeoutError):
            state.skipped_converted += 1
            return None, None
        except Exception:  # noqa: BLE001 — one bad doc must not abort the scan
            state.skipped_converted += 1
            return None, None
        state.converted_extracted += 1
        if method:
            state.methods.add(method)
        return text, method
    if is_search_binary(path):
        return None, None
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError:
        return None, None
