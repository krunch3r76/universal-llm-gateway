"""On-demand dropbox PDF ingest (plan ingest-pdf-v1 D3)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from mcp_events import record

from ._extraction_profile import load_default_profile
from ._file_helpers import FILES_ROOT
from ._ingest_dropbox_pass import process_file
from ._ingest_dropbox_select import parse_dropbox_rel, route_pdf, select_paths
from ._ingest_dropbox_state import (
    config_path,
    done_path_is_duplicate,
    find_promoted_bundle,
    iter_state_records,
    load_state,
)
from .extract_document import OcrUnreachable

if TYPE_CHECKING:
    from fastmcp import FastMCP


@dataclass(frozen=True, slots=True)
class IngestConfig:
    canonical: str
    check: str
    family_map: list[tuple[str, str]]
    since: str
    cap: int
    ctx_allowlist: set[str]


def _parse_family_map(raw: Any) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if isinstance(item, dict):
            match = item.get("match")
            family = item.get("family")
            if isinstance(match, str) and isinstance(family, str):
                rows.append((match, family))
    return rows


def _model_family(model: str, family_map: list[tuple[str, str]]) -> str | None:
    for pattern, family in family_map:
        if fnmatch.fnmatch(model, pattern):
            return family
    return None


def load_config(path: Path | None = None) -> IngestConfig:
    cfg_path = path or config_path()
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("ocr-review config must be a mapping")
    canonical = str(data.get("canonical", ""))
    check = str(data.get("check", ""))
    family_map = _parse_family_map(data.get("family_map"))
    profile = load_default_profile()
    if canonical != profile.model:
        raise ValueError("canonical model must match document-extraction-v1 profile")
    if _model_family(canonical, family_map) == _model_family(check, family_map):
        raise ValueError("check model must be a different family from canonical")
    selection = data.get("selection") or {}
    since = str(selection.get("since", "2026-10-18"))
    cap = int(selection.get("cap", 5))
    allow = selection.get("ctx_allowlist") or ["cortex_finance", "cortex_legal"]
    ctx_allowlist = {str(x) for x in allow}
    return IngestConfig(
        canonical=canonical,
        check=check,
        family_map=family_map,
        since=since,
        cap=cap,
        ctx_allowlist=ctx_allowlist,
    )


def reconcile_promote_stages(_config: dict[str, Any]) -> None:
    from ._ingest_dropbox_state import (
        iter_state_records,
        load_state,
        save_state,
        try_ingest_lock,
    )

    for rec in list(iter_state_records()):
        if rec.get("stage") != "promote" or rec.get("terminal"):
            continue
        sha = rec["source_sha256"]
        rel = rec["source_path"]
        if (FILES_ROOT / rel).exists():
            continue
        with try_ingest_lock(sha) as locked:
            if not locked:
                continue
            current = load_state(sha) or rec
            entity_id = current.get("entity_id") or _entity_id_from_path(rel)
            bundle = find_promoted_bundle(sha, rel)
            if bundle and _entity_hash(entity_id) == sha:
                current["stage"] = "done"
                current["entity_id"] = entity_id
                current["last_error"] = None
                record(
                    "mcp.ingest.state.advanced",
                    path=rel,
                    from_stage="promote",
                    to_stage="done",
                    terminal=None,
                    last_error_kind=None,
                )
            else:
                current["terminal"] = "needs_human"
                current["last_error"] = "missing_after_promote"
                record(
                    "mcp.ingest.state.advanced",
                    path=rel,
                    from_stage="promote",
                    to_stage="promote",
                    terminal="needs_human",
                    last_error_kind="missing_after_promote",
                )
            save_state(sha, current)


def _entity_id_from_path(rel: str) -> str:
    import re

    stem = Path(rel).stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return f"document:{slug or 'document'}"


def _entity_hash(entity_id: str) -> str | None:
    from tools import _evidence_entity_ops as entity_ops

    try:
        resp = entity_ops.cx(
            "POST",
            "/mcp",
            {"tool": "entity_get", "arguments": {"entity_id": entity_id}},
        )
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(resp, dict):
        return None
    return resp.get("content_hash")


def _config_dict(config: IngestConfig) -> dict[str, Any]:
    return {
        "canonical": config.canonical,
        "check": config.check,
        "family_map": config.family_map,
    }


def _newest_as_of() -> str | None:
    latest: str | None = None
    for rec in iter_state_records():
        ts = rec.get("updated_at")
        if isinstance(ts, str) and (latest is None or ts > latest):
            latest = ts
    return latest


def _should_skip_for_cap(rel: str) -> bool:
    parsed = parse_dropbox_rel(rel)
    if parsed is None:
        return True
    abs_path = FILES_ROOT / rel
    if not abs_path.is_file():
        return False
    from ._extract_document_helpers import compute_source_sha256

    try:
        sha, _ = compute_source_sha256(abs_path)
    except OSError:
        return False
    rec = load_state(sha)
    if not rec:
        return False
    return bool(rec.get("terminal"))


def ingest_dropbox_run(
    *,
    paths: list[str] | None = None,
    ctx: str | None = None,
    since: str | None = None,
    cap: int | None = None,
) -> dict[str, Any]:
    config = load_config()
    eff_since = since or config.since
    eff_cap = cap if cap is not None else config.cap
    reconcile_promote_stages(_config_dict(config))
    cfgd = _config_dict(config)

    skip = (lambda _rel: False) if paths is not None else _should_skip_for_cap
    selected = select_paths(
        paths=paths,
        ctx=ctx,
        since=eff_since,
        cap=eff_cap,
        ctx_allowlist=config.ctx_allowlist,
        skip_predicate=skip,
    )

    processed: list[dict[str, str]] = []
    stopped = False
    stopped_path: str | None = None
    budget = eff_cap

    def cap_counter() -> bool:
        nonlocal budget
        if budget <= 0:
            return False
        budget -= 1
        return True

    for rel in selected:
        try:
            outcome, stop = process_file(cfgd, rel, cap_counter=cap_counter)
        except OcrUnreachable:
            outcome, stop = "attempt", True
        processed.append({"path": rel, "outcome": outcome})
        if stop:
            stopped = True
            stopped_path = rel
            break
    return {
        "processed": processed,
        "stopped": stopped,
        "stopped_path": stopped_path,
    }


def ingest_dropbox_dry_run(
    *,
    paths: list[str] | None = None,
    ctx: str | None = None,
    since: str | None = None,
    cap: int | None = None,
) -> dict[str, Any]:
    config = load_config()
    eff_since = since or config.since
    eff_cap = cap if cap is not None else config.cap
    selected = select_paths(
        paths=paths,
        ctx=ctx,
        since=eff_since,
        cap=eff_cap,
        ctx_allowlist=config.ctx_allowlist,
        skip_predicate=_should_skip_for_cap,
    )
    rows: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    for rel in selected:
        abs_path = FILES_ROOT / rel
        if not abs_path.is_file():
            continue
        from ._extract_document_helpers import compute_source_sha256

        try:
            sha, _ = compute_source_sha256(abs_path)
        except OSError:
            continue
        rec = load_state(sha)
        if rec and done_path_is_duplicate(rec, rel, sha):
            duplicates.append(
                {
                    "path": rel,
                    "duplicate_of": str(rec.get("entity_id") or rec.get("source_path")),
                },
            )
            continue
        stage = "extract"
        if rec:
            stage = str(rec.get("stage") or "extract")
        else:
            _method, err = route_pdf(rel)
            if err:
                stage = "terminal"
        rows.append({"path": rel, "stage": stage})
    return {
        "selected": rows,
        "duplicates": duplicates,
        "as_of": _newest_as_of(),
        "reconcile_run": False,
        "source": "notes/system/ingest-state",
        "freshness": _newest_as_of(),
    }


def ingest_dropbox_status() -> dict[str, Any]:
    terminal: list[dict[str, Any]] = []
    duplicates: list[dict[str, str]] = []
    for rec in iter_state_records():
        if rec.get("terminal") == "needs_human":
            kinds: list[str] = []
            cc = rec.get("crosscheck")
            if isinstance(cc, dict):
                for item in cc.get("mismatches") or []:
                    if isinstance(item, dict) and item.get("kind"):
                        kinds.append(str(item["kind"]))
            terminal.append(
                {
                    "path": rec.get("source_path"),
                    "last_error": rec.get("last_error"),
                    "mismatch_kinds": kinds,
                },
            )
        for dup in rec.get("duplicate_paths") or []:
            duplicates.append(
                {
                    "path": dup,
                    "duplicate_of": str(rec.get("entity_id") or rec.get("source_path")),
                },
            )
    return {
        "terminal": terminal,
        "duplicates": duplicates,
        "as_of": _newest_as_of(),
        "reconcile_run": False,
        "source": "notes/system/ingest-state",
        "freshness": _newest_as_of(),
    }


def register_ingest_dropbox_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Ingest Dropbox")
    def ingest_dropbox(
        op: str,
        paths: list[str] | None = None,
        ctx: str | None = None,
        since: str | None = None,
        cap: int | None = None,
    ) -> dict[str, Any]:
        """Run bounded dropbox PDF ingest (extract, cross-check, promote)."""
        if op == "run":
            return ingest_dropbox_run(paths=paths, ctx=ctx, since=since, cap=cap)
        if op == "dry_run":
            return ingest_dropbox_dry_run(
                paths=paths,
                ctx=ctx,
                since=since,
                cap=cap,
            )
        if op == "status":
            return ingest_dropbox_status()
        raise ValueError(f"Unknown ingest_dropbox op {op!r}")
