"""Stage CDP generate inputs to cortex:// for the web-anthropic satellite.

Life/web cannot read ``workspaces://`` (friction a23964). The model-endpoint
adapter stages checkout-relative and workspaces:// refs under
``cortex://notes/system/ephemeral/cdp-endpoint/<execution_id>/`` before submit.
Pre-staged ``cortex://`` URIs pass through. Unstageable inputs fail closed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root, workspaces_root

_EPHEMERAL_PREFIX = "notes/system/ephemeral/cdp-endpoint"


class CdpStagingError(ValueError):
    """Unstageable CDP prompt input (maps to HTTP 422)."""

    def __init__(self, reason: str, *, code: str = "cdp_prompt_unstageable") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True, slots=True)
class StagedPrompt:
    """Cortex URI ready for satellite submit plus optional ephemeral root."""

    prompt_uri: str
    ephemeral_root: Path | None
    staged: bool


def _cortex_uri(rel: str) -> str:
    return f"cortex://{rel.lstrip('/')}"


def ephemeral_dir(execution_id: str) -> Path:
    """On-disk root for one CDP dispatch's staged inputs."""
    return cortex_files_root() / _EPHEMERAL_PREFIX / execution_id


def ephemeral_uri_prefix(execution_id: str) -> str:
    """Return cortex:// URI prefix for one execution's ephemeral staging tree."""
    return _cortex_uri(f"{_EPHEMERAL_PREFIX}/{execution_id}")


def resolve_workspaces_path(uri_or_path: str) -> Path | None:
    """Map workspaces:// or checkout-relative path to an on-disk file."""
    raw = uri_or_path.strip()
    if not raw:
        return None
    if raw.startswith("cortex://"):
        return None
    if raw.startswith("workspaces://"):
        rest = raw[len("workspaces://") :]
        parts = rest.split("/", 1)
        if len(parts) != 2 or not parts[1]:
            return None
        repo, rel = parts[0], parts[1]
        root = workspaces_root()
        # workspaces_root may already be the ULG checkout or its parent.
        candidates = [
            root / repo / rel,
            root / rel,
            root.parent / repo / rel,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        return None
    path = Path(raw)
    if path.is_file():
        return path.resolve()
    # Checkout-relative under ULG.
    candidate = workspaces_root() / raw
    if candidate.is_file():
        return candidate.resolve()
    return None


def stage_prompt_uri(
    *,
    execution_id: str,
    prompt_uri: str | None = None,
    prompt_text: str | None = None,
    packet_path: str | None = None,
    sidecar_ref: str | None = None,
) -> StagedPrompt:
    """Return a cortex:// prompt_uri for satellite submit.

    Priority: inline ``prompt_text`` > ``prompt_uri`` > ``sidecar_ref`` >
    ``packet_path``. ``cortex://`` inputs pass through without copy.
    """
    if prompt_text is not None and prompt_text.strip():
        dest_dir = ephemeral_dir(execution_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "prompt.md"
        dest.write_text(prompt_text, encoding="utf-8")
        rel = f"{_EPHEMERAL_PREFIX}/{execution_id}/prompt.md"
        return StagedPrompt(
            prompt_uri=_cortex_uri(rel),
            ephemeral_root=dest_dir,
            staged=True,
        )

    for candidate in (prompt_uri, sidecar_ref, packet_path):
        if not candidate or not str(candidate).strip():
            continue
        raw = str(candidate).strip()
        if raw.startswith("cortex://"):
            return StagedPrompt(
                prompt_uri=raw,
                ephemeral_root=None,
                staged=False,
            )
        source = resolve_workspaces_path(raw)
        if source is None:
            raise CdpStagingError(
                f"unstageable CDP prompt input: {raw!r} "
                "(not cortex:// and not a readable workspaces/checkout path)",
            )
        dest_dir = ephemeral_dir(execution_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        shutil.copy2(source, dest)
        rel = f"{_EPHEMERAL_PREFIX}/{execution_id}/{source.name}"
        return StagedPrompt(
            prompt_uri=_cortex_uri(rel),
            ephemeral_root=dest_dir,
            staged=True,
        )

    raise CdpStagingError(
        "CDP generate requires prompt_text, prompt_uri, sidecar_ref, or packet_path",
        code="cdp_prompt_missing",
    )


def sweep_ephemeral(execution_id: str) -> bool:
    """Best-effort delete of staged tree after terminal status."""
    root = ephemeral_dir(execution_id)
    if not root.is_dir():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return not root.exists()


def gc_orphaned_ephemeral(*, max_age_s: int = 24 * 3600) -> int:
    """Delete ephemeral trees older than ``max_age_s``. Returns count removed."""
    import time

    base = cortex_files_root() / _EPHEMERAL_PREFIX
    if not base.is_dir():
        return 0
    now = time.time()
    removed = 0
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            age = now - child.stat().st_mtime
        except OSError:
            continue
        if age >= max_age_s:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
