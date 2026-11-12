"""Coerce map-shaped ``expected_read_sha256`` at the unified fs / markdown boundary."""

from __future__ import annotations

from typing import Any

from tools._file_helpers import _path_match_keys
from tools._sparse_document_read import (
    expected_read_sha256_key_error,
    param_not_applicable_error,
)


def _single_path_expected_read_ops() -> frozenset[str]:
    from tools.filesystem._fs_dispatch import SINGLE_PATH_EXPECTED_READ_OPS

    return SINGLE_PATH_EXPECTED_READ_OPS


def _known_workspaces_repo_names() -> set[str]:
    try:
        from tools._project_paths import project_root, repo_roots

        return {repo.name for repo in repo_roots(project_root())}
    except Exception:
        return set()


def workspaces_target_repo_and_rel(
    path: str,
    *,
    workspaces_target_repo: str | None = None,
) -> tuple[str, str]:
    """Resolve workspaces CAS target repo + repo-relative path for key matching."""
    bare = str(path or "").strip().lstrip("/")
    if not bare:
        return "", ""
    known = _known_workspaces_repo_names()
    if workspaces_target_repo:
        repo = workspaces_target_repo.strip("/")
        if bare == repo:
            return repo, ""
        if bare.startswith(f"{repo}/"):
            return repo, bare[len(repo) + 1 :]
        return repo, bare
    if "/" in bare:
        first, rest = bare.split("/", 1)
        if first in known:
            return first, rest
    return "", bare


def _workspaces_accepted_spellings(target_repo: str, target_rel: str) -> list[str]:
    repo = target_repo.strip("/")
    rel = target_rel.lstrip("/")
    if not repo and not rel:
        return []
    if not rel:
        return [repo, f"workspaces://{repo}"]
    spellings = [rel, f"{repo}/{rel}", f"workspaces://{repo}/{rel}"]
    out: list[str] = []
    for item in spellings:
        if item and item not in out:
            out.append(item)
    return out


def _workspaces_key_matches(
    key: str,
    *,
    target_repo: str,
    target_rel: str,
) -> bool:
    text = str(key).strip()
    repo = target_repo.strip("/")
    rel = target_rel.lstrip("/")
    if text.startswith("workspaces://"):
        rest = text.split("://", 1)[1]
        if "/" not in rest:
            k_repo = rest.strip("/")
            k_rel = ""
        else:
            k_repo, k_rel = rest.split("/", 1)
            k_repo = k_repo.strip("/")
            k_rel = k_rel.lstrip("/")
        return k_repo == repo and k_rel == rel
    bare = text.lstrip("/")
    if bare == rel:
        return True
    return bool(repo) and bare == f"{repo}/{rel}"


def _scheme_of_key(key: str) -> str | None:
    text = str(key).strip()
    if "://" not in text:
        return None
    scheme, _ = text.split("://", 1)
    return scheme.lower() if scheme in ("cortex", "workspaces") else None


def _target_aliases(
    path: str,
    sandbox: str,
    *,
    workspaces_target_repo: str | None = None,
) -> set[str]:
    if sandbox == "workspaces":
        repo, rel = workspaces_target_repo_and_rel(
            path, workspaces_target_repo=workspaces_target_repo
        )
        return set(_workspaces_accepted_spellings(repo, rel))
    aliases = set(_path_match_keys(path))
    bare = str(path or "").strip().lstrip("/")
    if bare:
        aliases.add(bare)
    if bare and sandbox:
        aliases.add(f"{sandbox}://{bare}")
    return aliases


def accepted_key_spellings(
    path: str,
    sandbox: str,
    *,
    workspaces_target_repo: str | None = None,
) -> list[str]:
    """Spellings callers may use to name *path* under *sandbox*."""
    aliases = sorted(
        _target_aliases(path, sandbox, workspaces_target_repo=workspaces_target_repo)
    )
    return list(aliases)


