#!/usr/bin/env python3
"""Same-tab stall wake — followup paste, never hop.

A hop opens a new Agents chat on the default model. This poller waits until the
named transcript is silent after an assistant turn, then pastes into that chat
(Ctrl+K by title + Ctrl+Enter) so the selected model is unchanged.

Landing proof: a new user row on the same transcript carrying the wake head.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from bus_watch.ide_followup import fire_transcript_followup
from durable_io.atomic import durable_write_text

_REPO = Path(__file__).resolve().parents[1]
_TRANSCRIPTS = (
    Path.home()
    / ".cursor"
    / "projects"
    / "mnt-torus-projects-universal-llm-gateway"
    / "agent-transcripts"
)
_CONTENT_ROLES = frozenset({"user", "assistant"})


def _transcript_path(transcript_id: str) -> Path:
    return _TRANSCRIPTS / transcript_id / f"{transcript_id}.jsonl"


def _last_content_role(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    text = data[-131072:].decode("utf-8", errors="replace")
    role: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = row.get("role")
        if found in _CONTENT_ROLES:
            role = found
    return role


def _stall(path: Path, stall_s: float) -> dict:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return {"ready": False, "reason": "missing"}
    role = _last_content_role(path)
    return {
        "ready": age >= stall_s and role == "assistant",
        "age_s": round(age, 1),
        "last_role": role,
    }


def _wake_message(transcript_id: str, prompt: str) -> str:
    return f"STALL-WAKE transcript={transcript_id}\n{prompt.strip()}\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--transcript-id", required=True)
    p.add_argument("--chat-title", required=True)
    p.add_argument("--gui-host", default="jupiter")
    p.add_argument("--stall-s", type=float, default=240.0)
    p.add_argument("--poll-s", type=float, default=15.0)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--state-file", dest="state_file", type=Path, default=None)
    p.add_argument(
        "--prompt",
        default=(
            "Continue todo:checkpoint-relay-server-side-collapse on agent-bus:11161. "
            "Do not claim agent-bus:10479. Phase A+B shipped (3e87ffb5, 5ee01df7). "
            "Phase C is parked on a closed charter status vocabulary. "
            "Pick the next open row or say the slice is done."
        ),
    )
    args = p.parse_args()
    path = _transcript_path(args.transcript_id)
    state_path = args.state_file or (
        _REPO / "tmp" / "watchers" / f"tab-stall-followup-{args.transcript_id}.state.json"
    )
    attempts = 0
    while True:
        snap = _stall(path, args.stall_s)
        durable_write_text(
            state_path,
            json.dumps(
                {
                    "status": "polling",
                    "mode": "followup",
                    "transcript_id": args.transcript_id,
                    "chat_title": args.chat_title,
                    "attempts": attempts,
                    **snap,
                },
                indent=2,
            )
            + "\n",
        )
        if not snap["ready"]:
            time.sleep(args.poll_s)
            continue
        attempts += 1
        print(
            f"stall-followup-fire: attempt={attempts} age_s={snap['age_s']} "
            f"title={args.chat_title!r}",
            flush=True,
        )
        out = fire_transcript_followup(
            _wake_message(args.transcript_id, args.prompt),
            transcript_id=args.transcript_id,
            chat_title=args.chat_title,
            gui_host=args.gui_host,
        )
        if out.get("ok") and out.get("landed"):
            print("stall-followup-landed: same transcript", flush=True)
            durable_write_text(
                state_path,
                json.dumps(
                    {"status": "complete", "landed": True, **out},
                    indent=2,
                    default=str,
                )
                + "\n",
            )
            return 0
        print(f"stall-followup-miss: {out.get('phase')} {out.get('fix', '')}", flush=True)
        if attempts >= args.max_attempts:
            durable_write_text(
                state_path,
                json.dumps(
                    {"status": "failed", "attempts": attempts, "last": out},
                    indent=2,
                    default=str,
                )
                + "\n",
            )
            print("stall-followup-failed: max attempts", flush=True)
            return 1
        time.sleep(args.stall_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
