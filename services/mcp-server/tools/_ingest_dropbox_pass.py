from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp_events import record

from ._extract_document_helpers import compute_source_sha256
from ._file_helpers import FILES_ROOT, resolve_files_path
from ._ingest_dropbox_select import model_family, parse_dropbox_rel, route_pdf
from ._ingest_dropbox_state import (
    canonical_sidecar_path,
    catch_all_failure,
    entity_id_from_path,
    handle_duplicate_path,
    is_canonical_sidecar,
    is_duplicate_visit,
    load_state,
    new_record,
    read_sidecar,
    save_state,
    try_ingest_lock,
)
from ._ingest_dropbox_tokens import compare_bodies
from ._promote_document_helpers import PromoteError
from ._sidecar_schema import SIDECAR_SUFFIX
from .extract_document import OcrProviderError, OcrUnreachable, run_extract_document
from .promote_document_to_evidence import run_promote_document

_OCR_POISON = "[OCR error: "
_MAX_ERR = 200


def process_file(
    config: dict[str, Any],
    rel: str,
    *,
    cap_counter: Callable[[], bool],
) -> tuple[str, bool]:
    abs_path = resolve_files_path(rel, root=FILES_ROOT)
    try:
        source_sha256, _size = compute_source_sha256(abs_path)
    except OSError as exc:
        record("mcp.ingest.file.skipped", path=rel, error_class=exc.__class__.__name__)
        return "skipped", False

    with try_ingest_lock(source_sha256) as locked:
        if not locked:
            return "locked", False
        rec = load_state(source_sha256)
        if rec is None:
            rec = new_record(source_path=rel, source_sha256=source_sha256)
        elif rec.get("source_path") != rel:
            rec = handle_duplicate_path(rec, rel)

        if is_duplicate_visit(rec, rel, source_sha256):
            dup = rec.get("entity_id") or rec.get("source_path")
            return f"duplicate_of:{dup}", False

        if rec.get("terminal"):
            return "skipped", False

        if not cap_counter():
            return "skipped", False

        try:
            method, route_err = route_pdf(rel)
        except Exception as exc:  # noqa: BLE001
            if rec.get("source_sha256") == source_sha256:
                catch_all_failure(rec, source_sha256, "extract", exc, max_err=_MAX_ERR)
            return "attempt", False
        if route_err:
            rec["terminal"] = "needs_human"
            rec["last_error"] = route_err
            save_state(source_sha256, rec)
            return "needs_human", False
        if rec.get("method") is None:
            rec["method"] = method

        try:
            return _chain_stages(config, rel, rec, source_sha256, method)
        except OcrUnreachable:
            record("mcp.ingest.run.stopped", path=rel)
            return "attempt", True


def _chain_stages(
    config: dict[str, Any],
    rel: str,
    rec: dict[str, Any],
    source_sha256: str,
    method: str,
) -> tuple[str, bool]:
    stage = rec.get("stage", "extract")
    while stage in ("extract", "crosscheck", "promote"):
        prev = stage
        if stage == "extract":
            nxt = _stage_extract(config, rel, rec, source_sha256, method)
        elif stage == "crosscheck":
            if method != "vision_crosscheck":
                rec["terminal"] = "needs_human"
                rec["last_error"] = "route_mismatch"
                save_state(source_sha256, rec)
                return "needs_human", False
            nxt = _stage_crosscheck(config, rel, rec, source_sha256)
        else:
            nxt = _stage_promote(config, rel, rec, source_sha256, method)
        if rec.get("terminal"):
            return "needs_human", False
        if nxt == "done":
            record(
                "mcp.ingest.state.advanced",
                path=rel,
                from_stage=prev,
                to_stage="done",
                terminal=None,
                last_error_kind=None,
            )
            save_state(source_sha256, rec)
            return "done", False
        if nxt and nxt != prev:
            record(
                "mcp.ingest.state.advanced",
                path=rel,
                from_stage=prev,
                to_stage=nxt,
                terminal=rec.get("terminal"),
                last_error_kind=(rec.get("last_error") or "")[:40] or None,
            )
        if not nxt or nxt == prev:
            break
        stage = nxt
    save_state(source_sha256, rec)
    return "attempt", False


def _stage_extract(
    config: dict[str, Any],
    rel: str,
    rec: dict[str, Any],
    sha: str,
    method: str,
) -> str | None:
    canon = canonical_sidecar_path(rel)
    if is_canonical_sidecar(canon, sha):
        body = read_sidecar(canon)[1]
        if method == "vision_crosscheck" and _OCR_POISON in body:
            canon.unlink(missing_ok=True)
        else:
            rec["extract_attempts"] = 0
            rec["last_error"] = None
            rec["stage"] = "crosscheck" if method == "vision_crosscheck" else "promote"
            save_state(sha, rec)
            return rec["stage"]
    try:
        result = run_extract_document(rel)
    except OcrUnreachable:
        raise
    except OcrProviderError as exc:
        rec["extract_attempts"] = int(rec.get("extract_attempts", 0)) + 1
        rec["last_error"] = str(exc)[:_MAX_ERR]
        if rec["extract_attempts"] >= 3:
            rec["terminal"] = "needs_human"
        save_state(sha, rec)
        return "extract"
    except Exception as exc:  # noqa: BLE001
        return catch_all_failure(rec, sha, "extract", exc, max_err=_MAX_ERR)

    if not result.get("sidecar_path"):
        rec["extract_attempts"] = int(rec.get("extract_attempts", 0)) + 1
        rec["last_error"] = "empty_extraction"
        if rec["extract_attempts"] >= 3:
            rec["terminal"] = "needs_human"
        save_state(sha, rec)
        return "extract"

    if not rec.pop("_crosscheck_reset", False):
        rec["extract_attempts"] = 0
    rec["last_error"] = None
    rec["stage"] = "crosscheck" if method == "vision_crosscheck" else "promote"
    save_state(sha, rec)
    return rec["stage"]


