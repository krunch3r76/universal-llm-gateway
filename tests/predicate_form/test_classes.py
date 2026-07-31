"""Per-class unit tests — one positive + one no-op per Class 1-6."""

from __future__ import annotations

from predicate_form.classes import (
    apply_class_1,
    apply_class_2,
    apply_class_3,
    apply_class_4,
    class_6_check,
)
from predicate_form.entity_resolve import StaticEntityResolver
from predicate_form.parser import Predicate


def test_class_1_synonym_rewrite() -> None:
    p = Predicate("status", ("foo", "reassigned_to_another_department"))
    out, fired = apply_class_1(p)
    assert fired
    assert out.args == ("foo", "reassigned")


def test_class_1_no_op() -> None:
    p = Predicate("status", ("foo", "reassigned"))
    out, fired = apply_class_1(p)
    assert not fired
    assert out == p


def test_class_3_case_id() -> None:
    p = Predicate("role", ("foo", "filer", "24PR197054"))
    out, fired = apply_class_3(p)
    assert fired
    assert out.args == ("foo", "filer", "24pr197054")


def test_class_3_strip_case_prefix() -> None:
    p = Predicate("role", ("foo", "case_24PR197054"))
    out, fired = apply_class_3(p)
    assert fired
    assert out.args == ("foo", "24pr197054")


def test_class_3_month_name_date() -> None:
    p = Predicate("status", ("foo", "unavailable", "August_21_2024"))
    out, fired = apply_class_3(p)
    assert fired
    assert out.args == ("foo", "unavailable", "august_21_2024")


def test_class_3_no_op() -> None:
    p = Predicate("status", ("foo", "ready_to_file"))
    out, fired = apply_class_3(p)
    assert not fired
    assert out == p


def test_class_3_short_token_not_case_id() -> None:
    p = Predicate("role", ("foo", "filer", "case"))
    out, fired = apply_class_3(p)
    assert not fired
    assert out.args == ("foo", "filer", "case")


def test_class_4_filing_fees_split() -> None:
    p = Predicate("has_attribute", ("legal_matter:x", "filing_fees_total_466_44"))
    out, fired = apply_class_4(p)
    assert fired
    assert out.args == ("legal_matter:x", "filing_fees", "466.44")


def test_class_4_no_op_on_3_arg() -> None:
    p = Predicate("has_attribute", ("legal_matter:x", "filing_fees", "466.44"))
    out, fired = apply_class_4(p)
    assert not fired
    assert out == p


def test_class_2_prefix_rewrite() -> None:
    resolver = StaticEntityResolver({"camelia-mahmoudi": "person:camelia-mahmoudi"})
    p = Predicate("status", ("camelia_mahmoudi", "ready_to_file"))
    out, fired = apply_class_2(p, resolver)
    assert fired
    assert out.args == ("person:camelia-mahmoudi", "ready_to_file")


def test_class_2_unknown_slug_stays_bare() -> None:
    resolver = StaticEntityResolver({})
    p = Predicate(
        "role", ("kaywan_mansubi", "administrator", "estate_of_dr_fred_mansubi")
    )
    out, fired = apply_class_2(p, resolver)
    assert not fired
    assert out == p


def test_class_2_passes_through_already_prefixed_and_numeric() -> None:
    resolver = StaticEntityResolver({})
    p = Predicate("has_attribute", ("legal_matter:foo", "cost", "450"))
    out, fired = apply_class_2(p, resolver)
    assert not fired
    assert out == p


def test_class_6_generic_state_on_account_flags() -> None:
    p = Predicate("status", ("account:foo", "rejected", "current"))
    assert class_6_check("account:foo", p) is True


def test_class_6_workflow_entity_exempt() -> None:
    p = Predicate("status", ("todo:foo", "completed"))
    assert class_6_check("todo:foo", p) is False


def test_class_6_specific_state_no_op() -> None:
    p = Predicate("status", ("person:foo", "ready_to_file"))
    assert class_6_check("person:foo", p) is False
