"""Resolve + normalize an implementation-plan phase deck for plan_phase source_refs.

The dispatch-bound lane (a caller that supplies ``workspaces_root``) MUST resolve
and read the on-disk phase deck or hard-fail with ``SourceRefError``; it never
degrades to attr-only normalization (spec plan-deck-handoff-packet-adapter §15
A1). The resolver returns ONE normalized deck object: ``deck_sha256`` is computed
from the exact same normalized bytes carried in ``body`` (§15 A4), so the embedded
corpus body and the hashed fingerprint can never diverge.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from implement_admission.admission_read import read_packet
from implement_admission.dense_spec_schema import DENSE_SPEC_RE
from implement_admission.source_ref import SourceRef, SourceRefError

_ULG_REPO_DIRNAME = "universal-llm-gateway"
_DEFAULT_DECK_DIR = "tmp/prompts/{slug}"
_FILE_PATH_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".json", ".toml", ".mdc")
_NON_PATH_TOKENS = {"none", "n/a", "-", "—"}

_CANONICAL = re.compile(r"^(?P<slug>.+)/phase-(?P<num>\d+)$")
# Markers flagging a deck as carrying unresolved design choices → route REASONING
# even when the file list + ACs look mechanically complete (spec §15 A2).
_OPEN_DESIGN_HEADINGS = re.compile(
    r"^#{1,3}\s+(?:alternatives\s+considered|design\s+decision)\b",
    re.IGNORECASE | re.MULTILINE,
)
_OPTIONAL_CONSULT = re.compile(
    r"^\*{0,2}Optional Consultation\*{0,2}\s*:\s*(?P<val>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CHECKLIST = re.compile(r"^\s*-\s*\[[ xX]\]\s+(?P<item>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class NormalizedDeck:
    """One normalized phase deck. ``sha256`` is over the same bytes as ``body``."""

    body: str
    sha256: str
    rel_path: str
    files_expected: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    objective: str | None = None
    open_design: bool = False


def _slug_and_phase(ref: SourceRef) -> tuple[str, int]:
    rest = (
        ref.canonical_ref.split(":", 1)[1]
        if ":" in ref.canonical_ref
        else ref.canonical_ref
    )
    match = _CANONICAL.match(rest)
    if not match:
        raise SourceRefError(
            code="phase_doc_not_found",
            source_ref=ref.external_ref,
            rule=f"cannot derive slug/phase from canonical_ref {ref.canonical_ref!r}",
        )
    return match.group("slug"), int(match.group("num"))


def _repo_base(workspaces_root: Path) -> Path:
    root = workspaces_root.resolve()
    if root.name == _ULG_REPO_DIRNAME:
        return root
    nested = root / _ULG_REPO_DIRNAME
    return nested if nested.is_dir() else root


def _path_contained_in(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _rel_to_workspaces(full_path: Path, workspaces_root: Path) -> str:
    from implement_admission.share_uri_emit import to_share_uri

    root = workspaces_root.resolve()
    resolved = full_path.resolve()
    repo = _repo_base(root)
    try:
        rel = resolved.relative_to(repo.resolve()).as_posix()
    except ValueError:
        rel = resolved.relative_to(root).as_posix()
    if repo != root:
        rel = f"{repo.name}/{rel}"
    return to_share_uri("workspaces", rel)


def _disambiguate(candidates: list[Path], attrs: dict) -> Path | None:
    target = attrs.get("phase_file")
    if not target:
        return None
    for candidate in candidates:
        if candidate.name == target:
            return candidate
    return None


def _dense_spec_pointer(attrs: dict, source_uri: str | None) -> str | None:
    dense_uri = attrs.get("dense_spec_uri")
    if dense_uri is not None and str(dense_uri).strip():
        return str(dense_uri).strip()
    if source_uri is not None and str(source_uri).strip():
        uri = str(source_uri).strip().removeprefix("files://")
        if DENSE_SPEC_RE.search(uri):
            return uri
    return None


def _resolve_dense_spec_deck(
    ref: SourceRef,
    *,
    pointer: str,
    workspaces_root: Path,
) -> NormalizedDeck:
    try:
        packet = read_packet(pointer, workspaces_root=workspaces_root)
    except SourceRefError as exc:
        raise SourceRefError(
            code="phase_doc_not_found",
            source_ref=ref.external_ref,
            rule=f"dense-spec pointer {pointer!r} unreadable",
            message=str(exc),
        ) from exc

    body = packet.text.replace("\r\n", "\n").replace("\r", "\n")
    sha = f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
    return NormalizedDeck(
        body=body,
        sha256=sha,
        rel_path=pointer,
        files_expected=_lift_expected_files(body),
        acceptance=_lift_verification(body),
        objective=_lift_objective(body),
        open_design=_detect_open_design(body, {}),
    )


def resolve_phase_deck(
    ref: SourceRef,
    *,
    workspaces_root: Path,
    entity_attrs: dict | None = None,
    source_uri: str | None = None,
) -> NormalizedDeck:
    """Resolve ``plan_phase:{slug}/phase-N`` to its on-disk deck and normalize it.

    Raises ``SourceRefError`` (``phase_doc_not_found`` / ``phase_doc_ambiguous``)
    on any resolution failure — the dispatch lane treats these as hard 422s.
    """
    attrs = entity_attrs or {}
    slug, phase_num = _slug_and_phase(ref)
    base = _repo_base(workspaces_root)

    override_dir = attrs.get("phase_dir")
    deck_dir = (
        (base / override_dir)
        if override_dir
        else (base / _DEFAULT_DECK_DIR.format(slug=slug))
    ).resolve()

    candidates: list[Path] = []
    if deck_dir.is_dir():
        candidates = sorted(deck_dir.glob(f"phase-{phase_num}-*.md"))
        legacy = deck_dir / f"phase-{phase_num}.md"
        if legacy.is_file() and legacy not in candidates:
            candidates.append(legacy)

    # Containment guard — reject any candidate resolving outside the sandbox.
    candidates = [c for c in candidates if c.is_file() and _path_contained_in(c, base)]

    if not candidates:
        pointer = _dense_spec_pointer(attrs, source_uri)
        if pointer is not None:
            return _resolve_dense_spec_deck(
                ref,
                pointer=pointer,
                workspaces_root=workspaces_root,
            )
        raise SourceRefError(
            code="phase_doc_not_found",
            source_ref=ref.external_ref,
            rule=(
                f"no phase-{phase_num}-*.md or phase-{phase_num}.md under "
                f"{deck_dir.as_posix()!r} and no dense-spec pointer on entity"
            ),
        )
    if len(candidates) > 1:
        picked = _disambiguate(candidates, attrs)
        if picked is None:
            names = ", ".join(c.name for c in candidates)
            raise SourceRefError(
                code="phase_doc_ambiguous",
                source_ref=ref.external_ref,
                rule=(
                    f"{len(candidates)} matches for phase-{phase_num} ({names}); "
                    "set entity attribute 'phase_file' to disambiguate"
                ),
            )
        candidates = [picked]

    deck_path = candidates[0]
    raw = deck_path.read_text(encoding="utf-8", errors="replace")
    body = raw.replace("\r\n", "\n").replace("\r", "\n")
    sha = f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}"

    return NormalizedDeck(
        body=body,
        sha256=sha,
        rel_path=_rel_to_workspaces(deck_path, workspaces_root),
        files_expected=_lift_expected_files(body),
        acceptance=_lift_verification(body),
        objective=_lift_objective(body),
        open_design=_detect_open_design(body, attrs),
    )


def _looks_like_file_path(path: str) -> bool:
    return "/" in path or path.endswith(_FILE_PATH_SUFFIXES)


def _extract_section(body: str, name_pattern: str) -> str | None:
    rx = re.compile(
        rf"^#{{1,3}}\s+{name_pattern}\b.*?$(?P<sec>.*?)(?=^#{{1,3}}\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = rx.search(body)
    return match.group("sec") if match else None


def _lift_expected_files(body: str) -> list[str]:
    section = _extract_section(body, r"Expected Files")
    if not section:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"`([^`]+)`", section):
        path = raw.strip()
        if (
            path
            and path.lower() not in _NON_PATH_TOKENS
            and _looks_like_file_path(path)
            and path not in seen
        ):
            seen.add(path)
            out.append(path)
    return out


def _lift_verification(body: str) -> list[str]:
    section = _extract_section(body, r"Verification")
    if not section:
        return []
    out: list[str] = []
    for line in section.splitlines():
        match = _CHECKLIST.match(line)
        if match:
            out.append(match.group("item"))
    return out


def _lift_objective(body: str) -> str | None:
    section = _extract_section(body, r"Objective")
    if not section:
        return None
    text = " ".join(
        line.strip() for line in section.splitlines() if line.strip()
    ).strip()
    return text[:280] or None


def _detect_open_design(body: str, attrs: dict) -> bool:
    if str(attrs.get("density", "")).strip().lower() == "sparse":
        return True
    if attrs.get("reasoning_required") in (True, "true", "True", 1, "1"):
        return True
    if _OPEN_DESIGN_HEADINGS.search(body):
        return True
    consult = _OPTIONAL_CONSULT.search(body)
    if consult and consult.group("val").strip().lower() not in _NON_PATH_TOKENS:
        return True
    return False
