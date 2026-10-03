"""Insert point for assistantSourceText (a:37508)."""

from __future__ import annotations

import pytest

from chat_harvest.assistant_source_text import (
    ASSISTANT_SOURCE_TEXT_JS,
    with_assistant_source_text,
)

pytestmark = pytest.mark.offline


def test_with_assistant_source_text_skips_param_object_brace() -> None:
    raw = "({ minMsgChars }) => {\n  const t = assistantSourceText(el);\n}\n"
    out = with_assistant_source_text(raw)
    assert out.index("function assistantSourceText") > out.index("=> {")
    assert ASSISTANT_SOURCE_TEXT_JS in out
    assert out.count("function assistantSourceText") == 1
