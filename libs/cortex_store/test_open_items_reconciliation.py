"""Pure unit tests for open-items reconciliation (no DB)."""

from __future__ import annotations

from cortex_store.open_items.reconcile import (
    build_resolution_index,
    is_resolved,
    reconcile_open_items,
)


def _index(*, slugs: tuple[str, ...] = (), ids: tuple[int, ...] = ()) -> object:
    records = [{"id": i, "slug": None, "claim": "", "entity_name": ""} for i in ids]
    records.extend(
        {"id": None, "slug": s, "claim": "", "entity_name": ""} for s in slugs
    )
    return build_resolution_index(records)


def test_bracketed_todo_ref_resolved() -> None:
    idx = _index(slugs=("my-task",))
    item = "Follow up [todo:my-task] before next session"
    assert is_resolved(item, idx) is True
    out = reconcile_open_items([item], index=idx, omit_resolved=False)
    assert out == [f"[RESOLVED] {item}"]


def test_bare_todo_prefix_resolved() -> None:
    idx = _index(slugs=("boot-fix",))
    item = "todo:boot-fix — next action: land the reconciliation patch"
    assert is_resolved(item, idx) is True
    out = reconcile_open_items([item], index=idx, omit_resolved=True)
    assert out == []


def test_bare_todo_prefix_unresolved_slug() -> None:
    idx = _index(slugs=("other-slug",))
    item = "todo:boot-fix — next action: still open"
    assert is_resolved(item, idx) is False
    out = reconcile_open_items([item], index=idx, omit_resolved=True)
    assert out == [item]


def test_omit_vs_tag_modes() -> None:
    idx = _index(slugs=("done-one",))
    item = "[todo:done-one] close the loop"
    tagged = reconcile_open_items([item], index=idx, omit_resolved=False)
    omitted = reconcile_open_items([item], index=idx, omit_resolved=True)
    assert tagged == [f"[RESOLVED] {item}"]
    assert omitted == []


def test_non_todo_item_untouched() -> None:
    idx = _index(slugs=("irrelevant",))
    item = "Review the Chase escrow statement for April"
    assert is_resolved(item, idx) is False
    out = reconcile_open_items([item], index=idx, omit_resolved=True)
    assert out == [item]


def test_bare_prefix_only_leading() -> None:
    idx = _index(slugs=("mid-hit",))
    item = "Discuss todo:mid-hit in the meeting notes (not a leading ref)"
    assert is_resolved(item, idx) is False
