"""Unit tests for pipelines/doc_generate/handlers/enforce_grounding.py.

Scope — DETERMINISTIC GUARD CORE (no pipeline context, no model):
- _authored_regions: paired + bare AUTHORED marker extraction
- AUTHORED string-equality loss diff (the F3-Q3-B guarantee, at the logic level):
  dropped region -> loss; altered region -> loss; preserved -> no loss
- _stamp_and_disclaim: GENERATED provenance stamp + disclaimer injection (F3-Q3-C, F1-Q1-A)
- _missing_coverage: deterministic inventory symbol vs doc-mention scan
- HUMAN marker drop detection (advisory)

The END-TO-END acceptance assertion — execute() raises PipelineExecutionError and
emits NO doc on AUTHORED loss — exercises the pipeline handler/resolver substrate and
is covered by the integration test authored under the universal-stargate test env
(see cortex todo:doc-gen-f3-authored-loss). These unit tests pin the pure decision
logic that the raise path depends on.
"""

from . import enforce_grounding as eg

_authored_regions = eg._authored_regions
_missing_coverage = eg._missing_coverage
_stamp_and_disclaim = eg._stamp_and_disclaim
_HUMAN_RE = eg._HUMAN_RE
_DISCLAIMER = eg._DISCLAIMER


# ---------------------------------------------------------------------------
# _authored_regions — marker extraction (paired + bare forms)
# ---------------------------------------------------------------------------


def test_authored_paired_region_extracted():
    doc = "intro\n<!-- AUTHORED:START -->\nkeep me\n<!-- AUTHORED:END -->\noutro"
    assert _authored_regions(doc) == ["keep me"]


def test_authored_bare_region_runs_to_next_marker():
    doc = (
        "<!-- AUTHORED -->\nhand-written rationale\n"
        "<!-- GENERATED:START -->\nauto stuff\n"
    )
    assert _authored_regions(doc) == ["hand-written rationale"]


def test_authored_bare_region_runs_to_eof():
    doc = "lead\n<!-- AUTHORED -->\ntrailing human note"
    assert _authored_regions(doc) == ["trailing human note"]


def test_empty_authored_region_dropped():
    doc = "<!-- AUTHORED:START -->\n   \n<!-- AUTHORED:END -->"
    assert _authored_regions(doc) == []


def test_no_authored_markers_returns_empty():
    assert _authored_regions("just generated content, no markers") == []


# ---------------------------------------------------------------------------
# AUTHORED string-equality loss diff — the F3 guard's decision logic
# (mirrors enforce_grounding.execute(): region present in existing_doc and
#  absent from reviewed_doc => loss; the handler raises on a non-empty result.)
# ---------------------------------------------------------------------------


def _authored_loss(existing_doc: str, reviewed_doc: str) -> list[str]:
    return [r for r in _authored_regions(existing_doc) if r not in reviewed_doc]


def test_dropped_authored_region_is_loss():
    existing = (
        "<!-- AUTHORED:START -->\ncritical human design note\n<!-- AUTHORED:END -->"
    )
    reviewed = "## Overview\nregenerated body with the human note silently gone\n"
    loss = _authored_loss(existing, reviewed)
    assert loss == ["critical human design note"]


def test_preserved_authored_region_no_loss():
    region = "critical human design note"
    existing = f"<!-- AUTHORED:START -->\n{region}\n<!-- AUTHORED:END -->"
    # Reviewed doc keeps the region body verbatim (markers may move/be re-emitted).
    reviewed = f"## Overview\nbody\n\n{region}\n"
    assert _authored_loss(existing, reviewed) == []


def test_altered_authored_region_is_loss():
    """String equality: any mutation of the region body counts as loss."""
    existing = (
        "<!-- AUTHORED:START -->\nrationale: we chose X over Y\n<!-- AUTHORED:END -->"
    )
    reviewed = "<!-- AUTHORED:START -->\nrationale: we chose X\n<!-- AUTHORED:END -->"
    loss = _authored_loss(existing, reviewed)
    assert loss == ["rationale: we chose X over Y"]


def test_no_authored_in_existing_no_loss():
    assert _authored_loss("plain prior doc", "regenerated doc") == []


# ---------------------------------------------------------------------------
# _stamp_and_disclaim — provenance stamp + disclaimer injection
# ---------------------------------------------------------------------------


def test_stamp_injects_sha_date_and_disclaimer():
    doc = "<!-- GENERATED:START -->\nbody\n<!-- GENERATED:END -->"
    out = _stamp_and_disclaim(doc, "abc123def456", "2026-06-10")
    assert "inventory_sha=abc123def456" in out
    assert "generated=2026-06-10" in out
    assert _DISCLAIMER in out
    # Original body and END marker are untouched.
    assert "body" in out
    assert "<!-- GENERATED:END -->" in out


def test_stamp_rewrites_every_generated_block():
    doc = (
        "<!-- GENERATED:START -->\na\n<!-- GENERATED:END -->\n"
        "<!-- GENERATED:START -->\nb\n<!-- GENERATED:END -->"
    )
    out = _stamp_and_disclaim(doc, "sha", "2026-06-10")
    assert out.count("inventory_sha=sha") == 2
    assert out.count(_DISCLAIMER) == 2


def test_stamp_noop_without_generated_marker():
    doc = "<!-- AUTHORED:START -->\nhuman\n<!-- AUTHORED:END -->"
    assert _stamp_and_disclaim(doc, "sha", "2026-06-10") == doc


# ---------------------------------------------------------------------------
# _missing_coverage — deterministic symbol-vs-doc scan
# ---------------------------------------------------------------------------


def test_missing_coverage_flags_absent_symbol():
    inventory = {
        "classes": [{"name": "Widget"}],
        "functions": [{"name": "render_widget"}],
    }
    doc = "This doc mentions Widget but not the render function."
    missing = _missing_coverage(doc, inventory)
    assert missing == ["function:render_widget"]


def test_missing_coverage_clean_when_all_present():
    inventory = {"functions": [{"name": "do_thing"}]}
    doc = "The do_thing function is fully documented here."
    assert _missing_coverage(doc, inventory) == []


def test_missing_coverage_ignores_private_symbols():
    inventory = {"functions": [{"name": "_private_helper"}]}
    assert _missing_coverage("no mention at all", inventory) == []


def test_missing_coverage_module_uses_basename():
    inventory = {"modules": [{"path": "pkg/sub/widgets.py"}]}
    doc = "See widgets.py for details."
    assert _missing_coverage(doc, inventory) == []


# ---------------------------------------------------------------------------
# HUMAN marker drop detection (advisory)
# ---------------------------------------------------------------------------


def test_human_marker_drop_detected():
    existing = "<!-- HUMAN: needs a deployment diagram -->\nbody"
    reviewed = "body without the marker"
    dropped = [m for m in _HUMAN_RE.findall(existing) if m not in reviewed]
    assert dropped == ["<!-- HUMAN: needs a deployment diagram -->"]


def test_human_marker_preserved_not_flagged():
    marker = "<!-- HUMAN: needs a deployment diagram -->"
    existing = f"{marker}\nbody"
    reviewed = f"{marker}\nregenerated body"
    dropped = [m for m in _HUMAN_RE.findall(existing) if m not in reviewed]
    assert dropped == []