def _stage_crosscheck(
    config: dict[str, Any],
    rel: str,
    rec: dict[str, Any],
    sha: str,
) -> str | None:
    canon = canonical_sidecar_path(rel)
    if not canon.is_file():
        rec["stage"] = "extract"
        rec["_crosscheck_reset"] = True
        save_state(sha, rec)
        return "extract"
    fm, body = read_sidecar(canon)
    fam_map = config["family_map"]
    canon_model = str(fm.get("model", ""))
    canon_fam = model_family(canon_model, fam_map)
    check_model = config["check"]
    check_fam = model_family(check_model, fam_map)
    if canon_fam is None or check_fam is None:
        rec["terminal"] = "needs_human"
        rec["last_error"] = "unknown_family"
        save_state(sha, rec)
        return None
    if canon_fam == check_fam:
        rec["terminal"] = "needs_human"
        rec["last_error"] = "same_family"
        save_state(sha, rec)
        return None

    pdf = resolve_files_path(rel, root=FILES_ROOT)
    for variant in pdf.parent.glob(f"{pdf.name}.args-*{SIDECAR_SUFFIX}"):
        if ".pages-" not in variant.name:
            variant.unlink(missing_ok=True)

    try:
        run_extract_document(rel, model=check_model)
    except OcrUnreachable:
        raise
    except OcrProviderError as exc:
        rec["check_attempts"] = int(rec.get("check_attempts", 0)) + 1
        rec["last_error"] = str(exc)[:_MAX_ERR]
        if rec["check_attempts"] >= 3:
            rec["terminal"] = "needs_human"
        save_state(sha, rec)
        return "crosscheck"
    except Exception as exc:  # noqa: BLE001
        return catch_all_failure(rec, sha, "crosscheck", exc, max_err=_MAX_ERR)

    check_path = sorted(pdf.parent.glob(f"{pdf.name}.args-*{SIDECAR_SUFFIX}"))[-1]
    _fm2, check_body = read_sidecar(check_path)
    agree, compared, mismatches = compare_bodies(body, check_body)
    if compared == 0:
        rec["terminal"] = "needs_human"
        rec["last_error"] = "no_tokens"
        save_state(sha, rec)
        return None
    cc = {
        "canonical_model": canon_model,
        "check_model": check_model,
        "check_sidecar_path": check_path.relative_to(FILES_ROOT).as_posix(),
        "tokens_compared": compared,
        "mismatches": mismatches if not agree else [],
    }
    if not agree:
        rec["terminal"] = "needs_human"
        rec["last_error"] = "crosscheck_disagree"
        rec["crosscheck"] = cc
        save_state(sha, rec)
        return None
    rec["stage"] = "promote"
    rec["crosscheck"] = cc
    save_state(sha, rec)
    return "promote"


def _stage_promote(
    config: dict[str, Any],
    rel: str,
    rec: dict[str, Any],
    sha: str,
    method: str,
) -> str | None:
    entity_id = rec.get("entity_id") or entity_id_from_path(rel)
    attrs: dict[str, Any]
    if method == "text_layer":
        attrs = {"ingest_method": "text_layer", "crosscheck": "not_run"}
    else:
        cc = rec.get("crosscheck") or {}
        attrs = {
            "ingest_method": "vision_crosscheck",
            "canonical_model": cc.get("canonical_model"),
            "check_model": cc.get("check_model"),
            "tokens_compared": cc.get("tokens_compared"),
            "crosscheck": "agree",
        }
    try:
        parsed = parse_dropbox_rel(rel)
        ctx = parsed[0] if parsed else "unknown"
        desc = f"Ingested {ctx} document {Path(rel).stem} ({method})"
        result = run_promote_document(rel, entity_id, desc, entity_attributes=attrs)
    except PromoteError as exc:
        rec["terminal"] = "needs_human"
        rec["last_error"] = exc.code
        save_state(sha, rec)
        return None
    except Exception as exc:  # noqa: BLE001
        return catch_all_failure(rec, sha, "promote", exc, max_err=_MAX_ERR)

    rec["stage"] = "done"
    rec["entity_id"] = result.get("entity_id", entity_id)
    rec["last_error"] = None
    save_state(sha, rec)
    return "done"
