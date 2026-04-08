#!/usr/bin/env python3
"""Prepare a Hafiz corpus into one markdown file per ghazal."""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

_FILES_ROOT_CANDIDATES = (
    Path("/data/files"),
    Path("/mnt/torus/mcp-data/files"),
)
_HEADER_RE = re.compile(r"^##\s+poem_id=(?P<poem_id>\d+)\s+\|\s+form=(?P<form>.+)$")
_GHAZAL_FORMS = {"غزل", "غزلیات", "<unk>"}
_THEME_KEYWORDS = {
    "love": ("عشق", "یار", "دلبر", "وصال", "فراق"),
    "wine": ("می", "باده", "ساقی", "جام", "شراب"),
    "garden": ("گل", "بلبل", "چمن", "باغ", "لاله"),
    "night": ("شب", "سحر", "صبح", "مهتاب", "ماه"),
    "mystical": ("رند", "خرابات", "صوفی", "عارف", "طریقت"),
    "journey": ("صبا", "باد", "کاروان", "منزل", "ره"),
}


@dataclass(slots=True)
class PoemSection:
    poem_id: int
    form: str
    lines: list[str]


def _default_files_root() -> Path:
    for candidate in _FILES_ROOT_CANDIDATES:
        if candidate.exists():
            return candidate
    return _FILES_ROOT_CANDIDATES[0]


def parse_args() -> argparse.Namespace:
    files_root = _default_files_root()
    parser = argparse.ArgumentParser(
        description="Split a sectioned Hafiz corpus into one markdown file per ghazal."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=files_root / "poetry/hafiz/divan-hafiz.txt",
        help="Sectioned Hafiz source file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=files_root / "poetry/hafiz/processed",
        help="Directory for per-ghazal markdown files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing generated markdown files before writing.",
    )
    return parser.parse_args()


def _parse_sections(text: str) -> list[PoemSection]:
    sections: list[PoemSection] = []
    current_header: tuple[int, str] | None = None
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header_match = _HEADER_RE.match(line)
        if header_match is not None:
            if current_header is not None and current_lines:
                sections.append(
                    PoemSection(
                        poem_id=current_header[0],
                        form=current_header[1],
                        lines=current_lines.copy(),
                    )
                )
            current_header = (
                int(header_match.group("poem_id")),
                header_match.group("form").strip(),
            )
            current_lines = []
            continue
        if current_header is not None:
            current_lines.append(line)

    if current_header is not None and current_lines:
        sections.append(
            PoemSection(
                poem_id=current_header[0],
                form=current_header[1],
                lines=current_lines.copy(),
            )
        )
    return sections


def _is_ghazal(section: PoemSection) -> bool:
    return section.form.strip() in _GHAZAL_FORMS


def _infer_themes(lines: list[str]) -> list[str]:
    text = "\n".join(lines)
    themes: list[str] = []
    for theme, keywords in _THEME_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            themes.append(theme)
    return themes


def _render_poem(section: PoemSection, themes: list[str]) -> str:
    theme_line = ", ".join(themes)
    poem_text = "\n".join(section.lines)
    return (
        "---\n"
        "poet: hafiz\n"
        "form: ghazal\n"
        f"themes: [{theme_line}]\n"
        "language: fa\n"
        f"source_poem_id: {section.poem_id}\n"
        f"source_form: {section.form}\n"
        "---\n\n"
        f"{poem_text}\n"
    )


def _prepare_output_dir(output_dir: Path, force: bool) -> None:
    if output_dir.exists() and force:
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    source_path = args.input.expanduser()
    output_dir = args.output_dir.expanduser()

    if not source_path.exists():
        raise FileNotFoundError(f"Input corpus not found: {source_path}")

    _prepare_output_dir(output_dir, force=args.force)
    sections = _parse_sections(source_path.read_text(encoding="utf-8"))

    written = 0
    for section in sections:
        if not _is_ghazal(section):
            continue
        if len(section.lines) < 2:
            continue
        written += 1
        output_path = output_dir / f"hafiz-ghazal-{written:03d}.md"
        output_path.write_text(
            _render_poem(section, _infer_themes(section.lines)),
            encoding="utf-8",
        )

    print(f"Wrote {written} ghazal file(s) to {output_dir}")


if __name__ == "__main__":
    main()
