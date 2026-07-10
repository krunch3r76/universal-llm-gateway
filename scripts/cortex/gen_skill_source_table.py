#!/usr/bin/env python3
"""Generate committed skill source table from live Cortex entities (generation-time only)."""

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

from implement_admission.skill_source_table import (  # noqa: E402
    CANONICAL_SKILL_SOURCE_URIS,
)
from implement_admission.skill_table_freshness import (  # noqa: E402
    _entity_source_uri,
    validate_generation_invariants,
)
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

TEMPLATE_VERSION = "1"

CANONICAL_SLUG_ALIASES: dict[str, str] = {
    "session-close-kernel": "session-close",
}

# Slugs that must persist without a resolvable live source_uri (slug → uri).
_REQUIRED_EXTRA_SLUGS: dict[str, str] = {}

_EXIT_BYTE_DRIFT = 1
_EXIT_LIVE_READ_FAILURE = 2
_EXIT_INVARIANT_VIOLATION = 3
_EXIT_UNEXPECTED_DROP = 4
_EXIT_FORMATTER_DRIFT = 5


class GeneratorError(Exception):
    """Classified generator failure (D11)."""

    def __init__(self, reason: str, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.exit_code = exit_code


def _fail(reason: str, message: str, *, exit_code: int) -> None:
    raise GeneratorError(reason, message, exit_code=exit_code)


def canonical_table_key(slug_or_entity_id: str) -> str:
    raw = slug_or_entity_id.strip()
    if raw.startswith("agent_skill:"):
        slug = raw.removeprefix("agent_skill:")
    elif raw.startswith("rule:"):
        slug = raw.removeprefix("rule:")
    elif ":" in raw:
        slug = raw.split(":", 1)[1]
    else:
        slug = raw
    return CANONICAL_SLUG_ALIASES.get(slug, slug)


_WS = "workspaces://universal-llm-gateway"


def _workspace_source_uri(slug: str) -> str:
    return f"{_WS}/.cursor/skills/{slug}/SKILL.md"


def _normalize_committed_uri(source_uri: str) -> str:
    uri = source_uri.strip().removeprefix("files://")
    if uri.startswith("cortex://"):
        uri = uri.removeprefix("cortex://")
    ws_prefix = f"{_WS}/.cursor/skills/"
    if uri.startswith(ws_prefix) and uri.endswith("/SKILL.md"):
        return uri
    marker = "/agent-skills/"
    if marker in uri or uri.startswith("agent-skills/"):
        stem = uri.split(marker, 1)[-1] if marker in uri else uri.removeprefix("agent-skills/")
        slug = Path(stem).stem
        return _workspace_source_uri(slug)
    if uri.startswith("workspaces://"):
        return uri
    return uri


def _uris_for_slug(client, slug: str) -> dict[str, str]:
    uris: dict[str, str] = {}
    variants = {slug}
    canonical = canonical_table_key(slug)
    if canonical != slug:
        variants.add(canonical)
    for variant in variants:
        rule_resp = client.get(f"/entities/rule:{variant}?intent=full")
        if rule_resp.status_code == 200:
            uri = _entity_source_uri(rule_resp.json())
            if uri:
                uris[f"rule:{variant}"] = uri
        eid = f"agent_skill:{variant}"
        skill_resp = client.get(f"/entities/{eid}?intent=full")
        if skill_resp.status_code == 200:
            uri = _entity_source_uri(skill_resp.json())
            if uri:
                uris[eid] = uri
    return uris


def _preferred_uri(uris: dict[str, str]) -> str | None:
    for eid, uri in uris.items():
        if eid.startswith("rule:"):
            return uri
    return next(iter(uris.values()), None)


def _live_slugs(client) -> dict[str, str]:
    """Build slug → normalized source_uri from live entities (prefer rule:)."""
    entries: dict[str, str] = {}
    resp = client.get("/entities", params={"type": "agent_skill", "limit": 500})
    if resp.status_code != 200:
        _fail(
            "LIVE_READ_FAILURE",
            f"entity list failed: {resp.status_code}",
            exit_code=_EXIT_LIVE_READ_FAILURE,
        )
    items = resp.json().get("items") or []
    for row in items:
        eid = str(row.get("id") or "")
        if not eid.startswith("agent_skill:"):
            continue
        slug = eid.removeprefix("agent_skill:")
        if slug == "*":
            continue
        uris = _uris_for_slug(client, slug)
        preferred = _preferred_uri(uris)
        if preferred:
            entries[slug] = _normalize_committed_uri(preferred)
    return entries


def _check_unexpected_drop(live: dict[str, str]) -> None:
    live_keys = {canonical_table_key(slug) for slug in live}
    extra_keys = {canonical_table_key(slug) for slug in _REQUIRED_EXTRA_SLUGS}
    committed_keys = set(CANONICAL_SKILL_SOURCE_URIS)
    dropped = committed_keys - (live_keys | extra_keys)
    if dropped:
        _fail(
            "UNEXPECTED_DROP",
            f"committed slugs absent from live and extras: {sorted(dropped)}",
            exit_code=_EXIT_UNEXPECTED_DROP,
        )


def build_table(
    *,
    cortex_url: str = DEFAULT_CORTEX_URL,
    live_slugs: dict[str, str] | None = None,
) -> dict[str, str]:
    if live_slugs is None:
        try:
            with make_sync_client(cortex_url, timeout=30.0) as client:
                live = _live_slugs(client)
        except GeneratorError:
            raise
        except Exception as exc:
            _fail(
                "LIVE_READ_FAILURE",
                f"live read failed: {exc}",
                exit_code=_EXIT_LIVE_READ_FAILURE,
            )
    else:
        live = live_slugs

    _check_unexpected_drop(live)

    entries: dict[str, str] = {}
    for slug, uri in live.items():
        key = canonical_table_key(slug)
        if key not in entries:
            entries[key] = uri
        elif slug == key:
            entries[key] = uri
    for slug, uri in _REQUIRED_EXTRA_SLUGS.items():
        key = canonical_table_key(slug)
        entries[key] = _normalize_committed_uri(uri)

    errors = validate_generation_invariants(entries, aliases=dict(CANONICAL_SLUG_ALIASES))
    if errors:
        _fail(
            "INVARIANT_VIOLATION",
            "generation validation failed:\n" + "\n".join(errors),
            exit_code=_EXIT_INVARIANT_VIOLATION,
        )
    return dict(sorted(entries.items()))


def table_digest(entries: dict[str, str]) -> str:
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _render_module(entries: dict[str, str]) -> str:
    digest = table_digest(entries)
    lines = [
        '"""Build-time generated canonical skill slug → source_uri table (D1).',
        "",
        "Hot paths (packet render, boot, materialize) read this table only — never live",
        '``entity_get``. Generation-time validation and ``skill_table_freshness`` compare',
        "against live Cortex entities (F1).",
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
        "# slug synonyms → canonical table key (``entity_slug_from_id`` output may differ)",
        "CANONICAL_SLUG_ALIASES: Final[dict[str, str]] = {",
    ]
    for alias in sorted(CANONICAL_SLUG_ALIASES):
        canonical = CANONICAL_SLUG_ALIASES[alias]
        lines.append(f'    "{alias}": "{canonical}",')
    lines.extend(
        [
            "}",
            "",
            "# Generated from live Cortex entities — prefer substantiated ``rule:`` ``source_uri``.",
            "CANONICAL_SKILL_SOURCE_URIS: Final[dict[str, str]] = {",
        ]
    )
    for key in sorted(entries):
        value = entries[key].replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    "{key}": "{value}",')
    lines.extend(
        [
            "}",
            "",
            f'TABLE_DIGEST: Final[str] = "{digest}"',
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
            '    """Map slug/entity id → ``source_uri`` via the committed table (D1)."""',
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
        ]
    )
    return "\n".join(lines) + "\n"


def _check_formatter_stable(module_path: Path) -> None:
    for cmd in (
        ["ruff", "format", "--check", str(module_path)],
        ["black", "--check", str(module_path)],
    ):
        try:
            result = subprocess.run(
                cmd,
                cwd=_REPO,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            continue
        if result.returncode != 0:
            _fail(
                "FORMATTER_DRIFT",
                f"formatter {' '.join(cmd[:2])} would change {module_path.name}",
                exit_code=_EXIT_FORMATTER_DRIFT,
            )


def _emit_failure(exc: GeneratorError) -> int:
    print(f"REASON: {exc.reason} — {exc}", file=sys.stderr)
    return exc.exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify committed module matches live")
    parser.add_argument("--print-digest", action="store_true", help="Print table digest only")
    args = parser.parse_args()

    try:
        built = build_table()
    except GeneratorError as exc:
        return _emit_failure(exc)

    digest = table_digest(built)
    if args.print_digest:
        print(digest)
        return 0

    rendered = _render_module(built)

    if args.check:
        if not _OUTPUT.exists():
            print(f"REASON: BYTE_DRIFT — {_OUTPUT} missing", file=sys.stderr)
            return _EXIT_BYTE_DRIFT
        committed = _OUTPUT.read_text(encoding="utf-8")
        if committed != rendered:
            print(
                f"REASON: BYTE_DRIFT — {_OUTPUT.relative_to(_REPO)} differs from live render",
                file=sys.stderr,
            )
            return _EXIT_BYTE_DRIFT
        try:
            _check_formatter_stable(_OUTPUT)
        except GeneratorError as exc:
            return _emit_failure(exc)
        print(f"OK template_version={TEMPLATE_VERSION} digest={digest}")
        return 0

    _OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"WROTE {_OUTPUT.relative_to(_REPO)}")
    print(f"OK template_version={TEMPLATE_VERSION} digest={digest} rows={len(built)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
