"""CI guard: MCP doc surfaces stay in sync with _OP_SPECS + handler signatures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cortex_store.dispatch_ops import (
    _DEPRECATED_PARAM_NAMES,
    _INTERNAL_PARAMS,
    _OP_SPECS,
)
from cortex_store.dispatch_ops._doc_gen import (
    _ALIAS_AMBIGUOUS,
    _CORTEX_PY,
    _TOOLS_PY,
    START_MARKER,
    apply_write,
    check_tree,
    generate_blocks,
)
from cortex_store.dispatch_ops._doc_gen_support import (
    build_op_docs,
    doc_required_names,
    expected_visible_params,
    parse_cortex_ops_from_source,
    parse_tool_definition_ops_from_source,
    validate_generated_blocks,
)
from cortex_store.dispatch_ops._doc_required_by_op import _DOC_REQUIRED_BY_OP
from cortex_store.dispatch_ops._session_close_doc_type import (
    _SESSION_CLOSE_REQUIRED_FIELDS,
)

_PKG_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.offline
def test_ac1_op_set_parity_cortex_py() -> None:
    text = _CORTEX_PY.read_text(encoding="utf-8")
    documented = set(parse_cortex_ops_from_source(text))
    assert documented == set(_OP_SPECS.keys())
    assert len(documented) == len(_OP_SPECS)


@pytest.mark.offline
def test_ac1_op_set_parity_cortex_tool_definition() -> None:
    text = _TOOLS_PY.read_text(encoding="utf-8")
    documented = set(parse_tool_definition_ops_from_source(text))
    assert documented == set(_OP_SPECS.keys())
    assert len(documented) == len(_OP_SPECS)


@pytest.mark.offline
def test_ac2_param_parity_all_ops() -> None:
    cortex_ops = parse_cortex_ops_from_source(_CORTEX_PY.read_text(encoding="utf-8"))
    tool_ops = parse_tool_definition_ops_from_source(_TOOLS_PY.read_text(encoding="utf-8"))
    drift: list[str] = []
    for op in _OP_SPECS:
        expected = expected_visible_params(op)
        if cortex_ops.get(op) != expected or tool_ops.get(op) != expected:
            drift.append(op)
    assert drift == []


@pytest.mark.offline
def test_ac2_param_parity_samples() -> None:
    cortex_ops = parse_cortex_ops_from_source(_CORTEX_PY.read_text(encoding="utf-8"))
    tool_ops = parse_tool_definition_ops_from_source(_TOOLS_PY.read_text(encoding="utf-8"))
    for op in ("entity_get", "assert", "session_close"):
        expected = expected_visible_params(op)
        assert cortex_ops[op] == expected
        assert tool_ops[op] == expected


def _op_param_tokens(op: str, text: str) -> list[str]:
    """Parse autogen param tokens for ``op`` (canonical line or alias annotation)."""
    import re

    # Word-boundary: avoid ``resolve`` matching inside ``deadline_resolve``.
    m = re.search(rf"(?<![\w]){re.escape(op)}\s+\(([^)]*)\)", text)
    if m:
        return [t.strip() for t in m.group(1).split(",") if t.strip()]
    # Alias ops share the canonical line: ``impact (...) (aliases: graph_reach)``.
    alias_re = re.compile(
        rf"\w+\s+\(([^)]*)\)\s+\(aliases:\s*[^)]*\b{re.escape(op)}\b"
    )
    m = alias_re.search(text)
    assert m is not None, f"{op} missing as primary op or alias"
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def _assert_required_params_not_optional(
    op: str, required: set[str], *, label: str, text: str
) -> None:
    tokens = _op_param_tokens(op, text)
    for field in required:
        assert field in tokens, (
            f"{label} {op}: {field} must be required (no ?); got {tokens[:8]}"
        )
        assert f"{field}?" not in tokens, (
            f"{label} {op}: {field}? falsely optional"
        )


@pytest.mark.offline
def test_session_close_required_params_not_marked_optional() -> None:
    """Friction 23129: descriptor must not advertise session_id? when validation requires it."""
    cortex_text = _CORTEX_PY.read_text(encoding="utf-8")
    tools_text = _TOOLS_PY.read_text(encoding="utf-8")
    required = set(_SESSION_CLOSE_REQUIRED_FIELDS)
    for label, text in (("cortex.py", cortex_text), ("tools.py", tools_text)):
        for op in ("session_close", "session_close_preflight"):
            _assert_required_params_not_optional(
                op, required, label=label, text=text
            )


@pytest.mark.offline
@pytest.mark.parametrize("op", sorted(_DOC_REQUIRED_BY_OP))
def test_doc_required_params_not_marked_optional(op: str) -> None:
    """Friction 23147: validation-required fields must not carry ? in autogen prose."""
    required = set(doc_required_names(op))
    assert required, f"{op} missing from _DOC_REQUIRED_BY_OP"
    cortex_text = _CORTEX_PY.read_text(encoding="utf-8")
    tools_text = _TOOLS_PY.read_text(encoding="utf-8")
    for label, text in (("cortex.py", cortex_text), ("tools.py", tools_text)):
        _assert_required_params_not_optional(op, required, label=label, text=text)


@pytest.mark.offline
def test_ac3_entity_get_intents_and_params() -> None:
    cortex_text = _CORTEX_PY.read_text(encoding="utf-8")
    tools_text = _TOOLS_PY.read_text(encoding="utf-8")
    for text in (cortex_text, tools_text):
        assert 'intent="body"' in text or "intent=\"body\"" in text or "body" in text
        assert "card-md" in text
    for ops in (
        parse_cortex_ops_from_source(cortex_text),
        parse_tool_definition_ops_from_source(tools_text),
    ):
        assert "section" in ops["entity_get"]
        assert "full_body" in ops["entity_get"]


@pytest.mark.offline
def test_ac4_alias_annotations() -> None:
    cortex_text = _CORTEX_PY.read_text(encoding="utf-8")
    tools_text = _TOOLS_PY.read_text(encoding="utf-8")
    assert "(aliases: graph_reach)" in cortex_text
    assert "(aliases: claim_alignment)" in cortex_text
    assert "(aliases: graph_reach)" in tools_text
    assert "(aliases: claim_alignment)" in tools_text
    assert "graph_reach" not in cortex_text.split("(aliases: graph_reach)")[0].split("\n")[-1]
    documented = parse_cortex_ops_from_source(cortex_text)
    assert "graph_reach" in documented
    assert "claim_alignment" in documented


@pytest.mark.offline
def test_ac5_internal_and_deprecated_params_hidden() -> None:
    cortex_ops = parse_cortex_ops_from_source(_CORTEX_PY.read_text(encoding="utf-8"))
    tool_ops = parse_tool_definition_ops_from_source(_TOOLS_PY.read_text(encoding="utf-8"))
    hidden = _INTERNAL_PARAMS | _DEPRECATED_PARAM_NAMES
    for op in _OP_SPECS:
        for surface, ops in (("cortex", cortex_ops), ("tools", tool_ops)):
            leaked = hidden & ops.get(op, set())
            assert leaked == set(), f"{op} on {surface} leaked hidden params {leaked}"


@pytest.mark.offline
def test_ac6_sentinel_regions_present() -> None:
    for path in (_CORTEX_PY, _TOOLS_PY):
        text = path.read_text(encoding="utf-8")
        assert START_MARKER in text
        assert "# <<< AUTOGEN:cortex-ops <<<" in text


@pytest.mark.offline
def test_ac7_regen_idempotent_check_passes() -> None:
    assert check_tree() is True


@pytest.mark.offline
def test_ac7_regen_check_fails_after_manual_edit(tmp_path: Path) -> None:
    cortex_text = _CORTEX_PY.read_text(encoding="utf-8")
    start = cortex_text.index(START_MARKER)
    end = cortex_text.index("# <<< AUTOGEN:cortex-ops <<<")
    corrupted = (
        cortex_text[: start + len(START_MARKER)]
        + "\n                  tampered () — edited\n"
        + cortex_text[end:]
    )
    fake = tmp_path / "cortex.py"
    fake.write_text(corrupted, encoding="utf-8")
    with patch("cortex_store.dispatch_ops._doc_gen._CORTEX_PY", fake):
        assert check_tree() is False


@pytest.mark.offline
def test_ac9_fail_closed_write_on_validation_failure(tmp_path: Path) -> None:
    cortex_orig = _CORTEX_PY.read_bytes()
    tools_orig = _TOOLS_PY.read_bytes()
    fake_cortex = tmp_path / "cortex.py"
    fake_tools = tmp_path / "tools.py"
    fake_cortex.write_bytes(cortex_orig)
    fake_tools.write_bytes(tools_orig)

    def _boom(*_args: object, **_kwargs: object) -> tuple[str, str]:
        raise RuntimeError("harvest failed for synthetic op")

    with patch("cortex_store.dispatch_ops._doc_gen._CORTEX_PY", fake_cortex):
        with patch("cortex_store.dispatch_ops._doc_gen._TOOLS_PY", fake_tools):
            with patch(
                "cortex_store.dispatch_ops._doc_gen.generate_blocks",
                side_effect=_boom,
            ):
                with pytest.raises(RuntimeError, match="harvest failed"):
                    apply_write()
    assert fake_cortex.read_bytes() == cortex_orig
    assert fake_tools.read_bytes() == tools_orig


@pytest.mark.offline
def test_ac10_alias_ambiguity_halts_with_diagnostic() -> None:
    ambiguous = {
        "alpha": "ops_misc:_op_stats",
        "beta": "ops_misc:_op_stats",
    }
    with pytest.raises(RuntimeError, match=_ALIAS_AMBIGUOUS):
        build_op_docs(ambiguous)


@pytest.mark.offline
def test_generate_blocks_validates_op_count() -> None:
    cortex_block, tool_block = generate_blocks()
    validate_generated_blocks(
        cortex_block, tool_block, expected_ops=len(_OP_SPECS)
    )
