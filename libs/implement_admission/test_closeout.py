"""Tests for ImplementCloseout v1 and adapter registry."""

from __future__ import annotations

import pytest

from implement_admission.closeout import ADAPTERS, ImplementCloseout, run_adapters
from implement_admission.spec import (
    CloseoutAdapterKind,
    CloseoutStatus,
    Source,
    SourceKind,
)


def test_closeout_round_trip() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="done",
        source_ref="todo:foo",
    )
    restored = ImplementCloseout.model_validate(closeout.model_dump())
    assert restored.source_ref == "todo:foo"


def test_adapter_registry_has_all_kinds() -> None:
    for kind in CloseoutAdapterKind:
        assert kind.value in ADAPTERS


def test_stub_adapter_raises_not_implemented() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    with pytest.raises(NotImplementedError, match="phase 4"):
        run_adapters(closeout, source)
