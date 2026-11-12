"""Neutral spawn/house JSONL ledger — shared by Stargate admit, MCP relay, and bus wake."""

from spawn_ledger.spawn_endpoint_house_ledger import (
    LEDGER_FILENAME,
    format_ledger_prompt_prefix,
    ingest_report_turn,
    last_successful_wake,
    ledger_path,
    map_source_status,
    mint_row_id,
    patch_spawn_row,
    project_row_status,
    read_all_rows,
    read_house_ledger,
    record_cse_wake,
    record_report_row,
    record_spawn,
    record_status_row,
    record_tab_wake,
)

CONSUMERS: tuple[str, ...] = ("agent_bus", "mcp", "stargate")

__all__ = [
    "CONSUMERS",
    "LEDGER_FILENAME",
    "format_ledger_prompt_prefix",
    "ingest_report_turn",
    "last_successful_wake",
    "ledger_path",
    "map_source_status",
    "mint_row_id",
    "patch_spawn_row",
    "project_row_status",
    "read_all_rows",
    "read_house_ledger",
    "record_cse_wake",
    "record_report_row",
    "record_spawn",
    "record_status_row",
    "record_tab_wake",
]
