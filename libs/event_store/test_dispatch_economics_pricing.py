"""Unit tests for dispatch-economics-dollar-equivalents."""

from __future__ import annotations

import ast
from pathlib import Path

from dataclasses import dataclass

import pytest

from event_store.dispatch_economics_pricing import (
    _build_pricing_audit,
    _index_wire_members,
    _price_row,
    wire_usd_present,
)
from event_store.dispatch_economics_rollup import build_dispatch_economics_rollup
from event_store import model_rate_table as mrt
from event_store.model_rate_table import (
    catalog_yaml_path,
    clear_catalog_rows_for_tests,
    load_catalog_rows_from_disk,
    load_manual_rows,
    resolve_rate,
    save_catalog_rows_to_disk,
    upsert_catalog_models,
)
from event_store.operation_catalog import get_operation, list_operations
from event_store.operation_dispatch import _DISPATCH, execute_operation
from event_store.store import EventStore


@dataclass
class _CatalogModelStub:
    id: str
    provider: str
    max_concurrent: int
    prompt_cost_per_m: float
    completion_cost_per_m: float


def _event_row(
    *,
    seq: int,
    signal: str,
    payload: dict,
    execution_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    return {
        "seq": seq,
        "signal": signal,
        "execution_id": execution_id,
        "request_id": request_id,
        "ts_unix_ms": 1_700_000_000_000 + seq,
        "payload": payload,
    }


@pytest.fixture(scope="module", autouse=True)
def _hermetic_catalog_path(tmp_path_factory: pytest.TempPathFactory):
    """Redirect catalog I/O away from the repo bootstrap yaml (A4)."""
    catalog = tmp_path_factory.mktemp("rates") / "model_rates_catalog.yaml"
    original = mrt._CATALOG_PATH
    mrt._CATALOG_PATH = catalog
    clear_catalog_rows_for_tests()
    yield catalog
    mrt._CATALOG_PATH = original
    clear_catalog_rows_for_tests()


@pytest.fixture(autouse=True)
def _clear_catalog_rows() -> None:
    clear_catalog_rows_for_tests()
    yield
    clear_catalog_rows_for_tests()


def test_seed_yaml_non_empty() -> None:
    rows, aliases = load_manual_rows()
    assert rows
    assert "cursor/composer-2.5" in rows


def test_resolve_rate_alias_and_resolved_model() -> None:
    row = resolve_rate("composer-2.5")
    assert row is not None
    assert row.model_id == "cursor/composer-2.5"
    assert resolve_rate(None, resolved_model="cursor/composer-2.5") is not None


def test_catalog_upsert_counts_and_negative_rejection() -> None:
    counts = upsert_catalog_models(
        [
            _CatalogModelStub(
                id="anthropic/claude-sonnet-4",
                provider="openrouter",
                max_concurrent=1,
                prompt_cost_per_m=3.0,
                completion_cost_per_m=15.0,
            ),
            _CatalogModelStub(
                id="bad/negative",
                provider="openrouter",
                max_concurrent=1,
                prompt_cost_per_m=-1.0,
                completion_cost_per_m=1.0,
            ),
        ]
    )
    assert counts["upserted"] == 1
    assert counts["rejected_negative"] == 1
    assert counts["rejected_zero_rate"] == 0
    assert resolve_rate("anthropic/claude-sonnet-4") is not None
    assert catalog_yaml_path().is_file()
    assert catalog_yaml_path().name == "model_rates_catalog.yaml"
    # Hermetic: never the host gateway catalog path.
    assert ".gateway/model_rates_catalog.yaml" not in str(catalog_yaml_path())
    assert "data/model_rates_catalog.yaml" not in str(catalog_yaml_path())


def test_catalog_upsert_rejects_zero_rates() -> None:
    counts = upsert_catalog_models(
        [
            _CatalogModelStub(
                id="prov/zero",
                provider="anthropic",
                max_concurrent=1,
                prompt_cost_per_m=0.0,
                completion_cost_per_m=0.0,
            ),
            _CatalogModelStub(
                id="prov/ok",
                provider="openrouter",
                max_concurrent=1,
                prompt_cost_per_m=1.0,
                completion_cost_per_m=2.0,
            ),
        ]
    )
    assert counts["rejected_zero_rate"] == 1
    assert counts["upserted"] == 1
    assert resolve_rate("prov/zero") is None
    assert resolve_rate("prov/ok") is not None
    disk = load_catalog_rows_from_disk()
    assert "prov/zero" not in disk


def test_catalog_upsert_dual_keys_openrouter_bare_id() -> None:
    counts = upsert_catalog_models(
        [
            _CatalogModelStub(
                id="openrouter/anthropic/claude-test",
                provider="openrouter",
                max_concurrent=1,
                prompt_cost_per_m=5.0,
                completion_cost_per_m=25.0,
            ),
        ]
    )
    assert counts["upserted"] == 2
    bare = resolve_rate("anthropic/claude-test")
    assert bare is not None
    assert bare.input_rate_per_m == 5.0
    assert bare.output_rate_per_m == 25.0


def test_catalog_disk_roundtrip(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    save_catalog_rows_to_disk(
        {
            "openai/gpt-4o-mini": {
                "model_id": "openai/gpt-4o-mini",
                "input_rate_per_m": 0.15,
                "output_rate_per_m": 0.6,
                "source": "catalog_refresh",
                "updated_at": "2026-07-20T00:00:00+00:00",
                "pinned": False,
            }
        },
        path=catalog_path,
    )
    rows = load_catalog_rows_from_disk(path=catalog_path)
    assert rows["openai/gpt-4o-mini"].output_rate_per_m == 0.6


def test_virtual_pipeline_aliases_resolve() -> None:
    assert resolve_rate("predicate-extract") is not None
    assert resolve_rate("rag-contextualize") is not None
    assert resolve_rate("todo-close") is not None


def test_fo1_bare_provider_aliases_resolve_nonzero() -> None:
    """FO1: rollup bare ids alias to priced openrouter catalog keys."""
    # Seed hermetic catalog with the openrouter targets the aliases point at.
    save_catalog_rows_to_disk(
        {
            "openrouter/anthropic/claude-opus-4.8": {
                "model_id": "openrouter/anthropic/claude-opus-4.8",
                "input_rate_per_m": 5.0,
                "output_rate_per_m": 25.0,
                "source": "catalog_refresh",
                "updated_at": "2026-07-20T00:00:00+00:00",
                "pinned": False,
            },
            "openrouter/openai/gpt-5.4-mini": {
                "model_id": "openrouter/openai/gpt-5.4-mini",
                "input_rate_per_m": 0.75,
                "output_rate_per_m": 4.5,
                "source": "catalog_refresh",
                "updated_at": "2026-07-20T00:00:00+00:00",
                "pinned": False,
            },
        }
    )
    clear_catalog_rows_for_tests()
    opus = resolve_rate("anthropic/claude-opus-4-8")
    assert opus is not None
    assert opus.input_rate_per_m > 0
    assert opus.output_rate_per_m > 0
    gpt = resolve_rate("openai/gpt-5.4-mini-2026-03-17")
    assert gpt is not None
    assert gpt.input_rate_per_m > 0
    assert gpt.output_rate_per_m > 0


def test_pricing_audit_excludes_cdp_from_unavailable_rate() -> None:
    audit = _build_pricing_audit(
        [
            {"substrate": "cursor-sdk", "cost_source": "rate_x_tokens"},
            {"substrate": "web-anthropic-cdp", "cost_source": "unavailable"},
            {"substrate": "stargate-snapshot", "cost_source": "unavailable"},
        ]
    )
    assert audit["cdp_stub_count"] == 1
    assert audit["unavailable_rate"] == 0.5
    assert audit["unavailable_rate_all_rows"] == pytest.approx(2 / 3)


def test_wire_usd_placeholder_zero_without_captured_is_absent() -> None:
    amount, key = wire_usd_present({"cost_usd": 0.0}, "missing")
    assert amount is None
    assert key is None


def test_wire_usd_zero_with_captured_is_authoritative() -> None:
    amount, key = wire_usd_present({"cost_usd": 0.0}, "captured")
    assert amount == 0.0
    assert key == "cost_usd"


def test_credits_not_treated_as_wire_usd() -> None:
    amount, key = wire_usd_present({"credits": 12.5}, "captured")
    assert amount is None
    assert key is None


def test_multi_member_sdk_wire_not_dropped_when_pipeline_wins_tokens() -> None:
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="shared-eid",
        payload={
            "execution_id": "shared-eid",
            "resolved_model": "cursor/composer-2.5",
            "usage_capture_status": "captured",
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cost_usd": 2.5,
            },
        },
    )
    pipeline = _event_row(
        seq=2,
        signal="pipeline.frontier.dispatch.completed",
        execution_id="shared-eid",
        payload={
            "execution_id": "shared-eid",
            "prompt_tokens": 999_999,
            "completion_tokens": 0,
        },
    )
    wire_index = _index_wire_members([sdk], [], [pipeline])
    g2 = build_dispatch_economics_rollup(
        sdk_rows=[sdk],
        snapshot_rows=[],
        pipeline_rows=[pipeline],
        cdp_stubs=[],
    )
    priced = _price_row(g2["rows"][0], wire_index.get("shared-eid"))
    assert priced["cost_usd"] == 2.5
    assert priced["cost_source"] == "wire"


