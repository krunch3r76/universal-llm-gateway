"""Unit tests for QualifiedScalar, seal gate, and render goldens."""

from __future__ import annotations

import pytest

import admission_common.qualified_scalar as qualified_scalar_module
from admission_common.qualified_scalar import (
    PUBLICATION_BUILDER_CENSUS,
    AbsenceSemantics,
    AuthorityClass,
    QualifiedScalar,
    SurfaceDecl,
    UnqualifiedScalarError,
    seal,
)

pytestmark = pytest.mark.offline


def test_absence_law_none_is_unobserved() -> None:
    scalar = QualifiedScalar(
        value=None,
        scope="browser CSE lanes, this host",
        authority=AuthorityClass.OBSERVED,
    )
    assert scalar.emit("live_cse_count") == {
        "live_cse_count": None,
        "live_cse_count_scope": "browser CSE lanes, this host",
        "live_cse_count_authority": "observed",
    }


def test_zero_is_observed_empty_not_unknown() -> None:
    scalar = QualifiedScalar(
        value=0,
        scope="browser CSE lanes, this host",
        authority=AuthorityClass.OBSERVED,
    )
    assert scalar.value == 0
    assert scalar.render("live_cse_count") == (
        "live_cse_count (browser CSE lanes, this host, observed): 0"
    )


def test_false_is_observed_empty() -> None:
    scalar = QualifiedScalar(
        value=False,
        scope="lane admission",
        authority=AuthorityClass.DERIVED,
    )
    assert scalar.render("busy") == "busy (lane admission, derived): False"


def test_emit_shape_present_value() -> None:
    scalar = QualifiedScalar(
        value=3,
        scope="cdp_ask execution store, pending/running records",
        authority=AuthorityClass.RECORDED,
    )
    assert scalar.emit("running_count") == {
        "running_count": 3,
        "running_count_scope": "cdp_ask execution store, pending/running records",
        "running_count_authority": "recorded",
    }


def test_render_golden_present() -> None:
    scalar = QualifiedScalar(
        value=3,
        scope="worktree main",
        authority=AuthorityClass.OBSERVED,
    )
    assert scalar.render("tree_residue") == "tree_residue (worktree main, observed): 3"


def test_render_golden_absent() -> None:
    scalar = QualifiedScalar(
        value=None,
        scope="closeout checkpoint",
        authority=AuthorityClass.ASSERTED,
    )
    assert scalar.render("checkpoint") == "checkpoint: unobserved"


def test_seal_accepts_fully_qualified_payload() -> None:
    payload = {
        "running_count": 1,
        "running_count_scope": "store",
        "running_count_authority": "recorded",
    }
    decl = SurfaceDecl("active_work_snapshot")
    assert seal(payload, decl) is payload


def test_seal_accepts_plain_registered_bare_scalar() -> None:
    payload = {"soft_limit": 2}
    decl = SurfaceDecl("active_work_snapshot")
    decl.plain("soft_limit", reason="configured lane admission constant")
    assert seal(payload, decl)["soft_limit"] == 2


def test_seal_raises_on_undeclared_bare_scalar() -> None:
    payload = {"rogue_count": 99}
    decl = SurfaceDecl("active_work_snapshot")
    with pytest.raises(UnqualifiedScalarError, match="rogue_count"):
        seal(payload, decl)


def test_seal_raises_on_nested_undeclared_bare_scalar() -> None:
    payload = {"rows": [{"depth": 1}]}
    decl = SurfaceDecl("active_work_snapshot")
    with pytest.raises(UnqualifiedScalarError, match="rows\\[0\\].depth"):
        seal(payload, decl)


def test_seal_skips_transcript_subtree() -> None:
    payload = {
        "effects_manifest": {
            "entries": [{"detail": {"timeout": 30000, "command": "true"}}]
        }
    }
    decl = SurfaceDecl("ImplementCloseout.model_dump")
    decl.transcript(
        "detail",
        reason="captured tool-argument transcript",
        under="effects_manifest",
    )
    assert seal(payload, decl)["effects_manifest"]["entries"][0]["detail"]["timeout"] == (
        30000
    )


def test_seal_refuses_bare_scalar_at_transcript_key() -> None:
    """A number sitting *at* the transcript key is still a published claim."""
    payload = {"detail": 30000}
    decl = SurfaceDecl("ImplementCloseout.model_dump")
    decl.transcript("detail", reason="captured tool-argument transcript")
    with pytest.raises(UnqualifiedScalarError, match=r"\$\.detail"):
        seal(payload, decl)


def test_seal_refuses_transcript_key_outside_under_zone() -> None:
    payload = {"other": {"detail": {"score": 7}}}
    decl = SurfaceDecl("ImplementCloseout.model_dump")
    decl.transcript(
        "detail",
        reason="captured tool-argument transcript",
        under="effects_manifest",
    )
    with pytest.raises(UnqualifiedScalarError, match="other.detail.score"):
        seal(payload, decl)


def test_absence_semantics_closeout_values() -> None:
    assert AbsenceSemantics.ABSENCE_ZERO.value == "absence=zero"
    assert AbsenceSemantics.ABSENCE_UNKNOWN.value == "absence=unknown"


def test_authority_class_closeout_values_preserved() -> None:
    assert AuthorityClass.LEDGER_ATTESTED.value == "ledger_attested"
    assert AuthorityClass.SELF_REPORTED.value == "self_reported"


def test_builder_census_two_sealed_seven_pending() -> None:
    assert len(PUBLICATION_BUILDER_CENSUS) == 9
    sealed = [k for k, v in PUBLICATION_BUILDER_CENSUS.items() if v == "sealed"]
    pending = [k for k, v in PUBLICATION_BUILDER_CENSUS.items() if v == "pending"]
    assert sealed == [
        "execution_store.active_work_snapshot",
        "ImplementCloseout.model_dump",
    ]
    assert len(pending) == 7


_KNOWN_BYPASS_BUILDERS = (
    "mcp_drain.active_work_snapshot",
    "giw.routes.integrate.get_active_work",
    "giw.routes.cursor_sdk.cursor_concurrency_stats",
)


def test_publication_builder_census_covers_known_bypass_builders() -> None:
    for key in _KNOWN_BYPASS_BUILDERS:
        assert key in PUBLICATION_BUILDER_CENSUS, f"missing census entry for {key!r}"
        assert PUBLICATION_BUILDER_CENSUS[key] == "pending"


def test_authority_is_documented_as_producer_attested() -> None:
    assert "producer-attested" in AuthorityClass.__doc__.lower()
    assert "producer-attested" in qualified_scalar_module.__doc__.lower()
