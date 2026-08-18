"""Cowork leg: one fresh project-ask submit+poll per grok turn.

A single retained Cowork session, polled via a cached ``cdp_url``, does not
survive the satellite's dormant/relaunch lifecycle: the port a session opened
on is torn down shortly after its first reply, so a later direct-CDP
reconnect silently finds nothing and the relay never notices (no exception,
no visible failure — confirmed live 2026-08-17, ``relays`` counter advanced
with zero effect on either product tab). ``submit`` + ``poll`` already
tolerates that lifecycle correctly (the satellite resolves/relaunches Chrome
per call), so each grok turn gets its own fresh ask instead of reusing a
cached session handle.
"""

from __future__ import annotations

import re
import time
from typing import Any

from cdp_ask.client import CdpAskClient

DEFAULT_PROJECT_ASK_URL = "http://127.0.0.1:8770"
_ADVANCE_PHASES = frozenset({"content_proof", "archiving", "terminal"})

# The harvested chat body carries scrape/UI chrome: a "Claude responded: <first
# words>" label line, tool-badge lines ("Used toys integration, used N skills")
# that render duplicated back-to-back, and a trailing relative timestamp. None
# of it is model content -- observed live 2026-08-17, previously stripped by
# hand before every manual relay.
_RESPONDED_LABEL_RE = re.compile(r"^claude responded:.*$", re.I)
_TRAILING_TIMESTAMP_RE = re.compile(
    r"^(just now|\d+\s+(second|minute|hour|day)s?\s+ago)\.?$", re.I
)
_TOOL_BADGE_SEGMENT = (
    r"(searched the web|used toys integration|used a skill|used \d+ skills?)"
)
_TOOL_BADGE_LINE_RE = re.compile(
    rf"^{_TOOL_BADGE_SEGMENT}(,\s*{_TOOL_BADGE_SEGMENT})*\.?$", re.I
)


class ClaudeLegError(RuntimeError):
    """project-ask submit/poll failed."""


def strip_chrome(text: str) -> str:
    """Drop the Cowork scrape chrome, keep the model's actual reply.

    The responded-label line is only recognized at position 0 (it is a
    scrape-time header, never mid-reply). Tool-badge lines are dropped
    wherever they land -- their vocabulary is narrow and exact enough that
    matching anywhere in the text does not risk eating real prose, and their
    position varies (sometimes leading, sometimes interleaved with a
    "Claude responded:" line landing between two of them).
    """
    lines = text.split("\n")
    if lines and _RESPONDED_LABEL_RE.match(lines[0].strip()):
        lines = lines[1:]
    lines = [line for line in lines if not _TOOL_BADGE_LINE_RE.match(line.strip())]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and _TRAILING_TIMESTAMP_RE.match(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def _client(base_url: str) -> CdpAskClient:
    return CdpAskClient(base_url=base_url, timeout_s=60.0)


def submit_retained(
    *,
    prompt_text: str,
    base_url: str = DEFAULT_PROJECT_ASK_URL,
    holder: str = "web-chat-relay",
) -> dict[str, Any]:
    """Admit a converse+retain Cowork ask. Admission ≠ first reply."""
    return _client(base_url).submit(
        {
            "prompt_text": prompt_text,
            "holder": holder,
            "purpose": "ask",
            "model": "opus-5",
            "converse": True,
            "no_project_uuid": True,
            "ensure_cowork_auto": True,
            "delete_after": False,
            "expected_size": "small",
            "harvest_source": "chat",
            "timeout_s": 360,
        }
    )


def poll_execution(execution_id: str, *, base_url: str) -> dict[str, Any]:
    return _client(base_url).poll(execution_id)


def poll_until_harvestable(
    execution_id: str,
    *,
    base_url: str = DEFAULT_PROJECT_ASK_URL,
    timeout_s: float = 420.0,
    poll_s: float = 5.0,
) -> dict[str, Any]:
    """Wait until content_proof/terminal (not turn_idle alone) or failed."""
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = poll_execution(execution_id, base_url=base_url)
        phase = str(last.get("completion_phase") or "")
        status = str(last.get("status") or "")
        if status == "failed" or phase == "failed":
            raise ClaudeLegError(
                f"project-ask failed execution_id={execution_id} "
                f"phase={phase} error={last.get('error')!r}"
            )
        if phase in _ADVANCE_PHASES or last.get("archive_uri") or last.get("body"):
            if phase == "turn_idle" and not last.get("body") and not last.get(
                "content_proof_uri"
            ):
                time.sleep(poll_s)
                continue
            if phase != "turn_idle" or last.get("body"):
                return last
        time.sleep(poll_s)
    raise ClaudeLegError(
        f"project-ask poll timeout execution_id={execution_id} last={last!r}"
    )


def ask_and_wait(
    *,
    prompt_text: str,
    base_url: str = DEFAULT_PROJECT_ASK_URL,
    holder: str = "web-chat-relay",
    poll_timeout_s: float = 420.0,
) -> dict[str, Any]:
    """Submit one turn and block until its reply is harvestable.

    Each call opens its own Cowork session — no session handle survives
    between calls. Cross-turn memory lives in Cortex, not in a cached
    ``chat_url``/``cdp_url``, so *prompt_text* should carry (or the caller's
    standing framing should carry) enough pointer context for a cold Opus
    session to search Cortex before writing anything new.
    """
    admitted = submit_retained(prompt_text=prompt_text, base_url=base_url, holder=holder)
    execution_id = str(admitted.get("execution_id") or "")
    if not execution_id:
        raise ClaudeLegError(f"submit missing execution_id: {admitted!r}")
    return poll_until_harvestable(
        execution_id, base_url=base_url, timeout_s=poll_timeout_s
    )
