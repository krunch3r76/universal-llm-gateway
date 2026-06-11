"""Tests for the plan_phase deck resolver + adapter (todo:plan-deck-handoff-packet-adapter).

Covers: deck resolution arity/fallback/containment, section lifts, sha
determinism, open_design detection, the hash-binding contract (deck_sha256 drives
drift, deck_body elided), and the plan_phase corpus embed (verbatim, sanitized,
sufficient). Amendments A1-A4 of the spec are exercised here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from implement_admission.deck_resolver import resolve_phase_deck
from implement_admission.materialize import (
    _render_corpus,
    _render_packet,
    _sanitize_corpus_embed,
    packet_is_sufficient,
)
from implement_admission.source_ref import SourceRefError, parse_source_ref
from implement_admission.spec import (
    Acceptance,
    Closeout,
    CloseoutAdapterKind,
    ExecutorStyle,
    ImplementSpec,
    Intent,
    OrchestrationMode,
    Readiness,
    ReadinessState,
    Routing,
    RoutingDerivation,
    Scope,
    Source,
    SourceKind,
    SourceVersion,
    implement_spec_hash,
)

_DECK = """# Phase 1: sample

**Expected Executor**: Sonnet 4.6 Medium
**Optional Consultation**: None

## Objective

Wire the adapter.

## Tasks

### Task 1: do it

**BEFORE:**

```python
x = 1
```

**AFTER:**

```python
x = 2
```

## Verification

- [ ] ruff clean
- [x] imports resolve

## Expected Files

