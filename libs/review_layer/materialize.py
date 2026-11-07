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
_GIT_TIMEOUT_S = 30
_MAX_LAYER_FILES = 200
_MAX_LAYER_BYTES = 5 * 1024 * 1024
_LAYER_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MANIFEST_FALLBACK_AGE = timedelta(hours=24)


class ReviewTargetUnreachable(Exception):  # noqa: N818 — stable API name in AC
    """Raised when a review head cannot be materialized from hub git."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ReviewLayerManifest:
    """On-disk manifest for a materialized review layer."""

    base_sha: str
    head_sha: str
    diff_sha256: str
    files: tuple[str, ...]
    created_at: str
    expires_at: str
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "diff_sha256": self.diff_sha256,
            "files": list(self.files),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }
        if self.truncated:
            payload["truncated"] = True
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewLayerManifest:
        return cls(
            base_sha=str(data["base_sha"]),
            head_sha=str(data["head_sha"]),
            diff_sha256=str(data["diff_sha256"]),
            files=tuple(str(p) for p in data.get("files") or ()),
            created_at=str(data["created_at"]),
            expires_at=str(data["expires_at"]),
            truncated=bool(data.get("truncated")),
        )


def hub_repo_path() -> Path:
    """Resolve the hub git checkout used for read-only review-layer export."""
    raw = os.environ.get("GIT_INTEGRATION_SOURCE_REPO", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from implement_admission.closeout_helpers import workspaces_root

    return workspaces_root()


def validate_layer_id(layer_id: str) -> None:
    """Refuse layer ids that would collide with partial dirs or path traversal."""
    if not layer_id or layer_id.endswith(_PARTIAL_SUFFIX):
        raise ReviewTargetUnreachable("invalid layer id")
    if not _LAYER_ID_RE.fullmatch(layer_id):
        raise ReviewTargetUnreachable("invalid layer id")


def _validate_git_ref(ref: str) -> None:
    if ref.startswith("-"):
        raise ReviewTargetUnreachable("invalid git ref")


def _git_run_bytes(repo: Path, *args: str) -> bytes:
    """Run a git subprocess and return raw stdout bytes (no decode/strip)."""
    _ = repo  # repo validated by caller via -C
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewTargetUnreachable("git command timed out") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b"git failed")[:240]
        try:
            reason = err.decode("utf-8", errors="replace").strip()
        except Exception:
            reason = "git failed"
        raise ReviewTargetUnreachable(reason)
    return proc.stdout


def _resolve_commit(repo: Path, ref: str) -> str:
    """Resolve a ref to a 40-char commit sha."""
    _validate_git_ref(ref)
    out = _git_run_bytes(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    sha = out.decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReviewTargetUnreachable("invalid commit sha")
    return sha


def _merge_base(repo: Path, *refs: str) -> str:
    out = _git_run_bytes(repo, "merge-base", *refs)
    sha = out.decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReviewTargetUnreachable("merge-base failed")
    return sha


def _default_base(repo: Path, head_sha: str) -> str:
    for base_ref in ("master", "main"):
        try:
            base_tip = _resolve_commit(repo, base_ref)
        except ReviewTargetUnreachable:
            continue
        try:
            return _merge_base(repo, base_tip, head_sha)
        except ReviewTargetUnreachable:
            continue
    raise ReviewTargetUnreachable("no merge-base against master/main")


def _diff_base_sha(
    repo: Path, head_sha: str, base_ref: str | None
) -> str:
    """Three-dot semantics: merge-base between optional base ref and head."""
    if base_ref:
        base_tip = _resolve_commit(repo, base_ref)
        return _merge_base(repo, base_tip, head_sha)
    return _default_base(repo, head_sha)


def _is_ancestor(repo: Path, descendant: str, ancestor: str) -> bool:
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                descendant,
                ancestor,
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewTargetUnreachable("git command timed out") from exc
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    err = (proc.stderr or proc.stdout or b"ancestor check failed")[:240]
    raise ReviewTargetUnreachable(err.decode("utf-8", errors="replace").strip())


def _parse_name_status_z(raw: bytes) -> list[str]:
    """Parse ``git diff --name-status -z --no-renames`` into repo-relative paths."""
    paths: list[str] = []
    chunks = raw.split(b"\0")
    idx = 0
    while idx < len(chunks):
        status_blob = chunks[idx]
        idx += 1
        if not status_blob:
            continue
        if idx >= len(chunks):
            break
        rel_blob = chunks[idx]
        idx += 1
        if not rel_blob:
            continue
        status = status_blob.decode("ascii", errors="replace")
        if status.startswith("D"):
            continue
        rel = rel_blob.decode("utf-8", errors="surrogateescape")
        if rel:
            paths.append(rel)
    return paths


def _cat_file_batch(repo: Path, head_sha: str, paths: list[str]) -> dict[str, bytes]:
    """Fetch blob bytes for ``paths`` at ``head_sha`` via one ``cat-file --batch``."""
    if not paths:
        return {}
    requests = "".join(f"{head_sha}:{p}\n" for p in paths).encode("ascii")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            input=requests,
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewTargetUnreachable("git command timed out") from exc
    if proc.returncode != 0:
        raise ReviewTargetUnreachable("cat-file batch failed")
    return _parse_cat_file_batch(proc.stdout, paths)


def _parse_cat_file_batch(stdout: bytes, paths: list[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    idx = 0
    data = stdout
    for path in paths:
        nl = data.find(b"\n", idx)
        if nl < 0:
            break
        header = data[idx:nl].decode("ascii", errors="replace")
        idx = nl + 1
        parts = header.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        try:
            size = int(parts[2])
        except ValueError:
            continue
        blob = data[idx : idx + size]
        idx += size
        if idx < len(data) and data[idx : idx + 1] == b"\n":
            idx += 1
        out[path] = blob
    return out


def _safe_file_dest(files_dir: Path, rel: str) -> Path:
    dest = (files_dir / rel).resolve()
    root = files_dir.resolve()
    if not dest.is_relative_to(root):
        raise ReviewTargetUnreachable("unsafe file path in diff")
    return dest


def _read_manifest_file(path: Path) -> ReviewLayerManifest | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return ReviewLayerManifest.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return None


def review_layer_exists(cortex_root: Path, layer_id: str) -> bool:
    """True when ``layer_id`` has a manifest on disk under cortex review-layers."""
    manifest = cortex_root / _REVIEW_LAYERS_PREFIX / layer_id / "manifest.json"
    return manifest.is_file()


def any_cited_layer_present(body: str, cortex_root: Path | None = None) -> bool:
    """True when the prompt cites a review-layer URI whose manifest exists on disk."""
    from review_layer.detect import cited_layer_ids

    root = cortex_root or cortex_files_root()
    for layer_id in cited_layer_ids(body):
        if review_layer_exists(root, layer_id):
            return True
    return False


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
            try:
                age = now_ts - child.stat().st_mtime
            except OSError:
                continue
            if age >= _STALE_PARTIAL_AGE_S:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            mtime_age = now_ts - child.stat().st_mtime
        except OSError:
            mtime_age = _MANIFEST_FALLBACK_AGE.total_seconds() + 1
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        expired = False
        if isinstance(payload, dict):
            expires_raw = str(payload.get("expires_at") or "")
            if expires_raw:
                try:
                    expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=UTC)
                    expired = expires < now
                except ValueError:
                    expired = mtime_age >= _MANIFEST_FALLBACK_AGE.total_seconds()
            else:
                expired = mtime_age >= _MANIFEST_FALLBACK_AGE.total_seconds()
        else:
            expired = mtime_age >= _MANIFEST_FALLBACK_AGE.total_seconds()
        if expired:
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
    """Materialize a review layer when ``head`` is not reachable from master.

    Returns ``None`` when ``head`` is already an ancestor of master (no layer needed).
    Reuses an existing layer directory when head+base match (idempotent).
    """
    validate_layer_id(layer_id)
    repo = (repo or hub_repo_path()).resolve()
    if not (repo / ".git").exists():
        raise ReviewTargetUnreachable("hub path is not a git repository")

    head_sha = _resolve_commit(repo, head_ref)

    for master_ref in ("master", "main"):
        try:
            master_sha = _resolve_commit(repo, master_ref)
        except ReviewTargetUnreachable:
            continue
        if _is_ancestor(repo, head_sha, master_sha):
            return None

    base_sha = _diff_base_sha(repo, head_sha, base_ref)

    cortex_root = cortex_root or cortex_files_root()
    layers_root = cortex_root / _REVIEW_LAYERS_PREFIX
    layers_root.mkdir(parents=True, exist_ok=True)
    gc_expired_layers(layers_root, now=now)

    final_dir = layers_root / layer_id
    if final_dir.is_dir():
        existing = _read_manifest_file(final_dir / "manifest.json")
        if (
            existing is not None
            and existing.head_sha == head_sha
            and existing.base_sha == base_sha
        ):
            return existing
        raise ReviewTargetUnreachable("layer id already used for a different revision")

    diff_bytes = _git_run_bytes(repo, "diff", f"{base_sha}..{head_sha}")
    diff_sha256 = hashlib.sha256(diff_bytes).hexdigest()

    name_status = _git_run_bytes(
        repo, "diff", "--name-status", "-z", "--no-renames", f"{base_sha}..{head_sha}"
    )
    all_paths = _parse_name_status_z(name_status)

    truncated = len(all_paths) > _MAX_LAYER_FILES
    paths = all_paths[:_MAX_LAYER_FILES]
    blobs = _cat_file_batch(repo, head_sha, paths)

    written_files: list[str] = []
    total_bytes = len(diff_bytes)
    partial_dir = layers_root / f"{layer_id}{_PARTIAL_SUFFIX}"
    if partial_dir.exists():
        shutil.rmtree(partial_dir, ignore_errors=True)

    try:
        partial_dir.mkdir(parents=True)
        (partial_dir / "diff.patch").write_bytes(diff_bytes)
        files_dir = partial_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        for rel in paths:
            content = blobs.get(rel)
            if content is None:
                continue
            if total_bytes + len(content) > _MAX_LAYER_BYTES:
                truncated = True
                break
            dest = _safe_file_dest(files_dir, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            written_files.append(rel)
            total_bytes += len(content)

        now = now or datetime.now(UTC)
        expires = now + _DEFAULT_TTL
        manifest = ReviewLayerManifest(
            base_sha=base_sha,
            head_sha=head_sha,
            diff_sha256=diff_sha256,
            files=tuple(written_files),
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            truncated=truncated,
        )
        (partial_dir / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not final_dir.exists():
            os.rename(partial_dir, final_dir)
        else:
            shutil.rmtree(partial_dir, ignore_errors=True)
    except Exception:
        shutil.rmtree(partial_dir, ignore_errors=True)
        raise

    return manifest


def layer_uri(layer_id: str) -> str:
    """Canonical cortex URI for a review layer directory."""
    return f"cortex://tmp/review-layers/{layer_id}/"


def format_review_layer_prompt_block(
    *,
    layer_id: str,
    manifest: ReviewLayerManifest,
    max_listed: int = 50,
) -> str:
    """Appendix block injected into API review prompts."""
    uri = layer_uri(layer_id)
    files = list(manifest.files)
    if len(files) > max_listed:
        shown = files[:max_listed]
        extra = len(files) - max_listed
        file_lines = "\n".join(f"- {path}" for path in shown)
        file_lines += f"\n- +{extra} more"
    else:
        file_lines = "\n".join(f"- {path}" for path in files) or "- (none)"
    trunc_line = "truncated: true\n" if manifest.truncated else ""
    return (
        "\n\n## review_layer\n"
        f"uri: {uri}\n"
        f"base_sha: {manifest.base_sha}\n"
        f"head_sha: {manifest.head_sha}\n"
        f"diff_sha256: {manifest.diff_sha256}\n"
        f"files:\n{file_lines}\n"
        f"{trunc_line}"
        "The reviewer must read the review layer at the URI above, not hub master.\n"
    )
