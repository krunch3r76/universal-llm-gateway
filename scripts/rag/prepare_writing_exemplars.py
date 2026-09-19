#!/usr/bin/env python3
"""Split curated Gutenberg essay volumes into one markdown file per essay.

Twin of ``prepare_persian_poetry.py``: small processed units with YAML
frontmatter, under
``mcp-data/files/writing-exemplars/{register}/{author}/processed/``.
``Volume.form`` is the pipeline ``register`` dirname. Does not index the
raw aleph mirror.
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

_ALEPH = Path("/mnt/torus/guten/harvest/aleph.gutenberg.org")
_FILES_ROOT_CANDIDATES = (
    Path("/data/files"),
    Path("/mnt/torus/mcp-data/files"),
)
_START_RE = re.compile(
    r"\*\*\*\s*START OF (?:THIS|THE) PROJECT GUTENBERG EBOOK .*?\*\*\*",
    re.I | re.S,
)
_END_RE = re.compile(
    r"\*\*\*\s*END OF (?:THIS|THE) PROJECT GUTENBERG EBOOK .*?\*\*\*",
    re.I,
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TWO_LINE_ROMAN = re.compile(
    r"^([IVXLC]+)\.\s*\n([A-Z][A-Z \-']+|[Oo]f .+)$",
    re.M,
)


def _files_root() -> Path:
    for candidate in _FILES_ROOT_CANDIDATES:
        if candidate.exists():
            return candidate
    return _FILES_ROOT_CANDIDATES[0]


@dataclass(frozen=True, slots=True)
class Volume:
    author: str
    form: str
    pg_id: int
    language: str
    titles: tuple[str, ...] = ()


# a:35607 humor first-wave zip-ok; Milne 5803 splits on titled essays.
_MILNE_TITLES: tuple[str, ...] = (
    "The Pleasure of Writing",
    "Acacia Road",
    "My Library",
    "The Chase",
    "Superstition",
    "The Charm of Golf",
    "Goldfish",
    "Saturday to Monday",
    "The Pond",
    "A Seventeenth-century Story",
    "Our Learned Friends",
    "A Word for Autumn",
    "A Christmas Number",
    "No Flowers by Request",
    "The Unfairness of Things",
    "Daffodils",
    "A Household Book",
    "Lunch",
    "The Friend of Man",
    "The Diary Habit",
    "Midsummer Day",
    "At the Bookstall",
    '"Who\'s Who"',
    "A Day at Lord's",
    "By the Sea",
    "Golden Fruit",
    "Signs of Character",
    "Intellectual Snobbery",
    "A Question of Form",
    "A Slice of Fiction",
    "The Label",
    "The Profession",
    "Smoking as a Fine Art",
    "The Path to Glory",
    "A Problem in Ethics",
    "The Happiest Half-hours of Life",
    "Natural Science",
    "On Going Dry",
    "A Misjudged Game",
    "A Doubtful Character",
    "Thoughts on Thermometers",
    "For a Wet Afternoon",
    "Declined with Thanks",
    "On Going into a House",
    "The Ideal Author",
)

VOLUMES: tuple[Volume, ...] = (
    Volume("emerson", "familiar", 2944, "en"),
    Volume(
        "twain",
        "familiar",
        3250,
        "en",
        titles=(
            "HOW TO TELL A STORY",
            "THE WOUNDED SOLDIER",
            "THE GOLDEN ARM",
            "MENTAL TELEGRAPHY AGAIN",
            "THE INVALID'S STORY",
        ),
    ),
    Volume("du-bois", "civic", 408, "en"),
    Volume("milne", "humor", 5803, "en", titles=_MILNE_TITLES),
)


def _aleph_zip(pg_id: int) -> Path:
    text = str(pg_id)
    parts = list(text[:-1]) + [text] if len(text) > 1 else [text]
    folder = _ALEPH.joinpath(*parts)
    for name in (f"{pg_id}-0.zip", f"{pg_id}.zip", f"{pg_id}-8.zip"):
        path = folder / name
        if path.exists():
            return path
    raise FileNotFoundError(f"No Gutenberg zip for {pg_id} under {folder}")


def _read_zip_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".txt"))
        return archive.read(name).decode("utf-8", errors="replace")


def _strip_boilerplate(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    start = _START_RE.search(text)
    end = _END_RE.search(text)
    if start and end and end.start() > start.end():
        return text[start.end() : end.start()].strip()
    return text.strip()


def _slug(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug or "untitled"


def _split_two_line_roman(body: str) -> list[tuple[str, str]]:
    matches = list(_TWO_LINE_ROMAN.finditer(body))
    pieces: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        title = re.sub(r"\s+", " ", match.group(2)).strip()
        pieces.append((title, body[match.end() : end].strip()))
    return pieces


def _split_named_titles(body: str, titles: tuple[str, ...]) -> list[tuple[str, str]]:
    """Use the last occurrence of each title as the heading (skip contents)."""
    found: list[tuple[int, str]] = []
    for title in titles:
        last = None
        for match in re.finditer(
            rf"^{re.escape(title)}\.?\s*$", body, flags=re.M | re.I
        ):
            last = match
        if last is not None:
            found.append((last.start(), title.title()))
    found.sort()
    pieces: list[tuple[str, str]] = []
    for i, (start, title) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(body)
        heading = re.match(r"^[^\n]+\n", body[start:end])
        text_start = start + (heading.end() if heading else 0)
        pieces.append((title, body[text_start:end].strip()))
    return pieces


def _split_du_bois(body: str) -> list[tuple[str, str]]:
    pieces = _split_two_line_roman(body)
    first_chapter = _TWO_LINE_ROMAN.search(body)
    fore = re.search(r"^The Forethought\s*$", body, re.M)
    if fore and first_chapter and first_chapter.start() > fore.end():
        text = body[fore.end() : first_chapter.start()].strip()
        if len(text) >= 200:
            pieces.insert(0, ("The Forethought", text))
    after = re.search(r"^The Afterthought\s*$", body, re.M)
    if after:
        text = body[after.end() :].strip()
        if len(text) >= 200:
            pieces.append(("The Afterthought", text))
    return pieces


def _render(volume: Volume, title: str, text: str) -> str:
    return (
        "---\n"
        f"author: {volume.author}\n"
        f"form: {volume.form}\n"
        f"language: {volume.language}\n"
        f"source_pg: {volume.pg_id}\n"
        f"title: {title}\n"
        "---\n\n"
        f"{text.rstrip()}\n"
    )


def _prepare_dir(path: Path, force: bool) -> None:
    if path.exists() and force:
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _split_volume(volume: Volume, body: str) -> list[tuple[str, str]]:
    if volume.titles:
        return _split_named_titles(body, volume.titles)
    if volume.author == "du-bois":
        return _split_du_bois(body)
    return _split_two_line_roman(body)


def parse_args() -> argparse.Namespace:
    root = _files_root()
    parser = argparse.ArgumentParser(
        description="Prepare Gutenberg essay volumes as writing-exemplar markdown."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=root / "writing-exemplars",
        help="Register/author folders land under this root.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing processed markdown before writing.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    written = 0
    for volume in VOLUMES:
        body = _strip_boilerplate(_read_zip_text(_aleph_zip(volume.pg_id)))
        pieces = [(t, p) for t, p in _split_volume(volume, body) if len(p) >= 200]
        out_dir = args.output_root / volume.form / volume.author / "processed"
        _prepare_dir(out_dir, force=args.force)
        for index, (title, text) in enumerate(pieces, start=1):
            path = out_dir / f"{volume.author}-{index:03d}-{_slug(title)}.md"
            path.write_text(_render(volume, title, text), encoding="utf-8")
            written += 1
        print(f"{volume.author}: {len(pieces)} unit(s) → {out_dir}")
    print(f"Wrote {written} file(s)")


if __name__ == "__main__":
    main()