def test_rate_x_tokens_includes_cache_when_rates_present() -> None:
    """Cache tiers contribute when the rate row has cache_* rates (Composer: read only)."""
    row = {
        "model_id": "cursor/composer-2.5",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "cache_read_tokens": 500_000,
    }
    priced = _price_row(row, None)
    assert priced["cost_source"] == "rate_x_tokens"
    # 1.0*0.5 + 0.5*0.2 + 1.0*2.5 = 0.5 + 0.1 + 2.5 = 3.1
    assert priced["cost_usd"] == pytest.approx(3.1)
    assert priced["cache_read_rate_per_m"] == 0.2
    assert priced["cache_write_rate_per_m"] is None


def test_opus_fixture_four_term_estimate_matches_cursor_billing() -> None:
    """Friction 25728: Opus 4.8 fixture → ~$57 (not ~$152 prompt+completion-only)."""
    row = {
        "model_id": "cursor/claude-opus-4-8",
        "prompt_tokens": 9_840_000,
        "completion_tokens": 57_000,
        "cache_read_tokens": 9_640_000,
        "cache_write_tokens": 203_000,
    }
    priced = _price_row(row, None)
    assert priced["cost_source"] == "rate_x_tokens"
    # 9.84*5 + 0.203*6.25 + 9.64*0.5 + 0.057*25 = 49.2 + 1.26875 + 4.82 + 1.425
    assert priced["cost_usd"] == pytest.approx(56.71375, abs=0.05)
    assert priced["input_rate_per_m"] == 5.0
    assert priced["cache_write_rate_per_m"] == 6.25
    assert priced["cache_read_rate_per_m"] == 0.5
    assert priced["output_rate_per_m"] == 25.0


