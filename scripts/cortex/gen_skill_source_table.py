#!/usr/bin/env python3
"""Generate committed skill source table from ``config/skills.yaml`` + SOT files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_OUTPUT = _REPO / "libs" / "implement_admission" / "skill_source_table.py"
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.catalog import load_skill_catalog  # noqa: E402

TEMPLATE_VERSION = "1"


def _render_module(uris: dict[str, str], aliases: dict[str, str]) -> str:
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(uris, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    q = json.dumps
    lines = [
        '"""Build-time generated canonical skill slug → source_uri table.',
        "",
        "Hot paths (packet render, boot, materialize) read this table only — never live",
        "``entity_get``. Generated from ``config/skills.yaml`` + SOT files",
        "(``scripts/cortex/gen_skill_source_table.py``).",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import json",
        "from typing import Final",
        "",
        "from cortex_store.guidance_entity import entity_slug_from_id",
        "",
        "# fmt: off",
        f'TEMPLATE_VERSION: Final[str] = "{TEMPLATE_VERSION}"',
        "",
        "# slug synonyms → canonical table key",
        "CANONICAL_SLUG_ALIASES: Final[dict[str, str]] = {",
    ]
    for alias, canonical in sorted(aliases.items()):
        lines.append(f"    {q(alias)}: {q(canonical)},")
    lines += [
        "}",
        "",
        "# Generated from skill catalog + SOT paths.",
        "CANONICAL_SKILL_SOURCE_URIS: Final[dict[str, str]] = {",
    ]
    for slug, uri in uris.items():
        lines.append(f"    {q(slug)}: {q(uri)},")
    lines += [
        "}",
        "",
        f"TABLE_DIGEST: Final[str] = {q(digest)}",
        "# fmt: on",
        "",
        "",
        "class SkillSourceResolveError(LookupError):",
        '    """Canonical slug absent from the committed resolver table."""',
        "",
        "",
        "def canonical_table_key(slug_or_entity_id: str) -> str:",
        '    """Normalize any entity id or bare slug to a canonical table key."""',
        "    raw = slug_or_entity_id.strip()",
        '    slug = entity_slug_from_id(raw) if ":" in raw else raw',
        "    return CANONICAL_SLUG_ALIASES.get(slug, slug)",
        "",
        "",
        "def canonical_agent_skill_id(slug_or_entity_id: str) -> str:",
        '    """Double-load exclusion key — always ``agent_skill:{canonical_slug}``."""',
        '    return f"agent_skill:{canonical_table_key(slug_or_entity_id)}"',
        "",
        "",
        "def resolve_canonical_source_uri(slug_or_entity_id: str) -> str:",
        '    """Map slug/entity id → ``source_uri`` via the committed table."""',
        "    key = canonical_table_key(slug_or_entity_id)",
        "    uri = CANONICAL_SKILL_SOURCE_URIS.get(key)",
        "    if not uri:",
        "        raise SkillSourceResolveError(",
        '            f"canonical slug {key!r} absent from skill source table "',
        '            f"(template_version={TEMPLATE_VERSION})"',
        "        )",
        "    return uri",
        "",
        "",
        "def table_bytes_for_digest() -> bytes:",
        '    """Stable serialization for determinism / freshness probes."""',
        "    return json.dumps(",
        '        CANONICAL_SKILL_SOURCE_URIS, sort_keys=True, separators=(",", ":")',
        "    ).encode()",
        "",
    ]
    return "\n".join(lines)


def build_from_catalog(repo_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    catalog = load_skill_catalog(repo_root=repo_root)
    uris = {slug: catalog.source_uri_for(slug) for slug in sorted(catalog.entries)}
    aliases = dict(catalog.alias_to_canonical)
    for slug in catalog.entries:
        catalog.resolve_sot(slug, repo_root)
    return uris, aliases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = (args.root or _REPO).resolve()
    uris, aliases = build_from_catalog(root)
    rendered = _render_module(uris, aliases)
    if args.check:
        current = _OUTPUT.read_text(encoding="utf-8") if _OUTPUT.is_file() else ""
        if current != rendered:
            print(
                "DRIFT: skill_source_table.py out of sync with catalog", file=sys.stderr
            )
            return 1
        print("OK gen_skill_source_table --check")
        return 0
    _OUTPUT.write_text(rendered, encoding="utf-8")
    try:
        subprocess.run(
            ["ruff", "format", str(_OUTPUT)],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass
    print(f"wrote {_OUTPUT} ({len(uris)} slugs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