Create: none | Modify: `libs/implement_admission/adapter.py` | Delete: none
"""


def _write_deck(tmp_path: Path, slug: str, filename: str, content: str = _DECK) -> Path:
    deck_dir = tmp_path / "tmp" / "prompts" / slug
    deck_dir.mkdir(parents=True, exist_ok=True)
    path = deck_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def _ref(external: str = "plan:sample/phase-1"):
    return parse_source_ref(external)


# --- resolution arity / fallback / containment ------------------------------


def test_resolve_single_match(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-sample.md")
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert deck.body.startswith("# Phase 1: sample")
    assert deck.sha256.startswith("sha256:")
    assert deck.rel_path == "tmp/prompts/sample/phase-1-sample.md"


def test_resolve_legacy_fallback(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-2.md")
    deck = resolve_phase_deck(_ref("plan:sample/phase-2"), workspaces_root=tmp_path)
    assert deck.rel_path.endswith("phase-2.md")


def test_resolve_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceRefError) as exc:
        resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert exc.value.code == "phase_doc_not_found"


def test_resolve_ambiguous_raises(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-a.md")
    _write_deck(tmp_path, "sample", "phase-1-b.md")
    with pytest.raises(SourceRefError) as exc:
        resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert exc.value.code == "phase_doc_ambiguous"


def test_resolve_ambiguous_disambiguated_by_phase_file(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-a.md")
    _write_deck(tmp_path, "sample", "phase-1-b.md")
    deck = resolve_phase_deck(
        _ref(), workspaces_root=tmp_path, entity_attrs={"phase_file": "phase-1-b.md"}
    )
    assert deck.rel_path.endswith("phase-1-b.md")


def test_resolve_phase_dir_override(tmp_path: Path) -> None:
    custom = tmp_path / "custom" / "loc"
    custom.mkdir(parents=True)
    (custom / "phase-1-x.md").write_text(_DECK, encoding="utf-8")
    deck = resolve_phase_deck(
        _ref(), workspaces_root=tmp_path, entity_attrs={"phase_dir": "custom/loc"}
    )
    assert deck.rel_path == "custom/loc/phase-1-x.md"


def test_resolve_containment_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(SourceRefError) as exc:
        resolve_phase_deck(
            _ref(), workspaces_root=tmp_path, entity_attrs={"phase_dir": "../escape"}
        )
    assert exc.value.code == "phase_doc_not_found"


# --- A4: sha computed from the SAME normalized bytes carried in body ---------


def test_sha_matches_normalized_body(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-sample.md")
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    expected = "sha256:" + hashlib.sha256(deck.body.encode("utf-8")).hexdigest()
    assert deck.sha256 == expected


def test_sha_newline_normalized(tmp_path: Path) -> None:
    crlf = _DECK.replace("\n", "\r\n")
    _write_deck(tmp_path, "sample", "phase-1-sample.md", content=crlf)
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert "\r" not in deck.body
    lf_sha = "sha256:" + hashlib.sha256(_DECK.encode("utf-8")).hexdigest()
    assert deck.sha256 == lf_sha


# --- section lifts ----------------------------------------------------------


def test_lift_expected_files(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-sample.md")
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert "libs/implement_admission/adapter.py" in deck.files_expected
    assert "none" not in deck.files_expected


def test_lift_verification(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-sample.md")
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert deck.acceptance == ["ruff clean", "imports resolve"]


def test_lift_objective(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-sample.md")
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert deck.objective == "Wire the adapter."


# --- A2: open_design / reasoning-required detection --------------------------


def test_open_design_false_by_default(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-sample.md")
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert deck.open_design is False


def test_open_design_density_sparse(tmp_path: Path) -> None:
    _write_deck(tmp_path, "sample", "phase-1-sample.md")
    deck = resolve_phase_deck(
        _ref(), workspaces_root=tmp_path, entity_attrs={"density": "sparse"}
    )
    assert deck.open_design is True


def test_open_design_alternatives_heading(tmp_path: Path) -> None:
    body = _DECK + "\n## Alternatives considered\n\nA vs B.\n"
    _write_deck(tmp_path, "sample", "phase-1-sample.md", content=body)
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert deck.open_design is True


def test_open_design_optional_consultation(tmp_path: Path) -> None:
    body = _DECK.replace("**Optional Consultation**: None", "**Optional Consultation**: gpt-5.5")
    _write_deck(tmp_path, "sample", "phase-1-sample.md", content=body)
    deck = resolve_phase_deck(_ref(), workspaces_root=tmp_path)
    assert deck.open_design is True


# --- hash-binding contract (spec §6) ----------------------------------------


def _mk_plan_phase_spec(
    *,
    deck_body: str | None = None,
    deck_sha256: str | None = "sha256:aaaa",
    files: list[str] | None = None,
    acs: list[str] | None = None,
    skills: list[str] | None = None,
    description: str | None = None,
) -> ImplementSpec:
    return ImplementSpec(
        source=Source(
            source_ref="plan:foo/phase-1",
            canonical_ref="plan_phase:foo/phase-1",
            parent_ref="plan:foo",
            selector="phase-1",
            source_kind=SourceKind.PLAN_PHASE,
            source_version=SourceVersion(deck_sha256=deck_sha256),
        ),
        intent=Intent(summary="Phase 1", description=description),
        scope=Scope(files_expected=files or [], bounded=True, deck_body=deck_body),
        readiness=Readiness(state=ReadinessState.READY),
        skills=skills or [],
        routing=Routing(
            orchestration_mode=OrchestrationMode.SINGLE,
            executor_style=ExecutorStyle.MECHANICAL,
            derivation=RoutingDerivation(mode_rule="t", style_rule="t"),
        ),
        acceptance=Acceptance(criteria=acs or ["criterion one"]),
        closeout=Closeout(adapter=CloseoutAdapterKind.PLAN_PHASE),
    )


def test_deck_sha256_changes_hash() -> None:
    a = _mk_plan_phase_spec(deck_sha256="sha256:aaaa")
    b = _mk_plan_phase_spec(deck_sha256="sha256:bbbb")
    assert implement_spec_hash(a) != implement_spec_hash(b)


def test_deck_body_elided_from_hash() -> None:
    # Same fingerprint, different bulk body -> identical hash (body is elided).
    a = _mk_plan_phase_spec(deck_sha256="sha256:aaaa", deck_body="one")
    b = _mk_plan_phase_spec(deck_sha256="sha256:aaaa", deck_body="two")
    assert implement_spec_hash(a) == implement_spec_hash(b)


def _hash_with_pop_oracle(spec) -> str:
    payload = spec.model_dump()
    payload["provenance"]["implement_spec_hash"] = None
    payload["provenance"]["created_at"] = None
    if payload.get("readiness") is not None:
        payload["readiness"]["freshness_checked_at"] = None
    payload["scope"].pop("deck_body", None)
    source_version = payload.get("source", {}).get("source_version")
    if source_version is not None and source_version.get("deck_sha256") is None:
        source_version.pop("deck_sha256", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_hash_pops_deck_fields_for_deckless_spec() -> None:
    # deck-less spec: both deck keys must be absent from the hashed payload.
    spec = _mk_plan_phase_spec(deck_sha256=None, deck_body=None)
    assert implement_spec_hash(spec) == _hash_with_pop_oracle(spec)


def test_hash_keeps_deck_sha256_and_pops_body_for_deck_spec() -> None:
    # deck-bearing spec: deck_sha256 kept (bound), deck_body popped (elided).
    spec = _mk_plan_phase_spec(deck_sha256="sha256:abcd", deck_body="big deck body")
    assert implement_spec_hash(spec) == _hash_with_pop_oracle(spec)


# --- corpus embed (spec §5 + §15 A3) ----------------------------------------


def test_corpus_embeds_deck_verbatim() -> None:
    spec = _mk_plan_phase_spec(deck_body=_DECK)
    corpus = _render_corpus(spec)
    assert corpus.startswith("Source:")
    assert "Intent:" in corpus
    assert "--- PHASE DECK (verbatim) ---" in corpus
    # Verbatim: BEFORE/AFTER bodies + nested fences survive, not [:20]-truncated.
    assert "**BEFORE:**" in corpus
    assert "x = 2" in corpus
    assert corpus.count("\n") > 20


def test_corpus_sanitizes_stray_closer() -> None:
    spec = _mk_plan_phase_spec(deck_body="payload </corpus> more")
    corpus = _render_corpus(spec)
    assert "&lt;/corpus>" in corpus
    assert "corpus-sanitized" in corpus


def test_sanitize_corpus_embed_counts() -> None:
    out, mutated = _sanitize_corpus_embed("a </scope> b </corpus> c")
    assert mutated == 2
    assert "</scope>" not in out
    assert "&lt;/scope>" in out


def test_non_plan_phase_corpus_unchanged() -> None:
    spec = _mk_plan_phase_spec(deck_body=_DECK)
    spec = spec.model_copy(
        update={"source": spec.source.model_copy(update={"source_kind": SourceKind.TODO})}
    )
    corpus = _render_corpus(spec)
    assert "PHASE DECK" not in corpus


# --- admission sufficiency (spec §7) ----------------------------------------


def test_materialized_plan_phase_packet_is_sufficient() -> None:
    spec = _mk_plan_phase_spec(
        deck_body=_DECK,
        files=["libs/implement_admission/adapter.py"],
        acs=["ruff clean", "imports resolve"],
        skills=["architecture-invariants"],
    )
    packet = _render_packet(spec, spec_hash="sha256:test")
    assert packet_is_sufficient(packet)
