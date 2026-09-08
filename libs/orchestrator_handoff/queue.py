"""JSON registrar queue for orchestrator tab handoff (house 10223 default).

State: ``tmp/watchers/orchestrator-handoff-queue.json``

One consumer: heartbeat ``--emit-keystroke-launch`` or ``registrar dispatch-next``.
Completion: ``release`` on handoff lock (queue_id in lock) or ``registrar complete``.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from durable_io.atomic import durable_write_text

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_HOUSE = "10223"
QUEUE_VERSION = 1

ItemStatus = Literal["queued", "launching", "in_flight", "done", "failed", "cancelled"]

PRIORITY_RANK: dict[str, int] = {
    "P0": 0,
    "P1": 1,
    "P2": 2,
    "P3": 3,
    "opportunity": 4,
}


def default_queue_path(house: str = DEFAULT_HOUSE) -> Path:
    return _REPO / "tmp/watchers" / f"orchestrator-handoff-queue-{house}.json"


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _priority_rank(label: str) -> int:
    key = (label or "").strip().upper()
    if key in PRIORITY_RANK:
        return PRIORITY_RANK[key]
    m = re.match(r"P(\d+)", key)
    if m:
        return int(m.group(1))
    return 9


@dataclass
class HandoffQueue:
    house: str
    path: Path

    @classmethod
    def open(cls, house: str = DEFAULT_HOUSE, path: Path | None = None) -> HandoffQueue:
        return cls(house=house, path=path or default_queue_path(house))

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"house": self.house, "version": QUEUE_VERSION, "items": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"house": self.house, "version": QUEUE_VERSION, "items": []}
        if not isinstance(data, dict):
            return {"house": self.house, "version": QUEUE_VERSION, "items": []}
        items = data.get("items")
        if not isinstance(items, list):
            data["items"] = []
        data.setdefault("house", self.house)
        data.setdefault("version", QUEUE_VERSION)
        return data

    def _save(self, data: dict[str, Any]) -> dict[str, Any]:
        data["house"] = self.house
        data["version"] = QUEUE_VERSION
        data["updated_at"] = _utcnow()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        durable_write_text(self.path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        return data

    def list_items(self, *, status: str | None = None) -> list[dict[str, Any]]:
        items = self._load()["items"]
        out = [i for i in items if isinstance(i, dict)]
        if status:
            out = [i for i in out if i.get("status") == status]
        return sorted(
            out,
            key=lambda r: (_priority_rank(str(r.get("priority", ""))), str(r.get("enqueued_at", ""))),
        )

    def get(self, item_id: str) -> dict[str, Any] | None:
        for item in self._load()["items"]:
            if isinstance(item, dict) and item.get("id") == item_id:
                return item
        return None

    def _find_by_holder(self, holder: str) -> dict[str, Any] | None:
        for item in self._load()["items"]:
            if isinstance(item, dict) and item.get("holder") == holder:
                return item
        return None

    def active_item(self) -> dict[str, Any] | None:
        for st in ("launching", "in_flight"):
            for item in self.list_items(status=st):
                return item
        return None

    def peek(self) -> dict[str, Any] | None:
        if self.active_item():
            return None
        queued = self.list_items(status="queued")
        return queued[0] if queued else None

    def enqueue(
        self,
        *,
        intent: str,
        work_prompt: str | None = None,
        priority: str = "P2",
        notes: str = "",
        thread: str = DEFAULT_HOUSE,
    ) -> dict[str, Any]:
        rel_prompt = work_prompt
        if work_prompt:
            p = Path(work_prompt)
            if not p.is_file():
                p = _REPO / work_prompt
            if not p.is_file():
                return {"ok": False, "reason": "work_prompt_missing", "path": work_prompt}
            try:
                rel_prompt = str(p.relative_to(_REPO))
            except ValueError:
                rel_prompt = str(p)

        item = {
            "id": f"q-{uuid.uuid4().hex[:10]}",
            "intent": intent.strip(),
            "work_prompt": rel_prompt,
            "priority": priority.strip() or "P2",
            "notes": notes.strip(),
            "thread": thread,
            "status": "queued",
            "holder": None,
            "attempts": 0,
            "enqueued_at": _utcnow(),
            "started_at": None,
            "completed_at": None,
            "closeout_turn": None,
            "failure_reason": None,
        }
        data = self._load()
        data["items"].append(item)
        self._save(data)
        return {"ok": True, "item": item}

    def _patch(self, item_id: str, **fields: Any) -> dict[str, Any]:
        data = self._load()
        for item in data["items"]:
            if isinstance(item, dict) and item.get("id") == item_id:
                item.update(fields)
                self._save(data)
                return {"ok": True, "item": item}
        return {"ok": False, "reason": "not_found", "id": item_id}

    def mark_launching(self, item_id: str) -> dict[str, Any]:
        return self._patch(item_id, status="launching", started_at=_utcnow())

    def mark_in_flight(self, item_id: str, holder: str) -> dict[str, Any]:
        return self._patch(item_id, status="in_flight", holder=holder, started_at=_utcnow())

    def complete(
        self,
        item_id: str | None = None,
        *,
        holder: str | None = None,
        closeout_turn: int | None = None,
    ) -> dict[str, Any]:
        if not item_id and holder:
            found = self._find_by_holder(holder)
            if not found:
                return {"ok": False, "reason": "holder_not_found", "holder": holder}
            item_id = found["id"]
        if not item_id:
            return {"ok": False, "reason": "id_or_holder_required"}
        fields: dict[str, Any] = {
            "status": "done",
            "completed_at": _utcnow(),
        }
        if closeout_turn is not None:
            fields["closeout_turn"] = closeout_turn
        return self._patch(item_id, **fields)

    def fail(self, item_id: str, reason: str) -> dict[str, Any]:
        item = self.get(item_id)
        attempts = int((item or {}).get("attempts") or 0)
        return self._patch(
            item_id,
            status="failed",
            completed_at=_utcnow(),
            failure_reason=reason[:500],
            attempts=attempts,
        )

    def requeue_launch_failure(
        self,
        item_id: str,
        reason: str,
        *,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        item = self.get(item_id)
        if not item:
            return {"ok": False, "reason": "not_found", "id": item_id}
        attempts = int(item.get("attempts") or 0) + 1
        if attempts >= max_attempts:
            return self.fail(item_id, f"{reason}:terminal_attempt_{attempts}")
        return self._patch(
            item_id,
            status="queued",
            holder=None,
            started_at=None,
            completed_at=None,
            attempts=attempts,
            failure_reason=reason[:500],
        )

    def cancel(self, item_id: str) -> dict[str, Any]:
        return self._patch(item_id, status="cancelled", completed_at=_utcnow())

    def import_ready_index_rows(self, index_path: Path) -> dict[str, Any]:
        """Enqueue ``**ready**`` rows from tab-launch-index markdown (skip duplicates)."""
        if not index_path.is_file():
            return {"ok": False, "reason": "index_missing", "path": str(index_path)}
        imported: list[str] = []
        skipped: list[str] = []
        # Block re-import only while active or successfully completed.
        # Cancelled/failed rows must not pin a **ready** index row — that wedge
        # stalled house 10223 when hop-overnight was cancelled then re-greenlit.
        existing_prompts = {
            i.get("work_prompt")
            for i in self._load()["items"]
            if isinstance(i, dict)
            and i.get("work_prompt")
            and i.get("status") in ("queued", "launching", "in_flight", "done")
        }
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if "|" not in line or "**ready**" not in line.lower():
                continue
            parts = [p.strip() for p in line.strip().split("|")]
            if len(parts) < 5:
                continue
            prompt_cell = parts[1].strip("` ")
            if not prompt_cell.endswith(".md"):
                continue
            rel = f"tmp/prompts/{prompt_cell}" if "/" not in prompt_cell else prompt_cell
            if rel in existing_prompts:
                skipped.append(rel)
                continue
            intent = Path(prompt_cell).stem[:60]
            out = self.enqueue(
                intent=intent,
                work_prompt=rel,
                priority=parts[3].strip() or "P2",
                notes=parts[4].strip(),
            )
            if out.get("ok"):
                imported.append(out["item"]["id"])
                existing_prompts.add(rel)
        return {"ok": True, "imported": imported, "skipped": skipped}

    def discover_opportunities(
        self,
        *,
        index_path: Path,
        opportunities_path: Path | None = None,
    ) -> list[dict[str, str]]:
        """Mechanical scan for follow-on WORK — does not enqueue."""
        out: list[dict[str, str]] = []
        seen: set[str] = set()

        def _add(source: str, prompt: str, priority: str, notes: str, intent: str) -> None:
            rel = prompt if prompt.startswith("tmp/") else f"tmp/prompts/{prompt}"
            if rel in seen:
                return
            seen.add(rel)
            out.append(
                {
                    "source": source,
                    "work_prompt": rel,
                    "priority": priority,
                    "notes": notes,
                    "intent": intent[:60],
                }
            )

        if index_path.is_file():
            for line in index_path.read_text(encoding="utf-8").splitlines():
                if "|" not in line:
                    continue
                lower = line.lower()
                if "**ready**" in lower:
                    continue
                parts = [p.strip() for p in line.strip().split("|")]
                if len(parts) < 5:
                    continue
                prompt_cell = parts[1].strip("` ")
                notes = parts[4]
                if not prompt_cell.endswith(".md"):
                    continue
                actionable = any(
                    tok in notes.upper() for tok in ("FAIL", "BLOCKED", "FOLLOW-UP", "GAP", "DEBT")
                )
                if not actionable:
                    continue
                _add(
                    "index_followup",
                    prompt_cell,
                    parts[3].strip() or "opportunity",
                    notes,
                    Path(prompt_cell).stem,
                )

        if opportunities_path and opportunities_path.is_file():
            title = ""
            for line in opportunities_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("### "):
                    title = line.removeprefix("### ").strip()
                if "**Status**" in line and "`ready`" in line.lower():
                    m = re.search(r"`(tab-launch-[^`]+\.md)`", line)
                    if not m:
                        m = re.search(r"`(tmp/prompts/[^`]+\.md)`", line)
                    if m:
                        cell = m.group(1)
                        name = Path(cell).name
                        _add("opportunities", name, "opportunity", title, title[:60] or name)
        return out

    def discover_work(
        self,
        *,
        index_path: Path,
        opportunities_path: Path | None = None,
        enqueue_opportunities: bool = False,
    ) -> dict[str, Any]:
        """Fill queue from index **ready** rows; surface opportunities when still empty."""
        imported = self.import_ready_index_rows(index_path)
        peek = self.peek()
        opportunities = self.discover_opportunities(
            index_path=index_path,
            opportunities_path=opportunities_path,
        )
        enqueued: list[str] = []
        if enqueue_opportunities and not peek:
            existing = {
                i.get("work_prompt")
                for i in self._load()["items"]
                if isinstance(i, dict)
                and i.get("work_prompt")
                and i.get("status") in ("queued", "launching", "in_flight", "done")
            }
            for opp in opportunities:
                wp = opp.get("work_prompt") or ""
                if not wp or wp in existing:
                    continue
                p = _REPO / wp
                if not p.is_file():
                    continue
                res = self.enqueue(
                    intent=opp.get("intent") or Path(wp).stem[:60],
                    work_prompt=wp,
                    priority=opp.get("priority") or "opportunity",
                    notes=opp.get("notes") or "",
                )
                if res.get("ok"):
                    enqueued.append(res["item"]["id"])
                    existing.add(wp)
            peek = self.peek()
        consult_suggested = not peek and not enqueued and not opportunities
        return {
            "ok": True,
            "import_ready": imported,
            "peek": peek,
            "opportunities": opportunities,
            "enqueued_opportunities": enqueued,
            "consult_suggested": consult_suggested,
        }
