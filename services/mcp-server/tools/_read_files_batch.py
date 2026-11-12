"""Multi-path read_files_batch.

Extracted from ``_file_helpers.py`` so that module stays within the SLOC ceiling.
``read_files_batch`` is re-exported from ``_file_helpers``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._file_helpers import (
    FILES_ROOT,
    _is_sparse_path,
    _load_plain_text_for_sparse,
    _nondocument_export_item,
    _path_match_keys,
    _read_files_batch_value,
    _stopped_export_item,
    _stopped_multi_section_item,
    resolve_files_path,
)
from ._file_read_result import read_file_result
from ._hashing import sha256_hex_equal, sha256_hex_of_file
from ._sparse_document_read import (
    BATCH_MAX_CONTENT_CHARS,
    BATCH_MAX_SECTIONS,
    param_not_applicable_error,
    resolve_document_read,
    sparse_selector_conflict_error,
)
from ._sparse_payload_envelope import finalize_sparse_document_payload


def read_files_batch(
    paths: list[str],
    root: Path = FILES_ROOT,
    *,
    binary: bool = False,
    section: str | None = None,
    sections: list[str] | None = None,
    export: bool = False,
    chunk_cursor: int | None = None,
    chunk_cursor_provided: bool = False,
    expected_read_sha256: str | dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read multiple sandboxed files, preserving per-path errors inline.

    Multi-path ``export`` budgets document paths only (16 items, 65536 code
    points). A text non-document keeps its full body. A binary non-document
    keeps the no-selector envelope and is not coerced to empty text. Neither
    consumes that budget or receives an export fetch. ``omitted`` lists stopped
    document paths; ``budget_cursor`` is the flattened index of the first.
    """
    if chunk_cursor_provided:
        return param_not_applicable_error(parameter="chunk_cursor")

    exp_map: dict[str, str] | None = None
    exp_scalar: str | None = None
    if isinstance(expected_read_sha256, dict):
        exp_map = dict(expected_read_sha256)
    elif expected_read_sha256:
        raw_exp = str(expected_read_sha256).strip()
        if raw_exp.startswith("{"):
            try:
                parsed = json.loads(raw_exp)
                if isinstance(parsed, dict):
                    exp_map = {str(k): str(v) for k, v in parsed.items()}
                else:
                    exp_scalar = raw_exp
            except json.JSONDecodeError:
                exp_scalar = raw_exp
        else:
            exp_scalar = raw_exp

    sparse_paths = [p for p in paths if _is_sparse_path(p, root)]
    if exp_scalar is not None and len(sparse_paths) > 1:
        return sparse_selector_conflict_error(
            "expected_read_sha256 scalar applies only when read_multi names one document path"
        )

    if exp_map is not None:
        requested: dict[str, str] = {}
        for path_item in paths:
            for alias in _path_match_keys(path_item):
                requested.setdefault(alias, path_item)
        bad_keys: list[str] = []
        resolved_map: dict[str, str] = {}
        for key, value in exp_map.items():
            target = None
            for alias in _path_match_keys(key):
                target = requested.get(alias)
                if target is not None:
                    break
            if target is None or not _is_sparse_path(target, root):
                bad_keys.append(str(key))
                continue
            resolved_map[target] = str(value)
        if bad_keys:
            return param_not_applicable_error(
                parameter="expected_read_sha256",
                keys=bad_keys,
            )
        exp_map = resolved_map

    def _per_path_expected(p: str) -> str | None:
        if exp_map is not None:
            return exp_map.get(p)
        if exp_scalar is not None and _is_sparse_path(p, root):
            return exp_scalar
        return None

    has_section = bool(section and str(section).strip())
    multi_path = len(paths) > 1
    multi_expanded = multi_path and (bool(sections) or export or has_section)

    if multi_expanded and sections:
        flat: list[dict[str, Any]] = []
        omitted: list[str] = []
        budget_cursor: int | None = None
        used = 0
        flat_idx = 0
        ended = False
        for rel in paths:
            for sid in list(sections or []):
                if ended or flat_idx >= BATCH_MAX_SECTIONS:
                    if budget_cursor is None:
                        budget_cursor = flat_idx
                    ended = True
                    flat.append(_stopped_multi_section_item(rel, sid, root=root))
                    omitted.append(f"{rel}:{sid}")
                    flat_idx += 1
                    continue
                per_exp = _per_path_expected(rel)
                one = read_file_result(
                    rel,
                    root=root,
                    binary=False,
                    sections=[sid],
                    expected_read_sha256=per_exp,
                )
                if (
                    one.get("outcome") == "stale_handle"
                    or one.get("code") == "stale_handle"
                ):
                    flat.append(
                        {
                            "path": rel,
                            "id": sid,
                            "outcome": "stale_handle",
                            "read_sha256": one.get("read_sha256")
                            or (one.get("data") or {}).get("read_sha256"),
                        }
                    )
                    flat_idx += 1
                    continue
                it = (one.get("items") or [None])[0]
                if it is None:
                    flat.append(
                        {
                            "path": rel,
                            "id": sid,
                            "outcome": "error",
                            "error": "no batch item",
                        }
                    )
                    flat_idx += 1
                    continue
                oc = it.get("outcome")
                if oc == "stale_handle" or one.get("outcome") == "stale_handle":
                    flat.append(
                        {
                            "path": rel,
                            "id": sid,
                            "outcome": "stale_handle",
                            "read_sha256": one.get("read_sha256"),
                        }
                    )
                    flat_idx += 1
                    continue
                if oc != "ok":
                    flat.append({"path": rel, **it})
                    if oc == "over_budget":
                        ended = True
                        budget_cursor = flat_idx
                        omitted.append(f"{rel}:{sid}")
                    flat_idx += 1
                    continue
                body = str(it.get("content", ""))
                next_used = used + len(body)
                if next_used > BATCH_MAX_CONTENT_CHARS:
                    ended = True
                    budget_cursor = flat_idx
                    flat.append({"path": rel, "id": sid, "outcome": "over_budget"})
                    omitted.append(f"{rel}:{sid}")
                    flat_idx += 1
                    continue
                used = next_used
                flat.append({"path": rel, **it})
                flat_idx += 1
        out: dict[str, Any] = {
            "selection": "multi",
            "read_multi": True,
            "items": flat,
            "sparse_section_batch": True,
        }
        if omitted:
            out["omitted"] = omitted
        if budget_cursor is not None:
            out["budget_cursor"] = budget_cursor
        return finalize_sparse_document_payload(out)

    if multi_expanded and export:
        items: list[dict[str, Any]] = []
        omitted: list[str] = []
        budget_cursor: int | None = None
        remaining = BATCH_MAX_CONTENT_CHARS
        terminated = False
        doc_count = 0
        flat_idx = 0
        for rel in paths:
            try:
                if not resolve_files_path(rel, root=root).is_file():
                    read_file_result(rel, root=root, binary=False)
            except Exception as exc:
                items.append({"path": rel, "error": str(exc)})
                flat_idx += 1
                continue
            if not _is_sparse_path(rel, root):
                items.append(_nondocument_export_item(rel, root=root))
                flat_idx += 1
                continue
            if terminated or doc_count >= BATCH_MAX_SECTIONS:
                if budget_cursor is None:
                    budget_cursor = flat_idx
                items.append(_stopped_export_item(rel, root=root))
                omitted.append(rel)
                terminated = True
                flat_idx += 1
                continue
            per_exp = _per_path_expected(rel)
            text = _load_plain_text_for_sparse(rel, root)
            body_len = len(text)
            if body_len > remaining:
                if budget_cursor is None:
                    budget_cursor = flat_idx
                items.append(_stopped_export_item(rel, root=root))
                omitted.append(rel)
                terminated = True
                flat_idx += 1
                continue
            src = resolve_files_path(rel, root=root)
            rs = sha256_hex_of_file(src)
            if per_exp and not sha256_hex_equal(rs, per_exp):
                items.append(
                    {
                        "path": rel,
                        "outcome": "stale_handle",
                        "read_sha256": rs,
                    }
                )
                doc_count += 1
                flat_idx += 1
                continue
            chunk = finalize_sparse_document_payload(
                resolve_document_read(
                    path=rel,
                    sandbox="cortex",
                    text=text,
                    read_sha256=rs,
                    export=True,
                    expected_read_sha256=per_exp,
                )
            )
            chunk["path"] = rel
            chunk["outcome"] = "ok"
            items.append(chunk)
            remaining -= len(text)
            doc_count += 1
            flat_idx += 1
        out: dict[str, Any] = {
            "selection": "multi",
            "read_multi": True,
            "items": items,
        }
        if omitted:
            out["omitted"] = omitted
        if budget_cursor is not None:
            out["budget_cursor"] = budget_cursor
        return finalize_sparse_document_payload(out)

    if multi_expanded and has_section:
        return sparse_selector_conflict_error(
            "read_multi does not support a single section= selector across multiple paths"
        )

    results: dict[str, Any] = {}
    for path in paths:
        try:
            per_exp = _per_path_expected(path)
            result = read_file_result(
                path,
                root=root,
                binary=binary,
                section=section,
                sections=sections,
                export=export,
                chunk_cursor=chunk_cursor,
                chunk_cursor_provided=chunk_cursor_provided,
                expected_read_sha256=per_exp,
            )
            if multi_path:
                results[path] = result
            elif binary or result.get("auto_binary"):
                results[path] = result
            else:
                results[path] = _read_files_batch_value(result)
        except Exception as exc:
            results[path] = {"error": str(exc)}
    if not multi_path:
        return {"files": results}
    return finalize_sparse_document_payload(
        {"selection": "multi", "read_multi": True, "files": results}
    )
