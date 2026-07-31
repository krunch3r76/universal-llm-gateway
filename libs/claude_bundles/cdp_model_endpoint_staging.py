"""Stage CDP generate inputs to cortex:// for the web-anthropic satellite.

``cortex://`` is the preferred packaging for web-anthropic — fewer tool calls on
the satellite side, faster first response. The model-endpoint adapter stages
checkout-relative and workspaces:// refs under
``cortex://notes/system/ephemeral/cdp-endpoint/<execution_id>/`` before submit.
Unstageable inputs fail closed.

Judgment-pair skills (``reasoning-posture``, ``frontier-reasoning-discipline``)
are always merged into ``skills=`` before staging — including light-bounded
dispatches that omit ``skills`` (``decision:reasoning-frontier-skill-pair``).
Because that merge always rewrites the prompt body, the generate path materializes
an ephemeral ``prompt.md`` rather than passing a pre-staged ``cortex://`` URI
through unchanged. ``stage_prompt_uri`` still pass-throughs ``cortex://`` when
called without a skills rewrite (tests / non-generate callers).

Slug-appropriateness gate (a:27430): ``path-sim`` is rejected on CDP ``skills=``
(loud 422). Cascade Q/R legs seal path-sim content into the prompt URI; they do
not list ``path-sim`` in ``skills=``. ``partition_cdp_skills`` stays a pure
channel router — this gate lives here, not there.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root, workspaces_root

_EPHEMERAL_PREFIX = "notes/system/ephemeral/cdp-endpoint"

# Scope rails ≺ epistemic quality — always on CDP skills= (light-bounded too).
CDP_JUDGMENT_SKILL_SLUGS: tuple[str, ...] = (
    "reasoning-posture",
    "frontier-reasoning-discipline",
)

# a:27430 — one-slug denylist at CDP skills= staging (not a general policy DSL).
_CDP_SKILLS_DENIED_SLUGS: frozenset[str] = frozenset({"path-sim"})
_CDP_SKILLS_PATH_SIM_REJECTED = "cdp_skills_path_sim_rejected"


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


def read_prompt_text(
    *,
    prompt_text: str | None = None,
    prompt_uri: str | None = None,
    packet_path: str | None = None,
    sidecar_ref: str | None = None,
) -> str:
    """Resolve CDP prompt inputs to a mutable text body.

    Used when skills= must prepend slash/inline delivery before staging.
    """
    if prompt_text is not None and prompt_text.strip():
        return prompt_text
    for candidate in (prompt_uri, sidecar_ref, packet_path):
        if not candidate or not str(candidate).strip():
            continue
        raw = str(candidate).strip()
        if raw.startswith("cortex://"):
            rel = raw[len("cortex://") :].lstrip("/")
            path = cortex_files_root() / rel
            if not path.is_file():
                raise CdpStagingError(
                    f"cortex prompt missing on disk: {raw!r}",
                    code="cdp_prompt_missing",
                )
            return path.read_text(encoding="utf-8")
        source = resolve_workspaces_path(raw)
        if source is None:
            raise CdpStagingError(
                f"unstageable CDP prompt input: {raw!r} "
                "(not cortex:// and not a readable workspaces/checkout path)",
            )
        return source.read_text(encoding="utf-8")
    raise CdpStagingError(
        "CDP generate requires prompt_text, prompt_uri, sidecar_ref, or packet_path",
        code="cdp_prompt_missing",
    )


def ensure_cdp_judgment_skills(skills: list[str] | None) -> list[str]:
    """Merge judgment-pair defaults into CDP ``skills=`` (idempotent).

    Always — including light-bounded / omitted ``skills``. Caller order is
    preserved; missing pair members are prepended in doctrine order
    (posture ≺ frontier). ``decision:reasoning-frontier-skill-pair``.
    """
    caller = [str(s).strip() for s in (skills or []) if str(s).strip()]
    have = {s.lstrip("/").lower() for s in caller}
    missing = [slug for slug in CDP_JUDGMENT_SKILL_SLUGS if slug not in have]
    return missing + caller


def reject_cdp_skills_path_sim(skills: list[str] | None) -> None:
    """Fail closed when ``path-sim`` appears in CDP ``skills=`` (a:27430).

    ``path-sim`` is a cascade choke-point cue, not a CDP skills= leaf. Lawful
    Q/A/R cascade legs seal path-sim content into the prompt URI; listing the
    slug here inlines a body whose own carve-out excludes codework architect
    binds. Surface class (cursor_only → inline) is a different axis — this gate
    does not reclassify delivery channel.
    """
    if not skills:
        return
    offenders = [
        raw
        for raw in skills
        if str(raw).strip().lstrip("/").lower() in _CDP_SKILLS_DENIED_SLUGS
    ]
    if not offenders:
        return
    raise CdpStagingError(
        "path-sim must not appear in CDP skills= "
        "(cascade legs seal path-sim into the prompt URI; "
        "architect/admit binds use the judgment pair only; a:27430)",
        code=_CDP_SKILLS_PATH_SIM_REJECTED,
    )


def stage_cdp_prompt_with_skills(
    *,
    execution_id: str,
    prompt_text: str | None = None,
    prompt_uri: str | None = None,
    packet_path: str | None = None,
    sidecar_ref: str | None = None,
    skills: list[str] | None = None,
) -> StagedPrompt:
    """Stage CDP input; ``skills`` prepends slash/inline manifest.

    Judgment-pair skills are always ensured before prepend — callers may omit
    ``skills`` on light-bounded CDP generate and still get the pair attached.

    Leading ``/<slug>\\n`` lines are **manifest only** — the Jupiter satellite
    attaches each ``shared_sync`` slug via composer **+ → Skills → pick**
    (``composer_session_skills.attach_session_skills``), never slash-type.

    Rejects ``path-sim`` in ``skills=`` (``cdp_skills_path_sim_rejected``).
    """
    from claude_bundles.cowork_skill_delivery import (
        SkillDeliveryError,
        prepend_cdp_dispatch_skills,
    )

    reject_cdp_skills_path_sim(skills)
    effective = ensure_cdp_judgment_skills(skills)
    body = read_prompt_text(
        prompt_text=prompt_text,
        prompt_uri=prompt_uri,
        packet_path=packet_path,
        sidecar_ref=sidecar_ref,
    )
    try:
        merged, _, _ = prepend_cdp_dispatch_skills(body, effective)
    except KeyError as exc:
        raise CdpStagingError(
            f"unknown skill in skills=: {exc.args[0] if exc.args else exc}",
            code="cdp_skills_unknown",
        ) from exc
    except SkillDeliveryError as exc:
        raise CdpStagingError(str(exc), code="cdp_skills_delivery") from exc
    return stage_prompt_uri(
        execution_id=execution_id,
        prompt_text=merged,
    )


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
    ``packet_path``. ``cortex://`` inputs pass through without copy when this
    helper is called directly. The generate path goes through
    ``stage_cdp_prompt_with_skills``, which always rewrites to ephemeral
    ``prompt.md`` after the judgment-pair merge.
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
