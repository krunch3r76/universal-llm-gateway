"""Shared conversion-aware search classification and text loading.

Used by cortex ``search_directory_impl`` and workspaces ``search_project_files``
so binary skips and converted-file budget/cap logic stay aligned (decision 188).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ._file_helpers import is_converted_format, load_searchable_text

# Overall wall budget covers enumeration + native read + converted extraction (F1 + 24276).
SEARCH_WALL_BUDGET_S = 20.0
SEARCH_NATIVE_MAX_BYTES = 2 * 1024 * 1024
SEARCH_SKIP_DIRS: frozenset[str] = frozenset({"tmp", ".runtime"})

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
    skipped_oversized: int = 0
    converted_extracted: int = 0
    methods: set[str] = field(default_factory=set)
    budget_start: float = field(default_factory=lambda: time.monotonic())
    wall_budget_s: float = field(default_factory=lambda: SEARCH_WALL_BUDGET_S)

    def wall_exceeded(self) -> bool:
        return (time.monotonic() - self.budget_start) >= self.wall_budget_s


def build_search_warnings(
    state: SearchBudgetState,
    *,
    wall_truncated: bool,
) -> str | None:
    """Compose non-exhaustive search warnings (friction 23000 + 24276)."""
    warnings: list[str] = []
    if state.skipped_converted:
        warnings.append(
            f"{state.skipped_converted} converted document(s) were NOT "
            "searched (extraction budget/cap) — results are NOT exhaustive "
            "over this tree. Do not certify 'zero remaining hits' from this "
            "response. (friction 23000)"
        )
    if state.skipped_oversized:
        warnings.append(
            f"{state.skipped_oversized} native file(s) exceeded the "
            f"{SEARCH_NATIVE_MAX_BYTES // (1024 * 1024)}MiB size cap and were NOT "
            "searched — results are NOT exhaustive over this tree. "
            "Narrow path=... or use a smaller scope. (friction 24276)"
        )
    if wall_truncated:
        warnings.append(
            f"Search stopped after {SEARCH_WALL_BUDGET_S}s wall budget "
            "(enumeration + scan) — results are NOT exhaustive. "
            "Narrow path=... for large trees. (friction 24276)"
        )
    return " ".join(warnings) if warnings else None


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
        if path.stat().st_size > SEARCH_NATIVE_MAX_BYTES:
            state.skipped_oversized += 1
            return None, None
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError:
        return None, None
