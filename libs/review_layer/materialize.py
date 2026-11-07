"""Git read-only export of unreachable review heads into cortex review-layers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root

_REVIEW_LAYERS_PREFIX = Path("tmp/review-layers")
_PARTIAL_SUFFIX = ".partial"
_STALE_PARTIAL_AGE_S = 3600
_DEFAULT_TTL = timedelta(hours=24)


class ReviewTargetUnreachable(Exception):  # noqa: N818 — stable API name in AC
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ReviewLayerManifest:
    base_sha: str
    head_sha: str
    diff_sha256: str
    files: tuple[str, ...]
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "diff_sha256": self.diff_sha256,
            "files": list(self.files),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @property
    def uri(self) -> str:
        return ""  # set by caller on full layer write


def hub_repo_path() -> Path:
    raw = os.environ.get("GIT_INTEGRATION_SOURCE_REPO", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from implement_admission.closeout_helpers import workspaces_root

    return workspaces_root()


def _git_run(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReviewTargetUnreachable(
            (proc.stderr or proc.stdout or "git failed").strip()
        )
    return proc.stdout.strip()


def _resolve_commit(repo: Path, ref: str) -> str:
    try:
        return _git_run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except ReviewTargetUnreachable as exc:
        raise ReviewTargetUnreachable(f"head unresolvable: {ref} ({exc.reason})") from exc


def _default_base(repo: Path, head_sha: str) -> str:
    for base_ref in ("master", "main"):
        try:
            base_tip = _git_run(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        except ReviewTargetUnreachable:
            continue
        try:
            return _git_run(repo, "merge-base", head_sha, base_tip)
        except ReviewTargetUnreachable:
            continue
    raise ReviewTargetUnreachable("no merge-base against master/main")


def _is_ancestor(repo: Path, descendant: str, ancestor: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", descendant, ancestor],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise ReviewTargetUnreachable(
        proc.stderr or proc.stdout or "ancestor check failed"
    )


def gc_expired_layers(root: Path | None = None, *, now: datetime | None = None) -> int:
    """Delete expired review layers and stale partial dirs."""
    base = (root or cortex_files_root() / _REVIEW_LAYERS_PREFIX).resolve()
    if not base.is_dir():
        return 0
    now = now or datetime.now(UTC)
    now_ts = now.timestamp()
    removed = 0
    for child in base.iterdir():
        name = child.name
        if name.endswith(_PARTIAL_SUFFIX):
            partial_root = child
            try:
                age = now_ts - partial_root.stat().st_mtime
            except OSError:
                continue
            if age >= _STALE_PARTIAL_AGE_S:
                shutil.rmtree(partial_root, ignore_errors=True)
                removed += 1
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            expires_raw = str(payload.get("expires_at") or "")
            expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < now:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def materialize_review_layer(
    *,
    layer_id: str,
    head_ref: str,
    base_ref: str | None = None,
    repo: Path | None = None,
    cortex_root: Path | None = None,
    now: datetime | None = None,
) -> ReviewLayerManifest | None:
    """Materialize a review layer when ``head`` is reachable but not on master.

    Returns ``None`` when ``head`` is already an ancestor of master (no layer needed).
    """
    repo = (repo or hub_repo_path()).resolve()
    cortex_root = cortex_root or cortex_files_root()
    layers_root = cortex_root / _REVIEW_LAYERS_PREFIX
    layers_root.mkdir(parents=True, exist_ok=True)

    gc_expired_layers(layers_root, now=now)

    head_sha = _resolve_commit(repo, head_ref)
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ReviewTargetUnreachable(f"invalid head sha: {head_sha}")

    for master_ref in ("master", "main"):
        try:
            master_sha = _git_run(repo, "rev-parse", "--verify", f"{master_ref}^{{commit}}")
        except ReviewTargetUnreachable:
            continue
        if _is_ancestor(repo, head_sha, master_sha):
            return None

    base_sha = (
        _resolve_commit(repo, base_ref) if base_ref else _default_base(repo, head_sha)
    )
    diff_text = _git_run(repo, "diff", f"{base_sha}..{head_sha}")
    diff_bytes = diff_text.encode("utf-8")
    diff_sha256 = hashlib.sha256(diff_bytes).hexdigest()

    name_status = _git_run(repo, "diff", "--name-status", f"{base_sha}..{head_sha}")
    files: list[str] = []
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()
        if status.startswith("D"):
            continue
        files.append(parts[-1].strip())

    now = now or datetime.now(UTC)
    expires = now + _DEFAULT_TTL
    manifest = ReviewLayerManifest(
        base_sha=base_sha,
        head_sha=head_sha,
        diff_sha256=diff_sha256,
        files=tuple(files),
        created_at=now.isoformat(),
        expires_at=expires.isoformat(),
    )

    final_dir = layers_root / layer_id
    partial_dir = layers_root / f"{layer_id}{_PARTIAL_SUFFIX}"
    if partial_dir.exists():
        shutil.rmtree(partial_dir, ignore_errors=True)
    partial_dir.mkdir(parents=True)

    (partial_dir / "diff.patch").write_bytes(diff_bytes)
    files_dir = partial_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for rel in files:
        if not rel or rel.endswith("/"):
            continue
        try:
            content = _git_run(repo, "show", f"{head_sha}:{rel}")
        except ReviewTargetUnreachable:
            continue
        dest = files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    (partial_dir / "manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if final_dir.exists():
        shutil.rmtree(final_dir, ignore_errors=True)
    os.rename(partial_dir, final_dir)
    return manifest


def layer_uri(layer_id: str) -> str:
    return f"cortex://tmp/review-layers/{layer_id}/"


def format_review_layer_prompt_block(
    *,
    layer_id: str,
    manifest: ReviewLayerManifest,
) -> str:
    uri = layer_uri(layer_id)
    file_lines = "\n".join(f"- {path}" for path in manifest.files) or "- (none)"
    return (
        "\n\n## review_layer\n"
        f"uri: {uri}\n"
        f"base_sha: {manifest.base_sha}\n"
        f"head_sha: {manifest.head_sha}\n"
        f"diff_sha256: {manifest.diff_sha256}\n"
        f"files:\n{file_lines}\n"
        "The reviewer must read the review layer at the URI above, not hub master.\n"
    )
