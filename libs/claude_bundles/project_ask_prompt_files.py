"""Resolve and load --prompt-file paths for project-ask (24951)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent


def project_root_base(repo: Path | None = None) -> Path:
    root = os.environ.get("PROJECT_ROOT", "").strip()
    base = Path(root) if root else (repo if repo is not None else _REPO)
    return base.resolve()


def resolve_prompt_path(raw: str, base: Path | None = None) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    resolved_base = base if base is not None else project_root_base()
    candidate = (resolved_base / path).resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError as exc:
        print(
            f"error: prompt-file {raw!r} resolves outside PROJECT_ROOT "
            f"({resolved_base}); use an absolute Jupiter-readable path "
            f"or a path under the checkout (e.g. tmp/reviews/your-prompt.md)",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return candidate


def load_prompt_files(
    prompt_files: list[str],
    *,
    base: Path | None = None,
    inline_prompt: str = "",
) -> list[str]:
    resolved_base = base if base is not None else project_root_base()
    loaded: list[str] = []
    for raw in prompt_files:
        path = resolve_prompt_path(raw, resolved_base)
        if not path.exists():
            _exit_unreadable(raw, path, resolved_base, "file not found")
        if not path.is_file():
            _exit_unreadable(raw, path, resolved_base, "not a regular file")
        try:
            loaded.append(path.read_text(encoding="utf-8"))
        except OSError as exc:
            _exit_unreadable(raw, path, resolved_base, str(exc))
    if inline_prompt.strip():
        loaded.append(inline_prompt.strip())
    return loaded


def _exit_unreadable(raw: str, path: Path, base: Path, detail: str) -> None:
    print(
        f"error: cannot read --prompt-file {raw!r} ({detail}; "
        f"resolved={path})\n"
        f"  Use a Jupiter-readable path under PROJECT_ROOT ({base}), "
        f"e.g. tmp/reviews/your-prompt.md",
        file=sys.stderr,
    )
    raise SystemExit(2)