def test_unavailable_when_no_rate_and_no_wire() -> None:
    row = {
        "model_id": "unknown/model",
        "prompt_tokens": 100,
        "completion_tokens": 50,
    }
    priced = _price_row(row, None)
    assert priced["cost_usd"] is None
    assert priced["cost_source"] == "unavailable"


def test_catalog_zero_rate_prices_as_unavailable() -> None:
    """A1: catalog_refresh 0/0 → unavailable (not $0), rates still audited."""
    save_catalog_rows_to_disk(
        {
            "prov/zero-catalog": {
                "model_id": "prov/zero-catalog",
                "input_rate_per_m": 0.0,
                "output_rate_per_m": 0.0,
                "source": "catalog_refresh",
                "updated_at": "2026-07-20T00:00:00+00:00",
                "pinned": False,
            }
        }
    )
    clear_catalog_rows_for_tests()
    priced = _price_row(
        {
            "model_id": "prov/zero-catalog",
            "prompt_tokens": 1_000_000,
            "completion_tokens": 0,
        },
        None,
    )
    assert priced["cost_usd"] is None
    assert priced["cost_source"] == "unavailable"
    assert priced["input_rate_per_m"] == 0.0
    assert priced["output_rate_per_m"] == 0.0
    assert priced["rate_source"] == "catalog_refresh"
    audit = _build_pricing_audit([priced])
    assert audit["unavailable_count"] == 1


