"""CloseoutMemoRequest — structured fields only, never closeout prose."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MEMO_KEY_RE = re.compile(r"^(giw|cdp|overdue):[A-Za-z0-9._:-]{1,120}$")
THREAD_RE = re.compile(r"^\d{1,10}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
SIDECAR_RE = re.compile(r"^(cortex|workspaces)://[A-Za-z0-9._/#:-]{1,240}$")
NEXT_RE = re.compile(
    r"^(none|hop_admitted:[A-Za-z0-9._:-]{1,80}|hop_skipped:[a-z_]{1,40}|resume:[A-Za-z0-9._:-]{1,80})$"
)
CONTRACT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

Kind = Literal[
    "sdk_closeout",
    "sdk_failed",
    "sdk_parked",
    "sdk_discarded",
    "conductor_stop",
    "conductor_parked",
    "cdp_reply",
    "cdp_unverified",
    "cdp_failed",
    "overdue",
]
Status = Literal[
    "completed",
    "partial",
    "failed",
    "refused",
    "delivery_failed",
    "parked",
    "discarded",
    "timeout",
    "stalled",
    "unverified",
    "overdue",
]

_STATUS_ALIAS = {
    "complete": "completed",
    "ok": "completed",
    "cancelled": "discarded",
    "canceled": "discarded",
}


def memo_id_for(memo_key: str) -> str:
    """Stable 16-hex id. Same key always yields the same id."""
    return hashlib.sha256(memo_key.encode("utf-8")).hexdigest()[:16]


class CloseoutMemoRequest(BaseModel):
    """Typed emit body. ``extra=forbid`` rejects closeout prose fields."""

    model_config = ConfigDict(extra="forbid")

    memo_v: Literal[1] = 1
    memo_key: str
    memo_id: str = ""
    kind: Kind
    status: Status
    contract: str = "unknown"
    wake_lane: str
    worker_thread: str
    dispatch_thread: str
    turn: int | Literal["latest"] = "latest"
    dispatch_id: str
    execution_id: str | None = None
    sidecar: str | None = None
    next: str = "none"
    emitted_at: str
    rejected_fields: list[str] = Field(default_factory=list)

    @field_validator("memo_key")
    @classmethod
    def _memo_key(cls, value: str) -> str:
        if not MEMO_KEY_RE.fullmatch(value):
            raise ValueError("memo_key does not match the closeout memo pattern")
        return value

    @field_validator("wake_lane", "worker_thread", "dispatch_thread")
    @classmethod
    def _thread(cls, value: str) -> str:
        if not THREAD_RE.fullmatch(value):
            raise ValueError("thread id must match ^\\d{1,10}$")
        return value

    @field_validator("dispatch_id")
    @classmethod
    def _dispatch_id(cls, value: str) -> str:
        if not ID_RE.fullmatch(value):
            raise ValueError("dispatch_id does not match the id pattern")
        return value

    @field_validator("execution_id")
    @classmethod
    def _execution_id(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not ID_RE.fullmatch(value):
            raise ValueError("execution_id does not match the id pattern")
        return value

    @field_validator("turn")
    @classmethod
    def _turn(cls, value: int | str) -> int | str:
        if value == "latest":
            return value
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("turn must be an int >= 1 or 'latest'")
        return value

    @field_validator("emitted_at")
    @classmethod
    def _emitted_at(cls, value: str) -> str:
        if not ISO_RE.fullmatch(value):
            raise ValueError("emitted_at must be ISO-8601 UTC (YYYY-MM-DDTHH:MM:SSZ)")
        return value

    def model_post_init(self, _context: object) -> None:
        if not self.memo_id:
            object.__setattr__(self, "memo_id", memo_id_for(self.memo_key))
        elif self.memo_id != memo_id_for(self.memo_key):
            raise ValueError("memo_id must be sha256(memo_key)[:16]")
        rejected = list(self.rejected_fields)
        if not CONTRACT_RE.fullmatch(self.contract):
            rejected.append("contract")
            object.__setattr__(self, "contract", "unknown")
        if self.sidecar is not None and not SIDECAR_RE.fullmatch(self.sidecar):
            rejected.append("sidecar")
            object.__setattr__(self, "sidecar", None)
        if not NEXT_RE.fullmatch(self.next):
            rejected.append("next")
            object.__setattr__(self, "next", "none")
        if rejected:
            object.__setattr__(self, "rejected_fields", rejected)


def coerce_status(raw: str | None) -> Status:
    """Map a producer status token onto the memo status enum."""
    token = str(raw or "").strip().lower()
    token = _STATUS_ALIAS.get(token, token)
    allowed: set[str] = {
        "completed",
        "partial",
        "failed",
        "refused",
        "delivery_failed",
        "parked",
        "discarded",
        "timeout",
        "stalled",
        "unverified",
        "overdue",
    }
    if token in allowed:
        return token  # type: ignore[return-value]
    return "completed"
