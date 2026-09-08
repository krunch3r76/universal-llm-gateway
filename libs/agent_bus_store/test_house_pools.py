"""Tests for continuity house ``## Pools`` parsing and projections."""

from __future__ import annotations

import hashlib

import pytest

from agent_bus_store.house_pools import (
    POOL_COLUMNS,
    PoolsParseError,
    apply_fable_house_staging,
    conductor_pool_admit_refusal,
    extract_pools_block,
    extract_pools_residue_sha8,
    format_house_read_first_block,
    inject_pools_checkpoint_projection,
    parse_pools,
    pool_status_is_blocked,
    pool_status_is_open,
    pools_block_sha256,
    pools_residue_matches_card,
    pools_residue_token,
    resolve_house_thread_id,
)

_MANIFEST_BLOCK = """<!-- pools v1 -->
## Pools
| pool | executor | status | must_load | must_read | closeout | forbidden |
|---|---|---|---|---|---|---|
| orchestrator | IDE cursor tab bound to this lane | open | ulg-for-llms · checkpoint-discipline · continuity-thread-shaping · agent-bus-discipline | tip CP · this card · 10223-projection.md · 10223-opportunities.md | CHECKPOINT on agent-bus:10223 | implement in this tab · linear thread read · burying pending in chat |
| work | Composer WORK tab (operator opens) | open | abstraction-layering · architecture-invariants · docstring-quality | launch prompt · opportunities row · cited spec | CLOSEOUT on agent-bus:10303 (`commit · pytest · files · verdict · bus_posts`) | posts on agent-bus:10223 · judgment forks (RULING ⇒ escalate) · scope beyond the slice |
| fable | cdp/fable (escalation=cdp/fable) + watcher armed with --execution-id | open | ulg-for-llms · reasoning-posture · hypothesize-simulate · architecture-invariants(inline) · ulg-architecture(inline) | this card · tip CP · 10223-opportunities.md · cited specs | sidecar + send reply on the request lane (proof_reply_from web-anthropic) | code diffs (write boundary) · editing this card · polling for its own harvest |
| conductor | cursor-sdk (team_dispatch seat=cursor-sdk) | blocked · bridge Timeout TypeError · since 2026-09-08 | conductor · reasoning-posture (non-mechanical) | this card · tip CP (continuity preamble) | worker thread CLOSEOUT + witness | admit while status≠open · G-row skip |
"""


def _card_with_block(extra: str = "") -> str:
    return f"# card\n\n{_MANIFEST_BLOCK}\n{extra}"


def _card_with_manifest(manifest: str) -> str:
    return f"# card\n\n{manifest}\n"


def test_parse_pools_round_trips_manifest_block() -> None:
    rows = parse_pools(_card_with_block())
    assert set(rows) == {"orchestrator", "work", "fable", "conductor"}
    work = rows["work"]
    assert work.must_load == (
        "abstraction-layering",
        "architecture-invariants",
        "docstring-quality",
    )
    assert "10303" in work.closeout


def test_parse_pools_unknown_column_raises() -> None:
    lines = _MANIFEST_BLOCK.splitlines()
    header = lines[2].replace("| forbidden |", "| extra_col |")
    bad = "\n".join(lines[:2] + [header] + lines[3:])
    with pytest.raises(PoolsParseError) as exc:
        parse_pools(_card_with_manifest(bad))
    assert exc.value.cell == "header"


def test_parse_pools_invalid_status_raises() -> None:
    bad = _MANIFEST_BLOCK.replace(
        "| orchestrator | IDE cursor tab bound to this lane | open |",
        "| orchestrator | IDE cursor tab bound to this lane | maybe |",
        1,
    )
    with pytest.raises(PoolsParseError) as exc:
        parse_pools(_card_with_manifest(bad))
    assert exc.value.cell == "status@orchestrator"


def test_pools_block_sha_and_residue_token() -> None:
    card = _card_with_block()
    block = extract_pools_block(card)
    assert block is not None
    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
    assert pools_block_sha256(card) == digest
    assert pools_residue_token(card) == f"Pools: {digest[:8]}"


def test_pools_residue_matches_card() -> None:
    card = _card_with_block()
    token = pools_residue_token(card)
    assert token is not None
    residue = f"Fold done · {token}"
    assert pools_residue_matches_card(residue, card)
    assert extract_pools_residue_sha8(residue) == token.removeprefix("Pools: ")


def test_pool_status_helpers() -> None:
    assert pool_status_is_open("open")
    assert not pool_status_is_open("serial · 1")
    assert pool_status_is_blocked("blocked · bridge Timeout TypeError · since 2026-09-08")


def test_inject_pools_checkpoint_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card_path = tmp_path / "notes/system/threads/10223-continuity.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(_card_with_block(), encoding="utf-8")
    digest = pools_block_sha256(_card_with_block())
    assert digest is not None
    body = "### Artifact anchors\n_none cited_\n\n## Residue"
    projected = inject_pools_checkpoint_projection(body, "10223")
    assert f"- Pools block · sha256:{digest}" in projected


def test_format_house_read_first_block() -> None:
    row = parse_pools(_card_with_block())["fable"]
    block = format_house_read_first_block(house_id="10223", row=row)
    assert block.startswith("## House (read first)")
    assert "cortex://notes/system/threads/10223-continuity.md" in block
    assert "proof_reply_from web-anthropic" in block


def test_apply_fable_house_staging_merges_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card_path = tmp_path / "notes/system/threads/10223-continuity.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(_card_with_block(), encoding="utf-8")
    body, skills = apply_fable_house_staging(
        "DIRECTIVE body",
        house_id="10223",
        skills=["consult-posture"],
    )
    assert body.startswith("## House (read first)")
    assert "DIRECTIVE body" in body
    assert skills[0] == "ulg-for-llms"
    assert "consult-posture" in skills


def test_apply_fable_house_staging_blocks_when_status_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    blocked = _MANIFEST_BLOCK.replace(
        "| open | ulg-for-llms · reasoning-posture",
        "| blocked · test · since 2026-09-08 | ulg-for-llms · reasoning-posture",
        1,
    )
    card_path = tmp_path / "notes/system/threads/10223-continuity.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(f"# card\n\n{blocked}", encoding="utf-8")
    with pytest.raises(PoolsParseError):
        apply_fable_house_staging("body", house_id="10223", skills=None)


def test_conductor_pool_admit_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card_path = tmp_path / "notes/system/threads/10223-continuity.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(_card_with_block(), encoding="utf-8")
    reason = conductor_pool_admit_refusal("10223")
    assert reason is not None
    assert reason.startswith("blocked")


def test_resolve_house_thread_id_from_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    assert (
        resolve_house_thread_id(None, context_text="house agent-bus:**10223**")
        is None
    )
    card_path = tmp_path / "notes/system/threads/10223-continuity.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(_card_with_block(), encoding="utf-8")
    assert resolve_house_thread_id(
        None, context_text="house agent-bus:**10223**"
    ) == "10223"


def test_pool_columns_constant() -> None:
    assert len(POOL_COLUMNS) == 7
