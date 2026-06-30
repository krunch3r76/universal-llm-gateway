#!/usr/bin/env python3
"""Backfill empty ``trigger_match_terms`` on active agent_skill entities.

Derives deterministic keyword terms from slug, trigger_short, skill_category,
and description, writes them to cortex SOT or workspace SKILL.md frontmatter,
then syncs via ``ingest_skills.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_related_parse import parse_frontmatter  # noqa: E402
from cortex_store.routes._skill_suggest import STOPWORDS  # noqa: E402
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9_+.-]+")
_FOL_OPERATORS = {"∨", "∧", "⇒", "⇔", "¬", "→", "∈", "∉", "∪", "∩", "⊆", "⊂", "|"}
_MAX_TERMS = 12
_PRESERVE_SLUGS = frozenset(
    {
        "completion-provenance-discipline",
        "consensus-steelman-posture",
        "consult-routing",
        "cursor-sdk-instruction-standard",
        "dispatch-shape",
        "implement-work-item",
        "lead-agent-git-integration",
        "lead-seat-boot",
        "session-close",
        "subgraph-render",
    }
)
_PROCEDURAL_STOPWORDS = frozenset(
    {"before", "when", "task", "read", "agent", "session", "any", "use"}
)
_SUPPRESSED = frozenset({"deprecated", "retired", "merged"})


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT_RE.split(text.lower()) if t]


def _domain_tokens(slug: str, skill_category: str) -> set[str]:
    out = set(_tokenize(slug))
    out.update(_tokenize(skill_category))
    if skill_category:
        out.add(skill_category.lower())
    return out


def _keep_term(term: str, *, domain_tokens: set[str]) -> bool:
    low = term.lower()
    if len(low) <= 2:
        return False
    if low in STOPWORDS or low in _PROCEDURAL_STOPWORDS:
        return low in domain_tokens
    return True


def derive_trigger_match_terms(
    slug: str,
    *,
    trigger_short: str = "",
    skill_category: str = "",
    description: str = "",
) -> list[str]:
    """Deterministic H3 derivation — cap 12, idempotent."""
    domain = _domain_tokens(slug, skill_category)
    terms: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        text = raw.strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        if not _keep_term(text, domain_tokens=domain):
            return
        seen.add(key)
        terms.append(text)

    add(slug)
    add(slug.replace("-", "_"))

    trigger_raw = trigger_short or ""
    for op in _FOL_OPERATORS:
        trigger_raw = trigger_raw.replace(op, " ")
    for tok in _tokenize(trigger_raw):
        add(tok)

    for tok in _tokenize(skill_category):
        add(tok)
    if skill_category and "-" in skill_category:
        add(skill_category)

    desc = (description or "")[:120]
    for tok in _tokenize(desc):
        add(tok)

    return terms[:_MAX_TERMS]


def derive_trigger_match_terms_from_vocab(
    slug: str,
    *,
    vocab_rows: list[tuple[str, str, str, float, int]],
    top_n: int = _MAX_TERMS,
) -> list[str]:
    """Top-N terms by score from skill_vocabulary rows for one slug."""
    slug_rows = [row for row in vocab_rows if row[0] == slug]
    slug_rows.sort(key=lambda row: (-row[3], row[2]))
    terms: list[str] = []
    seen: set[str] = set()
    for _slug, _register, term, _score, _chunks in slug_rows:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= top_n:
            break
    return terms


async def _load_skill_vocabulary_rows(
    db_path: Path | None = None,
) -> list[tuple[str, str, str, float, int]]:
    from services.rag.property_index import PropertyIndex

    idx = PropertyIndex(db_path=db_path) if db_path else PropertyIndex()
    await idx.start()
    try:
        conn = idx._ensure_conn()
        rows = conn.execute(
            "SELECT slug, register, term, score, chunk_count"
            " FROM skill_vocabulary"
            " ORDER BY slug ASC, score DESC, term ASC"
        ).fetchall()
        return [
            (str(r[0]), str(r[1]), str(r[2]), float(r[3]), int(r[4])) for r in rows
        ]
    finally:
        await idx.stop()


def _cortex_files_root() -> Path:
    return Path(
        os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
    ).expanduser()


def _patch_entity_terms(
    client: object, entity_id: str, terms: list[str], *, dry_run: bool
) -> bool:
    if dry_run:
        return True
    q = urllib.parse.quote(entity_id, safe=":")
    status, live = _entity_get(client, entity_id)
    if status != 200:
        print(f"  FAIL  {entity_id:40s}  [GET {status}]", file=sys.stderr)
        return False
    attrs = dict(live.get("attributes") or {})
    attrs["trigger_match_terms"] = terms
    patch_status, body = _request(
        client,
        "PATCH",
        f"/entities/{q}",
        body={"attributes": attrs},
    )
    if patch_status not in (200, 201):
        print(
            f"  FAIL  {entity_id:40s}  [PATCH {patch_status}] {body}",
            file=sys.stderr,
        )
        return False
    print(f"  ok    {entity_id:40s}  [PATCH attrs]")
    return True


def _skill_paths(slug: str, root: Path) -> tuple[Path | None, Path | None]:
    cortex_path = _cortex_files_root() / "agent-skills" / f"{slug}.md"
    workspace_path = root / ".cursor" / "skills" / slug / "SKILL.md"
    return (
        cortex_path if cortex_path.is_file() else None,
        workspace_path if workspace_path.is_file() else None,
    )


def _existing_frontmatter_terms(path: Path) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = parse_frontmatter(text)
    terms = fm.get("trigger_match_terms")
    if isinstance(terms, list) and terms:
        return [str(v) for v in terms]
    return None


def _patch_frontmatter_terms(path: Path, terms: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    line = f"trigger_match_terms: {json.dumps(terms, ensure_ascii=False)}"
    match = _FRONTMATTER_RE.match(text)
    if match:
        body = text[match.end() :]
        fm_lines = match.group(1).splitlines()
        out_fm: list[str] = []
        replaced = False
        for fm_line in fm_lines:
            if fm_line.startswith("trigger_match_terms:"):
                out_fm.append(line)
                replaced = True
            else:
                out_fm.append(fm_line)
        if not replaced:
            out_fm.append(line)
        new_text = "---\n" + "\n".join(out_fm) + "\n---" + body
    else:
        new_text = f"---\n{line}\n---\n\n{text.lstrip()}"
    path.write_text(new_text, encoding="utf-8")


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
    return _request(client, "GET", f"/entities/{q}")


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


def _entity_slug(entity_id: str) -> str:
    return entity_id.removeprefix("agent_skill:")


def _entity_terms(row: dict) -> list[str]:
    attrs = row.get("attributes") or {}
    if not isinstance(attrs, dict):
        return []
    terms = attrs.get("trigger_match_terms")
    return [str(t) for t in terms] if isinstance(terms, list) else []


def _entity_fields(row: dict) -> tuple[str, str, str, str]:
    slug = _entity_slug(str(row.get("id") or ""))
    attrs = row.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    trigger_short = str(attrs.get("trigger_short") or "")
    skill_category = str(attrs.get("skill_category") or "")
    description = str(row.get("description") or "")
    return slug, trigger_short, skill_category, description


def audit(client: object) -> int:
    rows = _fetch_active_skills(client)
    empty = [row for row in rows if not _entity_terms(row)]
    print(f"Audit: trigger_match_terms on {len(rows)} active agent_skill entities")
    print(f"  Empty/missing terms: {len(empty)}")
    for row in sorted(empty, key=lambda r: str(r.get("id"))):
        print(f"    - {row.get('id')}")
    return 0 if not empty else 1


def _run_ingest(root: Path, *, dry_run: bool) -> None:
    cmd = [
        sys.executable,
        str(_SCRIPTS_CORTEX / "ingest_skills.py"),
        "--root",
        str(root),
    ]
    if dry_run:
        cmd.append("--dry-run")
    subprocess.run(cmd, check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=_REPO)
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Skip post-write ingest_skills.py sync",
    )
    parser.add_argument(
        "--source",
        choices=("deterministic", "vocab"),
        default="deterministic",
        help="Term derivation source: slug/description heuristics or skill_vocabulary",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Metadata DB path for --source vocab (default: ~/.rag/store/rag_metadata.db)",
    )
    args = parser.parse_args(argv)

    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2

    if args.audit:
        return audit(client)

    rows = _fetch_active_skills(client)
    vocab_rows: list[tuple[str, str, str, float, int]] = []
    if args.source == "vocab":
        vocab_rows = asyncio.run(_load_skill_vocabulary_rows(args.db_path))
        if not vocab_rows:
            print(
                "ERROR: --source vocab requires populated skill_vocabulary"
                " (run scripts/rag/attribute_skill_vocabulary.py first)",
                file=sys.stderr,
            )
            return 2

    pending_files: list[tuple[str, list[str], Path]] = []
    pending_entities: list[tuple[str, list[str], str]] = []
    skipped_preserve = 0
    skipped_frontmatter = 0

    for row in rows:
        if _entity_terms(row):
            continue
        slug, trigger_short, skill_category, description = _entity_fields(row)
        entity_id = str(row.get("id") or f"agent_skill:{slug}")
        if slug in _PRESERVE_SLUGS:
            skipped_preserve += 1
            continue
        cortex_path, workspace_path = _skill_paths(slug, args.root.resolve())
        target = cortex_path or workspace_path
        if target is not None:
            existing = _existing_frontmatter_terms(target)
            if existing:
                skipped_frontmatter += 1
                continue
        terms = (
            derive_trigger_match_terms_from_vocab(slug, vocab_rows=vocab_rows)
            if args.source == "vocab"
            else derive_trigger_match_terms(
                slug,
                trigger_short=trigger_short,
                skill_category=skill_category,
                description=description,
            )
        )
        if target is not None:
            pending_files.append((slug, terms, target))
        else:
            pending_entities.append((slug, terms, entity_id))

    print(f"Backfill trigger_match_terms: {len(pending_files)} file(s), {len(pending_entities)} entity PATCH")
    print(f"  preserve-set skip: {skipped_preserve}")
    print(f"  frontmatter-already-set skip: {skipped_frontmatter}")
    failures = 0
    for slug, terms, target in sorted(pending_files):
        label = "WOULD WRITE" if args.dry_run else "WRITE"
        print(f"  {label}  {slug:40s}  {terms}  -> {target}")
        if not args.dry_run:
            _patch_frontmatter_terms(target, terms)
    for slug, terms, entity_id in sorted(pending_entities):
        label = "WOULD PATCH" if args.dry_run else "PATCH"
        print(f"  {label}  {slug:40s}  {terms}  -> {entity_id}")
        if not _patch_entity_terms(client, entity_id, terms, dry_run=args.dry_run):
            failures += 1

    if (pending_files or pending_entities) and not args.dry_run and not args.no_ingest:
        print("\nSyncing entities via ingest_skills.py …")
        _run_ingest(args.root.resolve(), dry_run=False)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
