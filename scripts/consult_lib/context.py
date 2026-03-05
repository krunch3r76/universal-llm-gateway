"""Context file loading: read and expand file/directory arguments."""

from __future__ import annotations

from pathlib import Path


def read_context_files(paths: list[str]) -> list[str]:
    """Read files and return formatted content blocks."""
    blocks: list[str] = []
    for path_str in paths:
        path = Path(path_str)
        if path.is_dir():
            for child in sorted(path.rglob("*.py"))[:20]:
                try:
                    content = child.read_text(errors="replace")
                    if len(content) > 8000:
                        content = content[:8000] + "\n[... truncated]"
                    blocks.append(f"### {child}\n```\n{content}\n```")
                except OSError:
                    blocks.append(f"### {child}\n[Error: could not read file]")
        elif path.is_file():
            try:
                content = path.read_text(errors="replace")
                if len(content) > 12000:
                    content = content[:12000] + "\n[... truncated]"
                blocks.append(f"### {path}\n```\n{content}\n```")
            except OSError:
                blocks.append(f"### {path}\n[Error: could not read file]")
        else:
            blocks.append(f"### {path}\n[Error: file not found]")
    return blocks


def collect_context_file_paths(paths: list[str]) -> list[str]:
    """Expand context file arguments into concrete file paths."""
    files: list[str] = []
    for path_str in paths:
        path = Path(path_str)
        if path.is_dir():
            files.extend(
                str(child)
                for child in sorted(path.rglob("*"))
                if child.is_file() and ".git/" not in str(child)
            )
        elif path.is_file():
            files.append(str(path))
    # Preserve input order and remove duplicates.
    return list(dict.fromkeys(files))
