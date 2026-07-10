#!/usr/bin/env python3
"""One-shot bootstrap: entity.description → workspace SOT frontmatter ``description:``.

Steady-state sync is file → entity via ``ingest_skills.py``; this script fills
empty/missing frontmatter on resolvable ``.cursor/skills/<slug>/SKILL.md`` files only.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from io import StringIO
from pathlib import Path

try:
    from ruamel.yaml import YAML
except ImportError:
    print("ERROR: ruamel.yaml required — pip install ruamel.yaml", file=sys.stderr)
    sys.exit(2)

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_related_parse import parse_frontmatter  # noqa: E402
from cortex_store.routes.boot._skill_trigger import (  # noqa: E402
    _parse_frontmatter_description,
)
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_SUPPRESSED = frozenset({"deprecated", "retired", "merged"})


def _request(
    client: object, method: str, path: str, body: dict | None = None
) -> tuple[int, dict]:
    kwargs: dict = {"json": body} if body is not None else {}
    resp = client.request(method, path, **kwargs)
    try:
        data = resp.json()
    except Exception:
        data = {}
    return resp.status_code, data


def _entity_get(client: object, entity_id: str) -> tuple[int, dict]:
    q = urllib.parse.quote(entity_id, safe=":")
    return _request(client, "GET", f"/entities/{q}?intent=full")


def _fetch_active_skills(client: object) -> list[dict]:
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        raise RuntimeError(f"GET /entities?type=agent_skill failed: {status}")
    rows: list[dict] = []
    for stub in body.get("items") or []:
        entity_id = str(stub.get("id") or "")
        if not entity_id.startswith("agent_skill:"):
            continue
        get_status, live = _entity_get(client, entity_id)
        if get_status != 200:
            raise RuntimeError(f"GET /entities/{entity_id} failed: {get_status}")
        if live.get("lifecycle") in _SUPPRESSED:
            continue
        rows.append(live)
    return rows


def _resolve_workspace_sot_path(source_uri: str | None, slug: str) -> Path | None:
    candidate = _REPO / ".cursor" / "skills" / slug / "SKILL.md"
    if candidate.is_file():
        return candidate
    if source_uri and ".cursor/skills/" in source_uri:
        return None
    return None


def _has_parseable_frontmatter(text: str) -> bool:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return False
    yaml = YAML(typ="safe")
    try:
        parsed = yaml.load(match.group(1))
    except Exception:
        return False
    return isinstance(parsed, dict)


def _file_frontmatter_description(text: str) -> str:
    fm = parse_frontmatter(text)
    return str(fm.get("description") or "").strip()


def _is_malformed_entity_description(description: str) -> bool:
    text = (description or "").strip()
    if not text:
        return True
    if text == ">-" or text.startswith(">-") or text.startswith("|"):
        return True
    if "\n" in text or "\r" in text:
        return True
    return not _round_trips_parser(text)


def _round_trips_parser(description: str) -> bool:
    """Probe single-line emission against boot ``_parse_frontmatter_description``."""
    yaml = YAML()
    yaml.width = 4096
    yaml.default_flow_style = False
    buf = StringIO()
    yaml.dump({"description": description}, buf)
    block = buf.getvalue().rstrip("\n")
    if "description:" not in block:
        return False
    for line in block.splitlines():
        if line.startswith("description:"):
            raw = line.split(":", 1)[1].strip()
            if raw.startswith(">-") or raw.startswith("|"):
                return False
    synthetic = f"---\n{block}\n---\n\n# Body\n"
    parsed = _parse_frontmatter_description(synthetic)
    return parsed == description


def _patch_frontmatter_description(path: Path, description: str) -> None:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"no frontmatter block: {path}")
    body = text[match.end() :]
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.default_flow_style = False
    fm = yaml.load(match.group(1))
    if not isinstance(fm, dict):
        raise ValueError(f"unparseable frontmatter: {path}")
    fm["description"] = description
    buf = StringIO()
    yaml.dump(fm, buf)
    new_fm = buf.getvalue().rstrip("\n")
    for line in new_fm.splitlines():
        if line.startswith("description:"):
            raw = line.split(":", 1)[1].strip()
            if raw.startswith(">-") or raw.startswith("|"):
                raise ValueError(f"emitter produced block/fold scalar: {path}")
    new_text = f"---\n{new_fm}\n---{body}"
    round_tripped = _parse_frontmatter_description(new_text)
    if round_tripped != description:
        raise ValueError(f"round-trip mismatch after write: {path}")
    path.write_text(new_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2

    rows = _fetch_active_skills(client)
    backfilled = 0
    kept_divergent = 0
    skipped_unresolvable: list[str] = []
    skipped_malformed: list[str] = []

    for row in sorted(rows, key=lambda r: str(r.get("id"))):
        entity_id = str(row.get("id") or "")
        slug = entity_id.removeprefix("agent_skill:")
        source_uri = str(row.get("source_uri") or "")
        path = _resolve_workspace_sot_path(source_uri, slug)
        if path is None:
            skipped_unresolvable.append(entity_id)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            skipped_unresolvable.append(entity_id)
            continue
        if not _has_parseable_frontmatter(text):
            skipped_malformed.append(entity_id)
            continue
        entity_desc = str(row.get("description") or "").strip()
        file_desc = _file_frontmatter_description(text)
        if file_desc:
            if file_desc != entity_desc:
                kept_divergent += 1
            continue
        if _is_malformed_entity_description(entity_desc):
            skipped_malformed.append(entity_id)
            continue
        label = "WOULD BACKFILL" if args.dry_run else "BACKFILL"
        print(f"  {label}  {slug:40s}  -> {path}")
        if not args.dry_run:
            try:
                _patch_frontmatter_description(path, entity_desc)
            except ValueError as exc:
                print(f"  FAIL  {slug:40s}  {exc}", file=sys.stderr)
                skipped_malformed.append(entity_id)
                continue
        backfilled += 1

    print(
        f"Counts: backfilled={backfilled} kept-file-divergent={kept_divergent} "
        f"skipped-unresolvable={len(skipped_unresolvable)} "
        f"skipped-malformed={len(skipped_malformed)}"
    )
    if skipped_unresolvable:
        print("\nSkipped (unresolvable source_uri):")
        for eid in skipped_unresolvable:
            print(f"  - {eid}")
    if skipped_malformed:
        print("\nSkipped (malformed entity description or frontmatter):")
        for eid in skipped_malformed:
            print(f"  - {eid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
