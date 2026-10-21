"""Normalize staged L0 pages and write ``*.normalized.pages.txt`` (if_absent)."""

from __future__ import annotations

import json
from typing import Any, override

from continuity_tape.extract_grokbot_jsonl import (
    TranscriptPageError,
    reassemble_pages,
)
from continuity_tape.normalize_grokbot_l0_pages import (
    L0NormalizeError,
    normalize_grokbot_l0_pages,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import FsClientMissingError, file_sha256, fs_of, refuse, rel_path_ok
from ._harvest import checkpoint_raw_uri
from .harvest_pages import (
    _PAGE_NAME,
    _mapped,
    _parse_page,
    check_pages,
    load_prior_lines,
    normalize_input,
    prior_window,
    raw_bytes_for,
)


def _bare(ref: str) -> str:
    return ref.removeprefix("cortex://").removeprefix("workspaces://")


async def _folder_bytes(
    options: dict[str, Any], fs: Any, l0_ref: str
) -> tuple[bytes, str] | dict[str, Any]:
    """Return (raw_bytes, normalize_text) or a refusal dict."""
    bare = _bare(l0_ref).rstrip("/")
    try:
        names = await fs.list(bare)
    except FsClientMissingError as exc:
        return refuse(exc.code, str(exc))
    if names is None:
        return refuse("source.missing", f"L0 capture {l0_ref} absent")
    page_names = []
    for name in names:
        base = name.rstrip("/").rsplit("/", 1)[-1]
        if _PAGE_NAME.match(base):
            page_names.append(name if name.startswith(bare) else f"{bare}/{base}")
    if not page_names:
        return refuse("harvest.pages_missing", f"no page files under {l0_ref}")
    parsed: list[dict[str, Any]] = []
    blobs: list[bytes] = []
    for name in page_names:
        blob = await fs.read(name)
        if blob is None:
            return refuse("source.missing", f"page {name} absent")
        try:
            page = _parse_page(blob.decode("utf-8"))
        except TranscriptPageError as exc:
            return _mapped("harvest.page_header", exc)
        except UnicodeDecodeError as exc:
            return refuse("pour.schema", str(exc))
        base = name.rsplit("/", 1)[-1]
        match = _PAGE_NAME.match(base)
        assert match is not None
        if int(match.group(1)) != page["lo"] or int(match.group(2)) != page["hi"]:
            return refuse(
                "harvest.page_name",
                f"{base} does not match header {page['lo']}-{page['hi']}",
            )
        parsed.append(page)
        blobs.append(blob)
    agent_id = str(options.get("agent_id") or "").strip()
    n = int(options.get("n") or 1)
    prior = b""
    prior_norm: bytes | None = None
    prior_lines: list[str] | None = None
    prior_total: int | None = None
    if n > 1:
        raw_uri = checkpoint_raw_uri(agent_id, n - 1)
        msg_uri = raw_uri.replace(".raw.json", ".messages.json")
        prior = await fs.read(raw_uri) or b""
        messages = await fs.read(msg_uri)
        if not prior or messages is None:
            return refuse("harvest.no_prior", f"missing {raw_uri} or {msg_uri}")
        norm_uri = (
            f"notes/system/checkpoints/grok/{agent_id}/{n - 1}.normalized.pages.txt"
        )
        prior_norm = await fs.read(norm_uri)
        try:
            payload = json.loads(messages.decode("utf-8"))
            window = prior_window(messages)
            cap = (payload.get("meta") or {}).get("capture") or {}
            cap_sha = cap.get("sha256") if isinstance(cap, dict) else None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return refuse("harvest.no_prior", str(exc))
        if prior_norm is None or not cap_sha:
            return refuse("harvest.no_prior", f"missing {norm_uri} or capture.sha256")
        if file_sha256(prior_norm) != cap_sha:
            return refuse(
                "harvest.no_prior",
                "prior normalized sha != capture.sha256",
            )
        try:
            prior_lines = load_prior_lines(prior_norm)
        except (TranscriptPageError, UnicodeDecodeError) as exc:
            return refuse("harvest.no_prior", str(exc))
        if window is None:
            return refuse("harvest.no_prior", "prior messages have no window")
        _next, prior_total = window
        if len(prior_lines) != _next:
            return refuse(
                "harvest.no_prior",
                f"prior normalized lines {len(prior_lines)} != window.hi {_next}",
            )
        try:
            aligned, _stats = normalize_grokbot_l0_pages(
                prior.decode("utf-8"), anchor_total=prior_total
            )
            round_trip, _info = reassemble_pages(aligned)
        except (L0NormalizeError, TranscriptPageError, UnicodeDecodeError) as exc:
            return refuse("harvest.no_prior", str(exc))
        if round_trip != prior_lines:
            return refuse(
                "harvest.no_prior",
                "prior raw does not match the sealed normalized lines",
            )
    refusal = check_pages(parsed, prior_lines=prior_lines, prior_total=prior_total)
    if refusal:
        return refusal
    paired = list(zip(parsed, blobs, strict=True))
    raw_out = raw_bytes_for(prior, paired)
    kept = [
        (page, blob.decode("utf-8")) for page, blob in paired if page.get("keep", True)
    ]
    prior_text = prior_norm.decode("utf-8") if n > 1 and prior_norm else ""
    norm_text = normalize_input(prior_text, kept)
    target = next(
        (page["target_total"] for page in parsed if "target_total" in page), None
    )
    return raw_out, norm_text, target


async def load_raw_for_pour(options: dict[str, Any], fs: Any) -> bytes | dict[str, Any]:
    """Re-read the verbatim L0. A directory is stitched; a missing file refuses."""
    l0_ref = str(options.get("l0_ref") or "").strip()
    if not l0_ref:
        return refuse("source.missing", "L0 capture absent")
    try:
        listing = await fs.list(_bare(l0_ref).rstrip("/"))
    except FsClientMissingError as exc:
        return refuse(exc.code, str(exc))
    except AttributeError:
        listing = None
    if listing is not None:
        loaded = await _folder_bytes(options, fs, l0_ref)
        if isinstance(loaded, dict):
            return loaded
        return loaded[0]
    try:
        raw = await fs.read(l0_ref)
    except FsClientMissingError as exc:
        return refuse(exc.code, str(exc))
    if raw is None:
        return refuse("source.missing", f"L0 capture {l0_ref} absent")
    return raw


async def run_harvest_normalise(options: dict[str, Any], fs: Any) -> dict[str, Any]:
    l0_ref = str(options.get("l0_ref") or "").strip()
    if l0_ref.startswith("workspaces://"):
        return refuse(
            "harvest.workspace_refused",
            "l0_ref must be a cortex path; workspaces:// is a different tree",
        )
    if not l0_ref:
        return refuse("source.no_pour", "harvest_seal requires l0_ref")
    if not rel_path_ok(_bare(l0_ref)):
        return refuse("admit.path_escape", f"refused path {l0_ref}")
    folder = False
    try:
        listing = await fs.list(_bare(l0_ref).rstrip("/"))
    except FsClientMissingError as exc:
        return refuse(exc.code, str(exc))
    except AttributeError:
        listing = None
    if listing is not None:
        folder = True
        loaded = await _folder_bytes(options, fs, l0_ref)
        if isinstance(loaded, dict):
            return loaded
        raw, text_for_norm, target_total = loaded
    else:
        try:
            raw = await fs.read(l0_ref)
        except FsClientMissingError as exc:
            return refuse(exc.code, str(exc))
        if raw is None:
            return refuse("source.missing", f"L0 capture {l0_ref} absent")
        try:
            text_for_norm = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return refuse("pour.schema", str(exc))
        target_total = None
    tape = "tape/" in l0_ref.replace("\\", "/")
    electron = str(options.get("fallback") or "") == "electron"
    if tape and not electron:
        return refuse(
            "harvest.tape_refused",
            "tape capture is not a checkpoint input; pass fallback=electron to scrape",
        )
    expected = str(options.get("l0_sha256") or "").strip()
    digest = file_sha256(raw)
    if expected and digest != expected:
        return refuse("source.sha_mismatch", f"{digest} != {expected}")
    try:
        normalized, stats = normalize_grokbot_l0_pages(
            text_for_norm, anchor_total=target_total
        )
    except L0NormalizeError as exc:
        return refuse("pour.schema", str(exc))
    agent_id = str(options.get("agent_id") or "unknown")
    n = int(options.get("n") or 1)
    norm_ref = f"notes/system/checkpoints/grok/{agent_id}/{n}.normalized.pages.txt"
    if "tape/" in norm_ref:
        return refuse("harvest.tape_refused", norm_ref)
    norm_bytes = normalized.encode("utf-8")
    norm_sha = file_sha256(norm_bytes)
    written = await fs.write(norm_ref, norm_bytes, if_absent=True)
    if not written.get("ok"):
        existing = await fs.read(norm_ref)
        if existing is None:
            return refuse(
                "envelope.conflict", "normalized capture if_absent lost the race"
            )
        if file_sha256(existing) != norm_sha:
            return refuse(
                "source.sha_mismatch",
                "existing normalized.pages.txt bytes differ",
            )
        norm_sha = file_sha256(existing)
    else:
        read_back = await fs.read(norm_ref)
        if read_back is None or file_sha256(read_back) != written["sha256"]:
            return refuse("source.sha_mismatch", "normalized read-back sha mismatch")
    return {
        "refused": None,
        "l0_ref": l0_ref,
        "l0_sha256": digest,
        "l0_bytes": len(raw),
        "normalized_ref": norm_ref,
        "normalized_sha256": norm_sha,
        "pages": stats.pages,
        "wrapper_lines_stripped": stats.wrapper_lines_stripped,
        "header_total_aligned": stats.header_total_aligned,
        "role_lines": stats.role_lines,
        "raw_ref": checkpoint_raw_uri(agent_id, n),
        "folder": folder,
    }


class GrokbotCheckpointHarvestNormaliseHandler(BaseHandler):
    step_type = "grokbot_checkpoint_harvest_normalise_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        from .admit import bind_admitted_n

        options = bind_admitted_n(
            dict(getattr(context, "options", {}) or {}),
            getattr(context, "outputs", {}) or {},
        )
        payload = await run_harvest_normalise(options, fs_of(context))
        return StepOutput(raw="", json=payload)