def _key_matches_target(
    key: str,
    *,
    path: str,
    sandbox: str,
    workspaces_target_repo: str | None = None,
) -> bool:
    key_scheme = _scheme_of_key(key)
    if key_scheme is not None and key_scheme != sandbox:
        return False
    if sandbox == "workspaces":
        target_repo, target_rel = workspaces_target_repo_and_rel(
            path, workspaces_target_repo=workspaces_target_repo
        )
        if not target_repo and not target_rel:
            return False
        return _workspaces_key_matches(
            key, target_repo=target_repo, target_rel=target_rel
        )
    target_aliases = _target_aliases(path, sandbox)
    key_aliases = set(_path_match_keys(key))
    bare_key = str(key).strip().lstrip("/")
    if bare_key and "://" not in bare_key:
        key_aliases.add(bare_key)
    if key_scheme:
        key_aliases.add(f"{key_scheme}://{bare_key}")
    return bool(target_aliases & key_aliases)


def _normalize_digest_value(
    key: str, raw: Any
) -> tuple[str | None, dict[str, Any] | None]:
    if raw is None or not isinstance(raw, str):
        return None, expected_read_sha256_key_error(
            keys=[key],
            message=(
                f"expected_read_sha256[{key!r}] must be a non-empty digest string."
            ),
            accepted_spellings=None,
        )
    stripped = raw.strip()
    if not stripped:
        return None, expected_read_sha256_key_error(
            keys=[key],
            message=f"expected_read_sha256[{key!r}] must not be empty.",
        )
    return stripped, None


def _resolve_map(
    exp_map: dict[str, Any],
    *,
    path: str,
    sandbox: str,
    workspaces_target_repo: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    if not exp_map:
        return None, expected_read_sha256_key_error(
            keys=[],
            message="expected_read_sha256 map must not be empty.",
        )
    matched: dict[str, str] = {}
    for key, raw in exp_map.items():
        if not _key_matches_target(
            str(key),
            path=path,
            sandbox=sandbox,
            workspaces_target_repo=workspaces_target_repo,
        ):
            continue
        digest, val_err = _normalize_digest_value(str(key), raw)
        if val_err is not None:
            return None, val_err
        matched[str(key)] = digest
    accepted = accepted_key_spellings(
        path, sandbox, workspaces_target_repo=workspaces_target_repo
    )
    all_keys = [str(k) for k in exp_map]
    if not matched:
        return None, expected_read_sha256_key_error(
            keys=all_keys,
            message=(
                "expected_read_sha256 has no entry for this read target; "
                f"received keys: {all_keys!r}."
            ),
            accepted_spellings=accepted,
        )
    if len(matched) < len(exp_map):
        return None, expected_read_sha256_key_error(
            keys=all_keys,
            message=(
                "expected_read_sha256 map lists foreign keys for this target: "
                f"{all_keys!r}."
            ),
            accepted_spellings=accepted,
        )
    digests = set(matched.values())
    if len(digests) > 1:
        return None, expected_read_sha256_key_error(
            keys=list(matched.keys()),
            message=(
                "expected_read_sha256 map is ambiguous for this target "
                "(conflicting digests)."
            ),
            accepted_spellings=accepted,
        )
    return next(iter(digests)), None


def coerce_expected_read_sha256_at_fs_boundary(
    *,
    op: str,
    expected: str | dict[str, str] | Any,
    path: str,
    sandbox: str,
    workspaces_target_repo: str | None = None,
) -> tuple[str | dict[str, str], dict[str, Any] | None]:
    """Normalize a map precondition to a scalar for single-path fs ops."""
    single_path_ops = _single_path_expected_read_ops()
    if op == "read_multi":
        if expected is None:
            return "", None
        return expected, None
    if op not in single_path_ops:
        if isinstance(expected, dict):
            return "", expected_read_sha256_key_error(
                keys=list(expected.keys()),
                message=(
                    f"expected_read_sha256 map is not valid for op={op!r} "
                    "(single-path ops only)."
                ),
            )
        if expected is None:
            return "", None
        return str(expected), None
    if op == "md_to_dict" and isinstance(expected, dict):
        return "", param_not_applicable_error(
            parameter="expected_read_sha256",
            keys=[str(k) for k in expected.keys()],
        )
    if not isinstance(expected, dict):
        if expected is None:
            return "", None
        text = str(expected).strip()
        return text, None
    digest, err = _resolve_map(
        expected,
        path=path,
        sandbox=sandbox,
        workspaces_target_repo=workspaces_target_repo,
    )
    if err is not None:
        return "", err
    assert digest is not None
    return digest, None