def test_manual_seed_local_zero_prices_as_rate_x_tokens() -> None:
    """A1: intentional local zeros remain authoritative $0 via rate_x_tokens."""
    priced = _price_row(
        {
            "model_id": "local/zero-cost",
            "prompt_tokens": 1_000_000,
            "completion_tokens": 500_000,
        },
        None,
    )
    assert priced["cost_source"] == "rate_x_tokens"
    assert priced["cost_usd"] == 0.0
    assert priced["rate_source"] == "manual_seed_local"


def test_placeholder_zero_falls_through_to_rate_x_tokens() -> None:
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="e1",
        payload={
            "execution_id": "e1",
            "resolved_model": "cursor/composer-2.5",
            "usage_capture_status": "missing",
            "usage": {"input_tokens": 1_000_000, "output_tokens": 0, "cost_usd": 0.0},
        },
    )
    wire_index = _index_wire_members([sdk], [], [])
    g2 = build_dispatch_economics_rollup(
        sdk_rows=[sdk], snapshot_rows=[], pipeline_rows=[], cdp_stubs=[]
    )
    priced = _price_row(g2["rows"][0], wire_index.get("e1"))
    assert priced["cost_source"] == "rate_x_tokens"
    assert priced["cost_usd"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_operation_registered_and_dispatched() -> None:
    names = {op["name"] for op in list_operations()}
    assert "dispatch-economics-dollar-equivalents" in names
    assert get_operation("dispatch-economics-dollar-equivalents") is not None
    assert "dispatch-economics-dollar-equivalents" in _DISPATCH

    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": "frontier.sdk.worker.completed",
                    "role": "observation",
                    "scope": "node",
                    "ts_unix_ms": 1_700_000_000_000,
                    "timestamp": "2026-07-20T00:00:00Z",
                    "source": "test",
                    "payload": {
                        "execution_id": "exec-live",
                        "resolved_model": "cursor/composer-2.5",
                        "usage_capture_status": "captured",
                        "usage": {"input_tokens": 1000, "output_tokens": 500},
                    },
                }
            ]
        )
        body = await execute_operation(
            "dispatch-economics-dollar-equivalents",
            {"since_ts": 0},
            store,
        )
        g2_body = await execute_operation(
            "dispatch-economics-token-rollup",
            {"since_ts": 0},
            store,
        )
    finally:
        await store.close()

    assert body["rows"]
    assert body["rows"][0]["cost_source"] == "rate_x_tokens"
    assert body["pricing_audit"]["rate_computed_count"] == 1
    assert "cost_usd" not in g2_body["rows"][0]


def test_g2_token_rollup_schema_unchanged() -> None:
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="e1",
        payload={
            "execution_id": "e1",
            "usage_capture_status": "captured",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )
    g2 = build_dispatch_economics_rollup(
        sdk_rows=[sdk], snapshot_rows=[], pipeline_rows=[], cdp_stubs=[]
    )
    row = g2["rows"][0]
    assert "cost_usd" not in row
    assert "cost_source" not in row


def test_mcp_events_allowlist_accepts_dollar_equivalents() -> None:
    events_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "mcp-server"
        / "tools"
        / "events.py"
    )
    tree = ast.parse(events_path.read_text(encoding="utf-8"))
    valid_operations: set[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_VALID_OPERATIONS":
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    if value.func.id == "frozenset" and value.args:
                        valid_operations = set(ast.literal_eval(value.args[0]))
                break
    assert isinstance(valid_operations, set)
    assert "dispatch-economics-dollar-equivalents" in valid_operations
