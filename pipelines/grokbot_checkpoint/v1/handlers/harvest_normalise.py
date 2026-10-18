"""Normalize staged L0 pages and write ``*.normalized.pages.txt`` (if_absent)."""

from __future__ import annotations

from typing import Any, override

from continuity_tape.normalize_grokbot_l0_pages import (
    L0NormalizeError,
    normalize_grokbot_l0_pages,
    normalized_capture_ref,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import FsClientMissingError, file_sha256, fs_of, refuse, rel_path_ok


async def run_harvest_normalise(options: dict[str, Any], fs: Any) -> dict[str, Any]:
    l0_ref = str(options.get("l0_ref") or "").strip()
    if not l0_ref:
        return refuse("source.no_pour", "harvest_seal requires l0_ref")
    if not rel_path_ok(l0_ref.removeprefix("cortex://").removeprefix("workspaces://")):
        return refuse("admit.path_escape", f"refused path {l0_ref}")
    try:
        raw = await fs.read(l0_ref)
    except FsClientMissingError as exc:
        return refuse(exc.code, str(exc))
    if raw is None:
        return refuse("source.missing", f"L0 capture {l0_ref} absent")
    expected = str(options.get("l0_sha256") or "").strip()
    digest = file_sha256(raw)
    if expected and digest != expected:
        return refuse("source.sha_mismatch", f"{digest} != {expected}")
    try:
        normalized, stats = normalize_grokbot_l0_pages(raw.decode("utf-8"))
    except L0NormalizeError as exc:
        return refuse("pour.schema", str(exc))
    norm_ref = normalized_capture_ref(l0_ref)
    norm_bytes = normalized.encode("utf-8")
    norm_sha = file_sha256(norm_bytes)
    written = await fs.write(norm_ref, norm_bytes, if_absent=True)
    if not written.get("ok"):
        existing = await fs.read(norm_ref)
        if existing is None:
            return refuse("envelope.conflict", "normalized capture if_absent lost the race")
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
    }


class GrokbotCheckpointHarvestNormaliseHandler(BaseHandler):
    step_type = "grokbot_checkpoint_harvest_normalise_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        payload = await run_harvest_normalise(options, fs_of(context))
        return StepOutput(raw="", json=payload)
