"""Receipt status taxonomy for headless email export."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

ReceiptStatus = Literal[
    "fetched",
    "already_present",
    "deduped",
    "auth_failed",
    "not_found",
    "partial_thread",
    "imprint_failed",
]


@dataclass(frozen=True, slots=True)
class ReceiptEntry:
    selector_kind: str
    selector_value: str
    status: ReceiptStatus
    sink_path: str | None = None
    content_hash: str | None = None
    error: str | None = None
    imprint_status: str | None = None
    entity_id: str | None = None
    # moved | already_archived | archive_failed | None (skipped)
    archive_status: str | None = None
    archive_folder: str | None = None


@dataclass(slots=True)
class ExportReceipt:
    idempotency_key: str
    matter_id: str
    account: str
    fetch_path: str
    entries: list[ReceiptEntry] = field(default_factory=list)
    dry_run: bool = False

    def add(self, entry: ReceiptEntry) -> None:
        self.entries.append(entry)

    def to_dict(self) -> dict[str, object]:
        return {
            "idempotency_key": self.idempotency_key,
            "matter_id": self.matter_id,
            "account": self.account,
            "fetch_path": self.fetch_path,
            "dry_run": self.dry_run,
            "entries": [asdict(entry) for entry in self.entries],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def exit_code(self) -> int:
        if any(entry.status == "auth_failed" for entry in self.entries):
            return 1
        if any(entry.status == "not_found" for entry in self.entries):
            return 1
        if any(entry.status == "imprint_failed" for entry in self.entries):
            return 1
        if any(entry.imprint_status == "imprint_failed" for entry in self.entries):
            return 1
        if any(entry.archive_status == "archive_failed" for entry in self.entries):
            return 1
        return 0
