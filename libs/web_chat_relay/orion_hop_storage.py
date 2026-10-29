"""Durable Orion hop receipts and wake-target persistence (fcntl-serialised JSON)."""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from durable_io.atomic import path_flock

_DEFAULT_ROOT = Path(os.environ.get("ULG_STATE_DIR", Path.home() / ".gateway")) / "orion"
_RECEIPTS_NAME = "wake_receipts.json"
_WAKE_TARGET_NAME = "wake_target_url.json"
_PENDING_ATTEMPT_NAME = "pending_hop_attempt.json"
_INFLIGHT_TTL_S = 120.0


def default_orion_state_dir() -> Path:
    return _DEFAULT_ROOT


@dataclass(frozen=True)
class OrionHopAttempt:
    """Immutable hop attempt bound to registration and smoke receipts."""

    attempt_id: str
    identity: str
    expected_old_url: str
    intended_new_url: str
    handoff_read_sha256: str

    def normalized_old(self) -> str:
        return self.expected_old_url.split("?", 1)[0]

    def normalized_new(self) -> str:
        return self.intended_new_url.split("?", 1)[0]

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any] | None) -> OrionHopAttempt | None:
        if not raw:
            return None
        attempt_id = str(raw.get("attempt_id") or "").strip()
        identity = str(raw.get("identity") or "").strip()
        old_u = str(raw.get("expected_old_url") or "").strip()
        new_u = str(raw.get("intended_new_url") or "").strip()
        sha = str(raw.get("handoff_read_sha256") or "").strip().lower()
        if not attempt_id or not identity or not old_u or not new_u or len(sha) != 64:
            return None
        return OrionHopAttempt(
            attempt_id=attempt_id,
            identity=identity,
            expected_old_url=old_u,
            intended_new_url=new_u,
            handoff_read_sha256=sha,
        )


def new_attempt_id() -> str:
    return secrets.token_hex(16)


class OrionHopStorage:
    """File-backed hop attempt, wake target, and delivery receipt store."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_orion_state_dir()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.receipts_path = self.root / _RECEIPTS_NAME
        self.wake_target_path = self.root / _WAKE_TARGET_NAME
        self.pending_attempt_path = self.root / _PENDING_ATTEMPT_NAME

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        with path_flock(path):
            path.write_text(text, encoding="utf-8")

    def save_pending_attempt(self, attempt: OrionHopAttempt) -> None:
        self._write_json(self.pending_attempt_path, attempt.to_dict())

    def load_pending_attempt(self) -> OrionHopAttempt | None:
        return OrionHopAttempt.from_dict(self._read_json(self.pending_attempt_path))

    def clear_pending_attempt(self, attempt_id: str) -> None:
        with path_flock(self.pending_attempt_path):
            current = self._read_json(self.pending_attempt_path)
            if str(current.get("attempt_id") or "") != attempt_id:
                return
            self.pending_attempt_path.unlink(missing_ok=True)

    def read_wake_target_url(self, *, default: str) -> str:
        raw = self._read_json(self.wake_target_path)
        url = str(raw.get("url") or "").strip()
        return url.split("?", 1)[0] if url else default.split("?", 1)[0]

    def compare_and_set_wake_target(
        self,
        *,
        expected_old: str,
        new_url: str,
    ) -> dict[str, Any]:
        expected = expected_old.split("?", 1)[0]
        candidate = new_url.split("?", 1)[0]
        with path_flock(self.wake_target_path):
            current_raw = self._read_json(self.wake_target_path)
            current = str(current_raw.get("url") or "").strip().split("?", 1)[0]
            if not current:
                current = expected
            if current != expected:
                return {
                    "ok": False,
                    "reason": "wake_target_cas_conflict",
                    "expected_old_url": expected,
                    "current_wake_target_url": current,
                }
            payload = {"url": candidate, "updated_at": time.time()}
            text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
            self.wake_target_path.write_text(text, encoding="utf-8")
            read_back = self._read_json(self.wake_target_path)
            committed = str(read_back.get("url") or "").split("?", 1)[0]
            if committed != candidate:
                return {
                    "ok": False,
                    "reason": "wake_target_readback_mismatch",
                    "expected_new_url": candidate,
                    "read_back_url": committed,
                }
            return {
                "ok": True,
                "reason": "wake_target_committed",
                "previous_wake_target_url": current,
                "wake_target_url": committed,
            }

    @staticmethod
    def _receipt_key(
        *,
        thread: str,
        turn: int | str,
        endpoint_url: str,
        kind: str,
    ) -> str:
        endpoint = endpoint_url.split("?", 1)[0]
        return f"{thread}|{turn}|{endpoint}|{kind}"

    def claim_delivery(
        self,
        *,
        thread: str,
        turn: int | str,
        endpoint_url: str,
        kind: str = "pointer_wake",
    ) -> tuple[str, dict[str, Any] | None]:
        """Return (claim_status, first_receipt). status ∈ claim|duplicate|in_flight."""
        key = self._receipt_key(
            thread=thread, turn=turn, endpoint_url=endpoint_url, kind=kind
        )
        now = time.time()
        with path_flock(self.receipts_path):
            store = self._read_json(self.receipts_path)
            row = store.get(key)
            if isinstance(row, dict):
                state = str(row.get("state") or "")
                if state == "completed":
                    return "duplicate", dict(row.get("receipt") or {})
                if state == "in_flight":
                    started = float(row.get("started_at") or 0)
                    if now - started < _INFLIGHT_TTL_S:
                        return "in_flight", None
                    row = {
                        **row,
                        "state": "ambiguous",
                        "reason": "in_flight_expired",
                    }
                    store[key] = row
            store[key] = {
                "state": "in_flight",
                "started_at": now,
                "thread": str(thread),
                "turn": str(turn),
                "endpoint_url": endpoint_url.split("?", 1)[0],
                "kind": kind,
            }
            text = json.dumps(store, sort_keys=True, indent=2) + "\n"
            self.receipts_path.write_text(text, encoding="utf-8")
            return "claim", None

    def finish_delivery(
        self,
        *,
        thread: str,
        turn: int | str,
        endpoint_url: str,
        kind: str,
        receipt: dict[str, Any],
        ambiguous: bool = False,
    ) -> None:
        key = self._receipt_key(
            thread=thread, turn=turn, endpoint_url=endpoint_url, kind=kind
        )
        state = "ambiguous" if ambiguous else "completed"
        with path_flock(self.receipts_path):
            store = self._read_json(self.receipts_path)
            store[key] = {
                "state": state,
                "finished_at": time.time(),
                "receipt": receipt,
                "thread": str(thread),
                "turn": str(turn),
                "endpoint_url": endpoint_url.split("?", 1)[0],
                "kind": kind,
            }
            text = json.dumps(store, sort_keys=True, indent=2) + "\n"
            self.receipts_path.write_text(text, encoding="utf-8")

    def save_smoke_receipt(self, attempt_id: str, receipt: dict[str, Any]) -> None:
        path = self.root / f"smoke_{attempt_id}.json"
        self._write_json(path, receipt)

    def load_smoke_receipt(self, attempt_id: str) -> dict[str, Any] | None:
        path = self.root / f"smoke_{attempt_id}.json"
        raw = self._read_json(path)
        return raw if raw else None
