"""Firing predicate and episode budget for the Opus second-read reflex leg.

cursor-auto holds no judgment of its own, so "occasionally escalate to a premium
reader" has to be a predicate over observable closeout shape plus a hard spend
cap — never a discretionary call. Pure except for the process-local budget
counters, which are deliberately in-memory: the reflex is a sampling device, and
losing the count across a worker restart is cheaper than a durable store.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass

_TRUTHY_OFF = {"0", "false", "no", "off"}

# Triggers read §2 through the relay's own field extractors rather than scanning
# raw text. Executors author these fields as bold lines, ATX headings, or table
# rows interchangeably, so any line-anchored regex written against one shape is
# quietly dead against the other two.
# Any AC the executor did not pass; self-graded acceptance is the single most
# common place a closeout overstates what landed. Deliberately not `partial`:
# that word appears in passing verdicts ("AC1=pass reason=partial coverage"),
# and a genuinely partial episode is already caught by the status trigger.
_AC_MISS_RE = re.compile(r"(?i)\b(fail(?:ed|s|ure)?|not[_ ]tested|unverified)\b")
# Field values meaning "nothing here" — anything else in `open forks` is a fork.
_EMPTY_VALUE_RE = re.compile(
    r"(?i)^(none|no|n/?a|nil|null|—|-|not applicable|nothing)\b[\s.;—-]*$"
)
_ESCALATE_RE = re.compile(r"(?im)^\s*ESCALATE\s*[:=]")
# Paths whose blast radius exceeds the executor's weight class: agent guidance and
# shared libraries are read by every downstream seat, so a quiet error compounds.
# Matched against the authored `effects` field only — implement closeouts mention
# these paths in prose constantly, and a trigger that fires on every mention is a
# trigger that just spends the whole budget on the first three jobs.
_SENSITIVE_PATH_RE = re.compile(
    r"(?i)(^|[\s`'\"(,])"
    r"(\.cursor/|\.claude/|cursor-plugins/|libs/|config/[\w.-]+\.ya?ml)"
)
_WEAK_STATUSES = frozenset({"partial", "blocked"})

_REFLEX_SKIP_CONTRACTS = frozenset({"answer", "confer", "execute", "propagate", "seed"})


def reflex_enabled() -> bool:
    """True unless explicitly disabled — the reflex is a spend path, keep it legible."""
    raw = os.environ.get("CURSOR_AUTO_REFLEX_ENABLED", "true").strip().lower()
    return raw not in _TRUTHY_OFF


def reflex_budget() -> int:
    """Max reflex legs per thread. ``0`` disables as surely as the enable flag."""
    try:
        return max(0, int(os.environ.get("CURSOR_AUTO_REFLEX_BUDGET", "3")))
    except ValueError:
        return 3


def reflex_sample_every() -> int:
    """Fire on every Nth otherwise-unremarkable job; ``0`` disables sampling."""
    try:
        return max(0, int(os.environ.get("CURSOR_AUTO_REFLEX_SAMPLE_EVERY", "5")))
    except ValueError:
        return 5


@dataclass(frozen=True, slots=True)
class ReflexVerdict:
    """Whether to fire the reflex leg, and the trigger or suppression reason."""

    fire: bool
    reason: str


class _EpisodeCounters:
    """Thread-scoped job and reflex tallies for the life of the worker process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, int] = {}
        self._spent: dict[str, int] = {}

    def note_job(self, thread_id: str) -> int:
        with self._lock:
            count = self._jobs.get(thread_id, 0) + 1
            self._jobs[thread_id] = count
            return count

    def spent(self, thread_id: str) -> int:
        with self._lock:
            return self._spent.get(thread_id, 0)

    def note_spend(self, thread_id: str) -> int:
        with self._lock:
            count = self._spent.get(thread_id, 0) + 1
            self._spent[thread_id] = count
            return count

    def reset(self, thread_id: str | None = None) -> None:
        with self._lock:
            if thread_id is None:
                self._jobs.clear()
                self._spent.clear()
                return
            self._jobs.pop(thread_id, None)
            self._spent.pop(thread_id, None)


_COUNTERS = _EpisodeCounters()


def counters() -> _EpisodeCounters:
    """Expose the process-local counters for wiring and tests."""
    return _COUNTERS


def _trigger_reason(
    *,
    contract: str,
    terminal_status: str,
    sdk_body: str,
    density: str | None,
) -> str | None:
    """Return the first matching trigger name, or ``None`` when nothing fires."""
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
        extract_status,
    )

    if terminal_status == "failed":
        return "executor_failed"
    if (extract_status(sdk_body) or "") in _WEAK_STATUSES:
        return "weak_closeout_status"
    ac_verdict = extract_field_section(sdk_body, "ac_verdict")
    if ac_verdict and _AC_MISS_RE.search(ac_verdict):
        return "ac_verdict_miss"
    if _ESCALATE_RE.search(sdk_body):
        return "executor_escalated"
    forks = extract_field_section(sdk_body, "open forks")
    if forks and not _EMPTY_VALUE_RE.match(forks.strip()):
        return "executor_escalated"
    if contract in {"implement", "verify"}:
        effects = extract_field_section(sdk_body, "effects") or ""
        if _SENSITIVE_PATH_RE.search(effects):
            return "sensitive_paths_touched"
    if density == "sparse":
        return "sparse_directive_scope"
    return None


def evaluate_reflex(
    *,
    thread_id: str,
    contract: str,
    terminal_status: str,
    sdk_body: str | None,
    density: str | None = None,
) -> ReflexVerdict:
    """Decide whether this terminal episode earns a premium second read.

    Call exactly once per job: the job tally that drives periodic sampling is
    advanced here, so a second call would double-count the episode.
    """
    if not reflex_enabled():
        return ReflexVerdict(False, "reflex_disabled")
    if contract in _REFLEX_SKIP_CONTRACTS:
        return ReflexVerdict(False, f"contract_exempt:{contract}")
    body = sdk_body or ""
    if not body.strip():
        return ReflexVerdict(False, "no_closeout_body")

    nth = _COUNTERS.note_job(thread_id)
    budget = reflex_budget()
    reason = _trigger_reason(
        contract=contract,
        terminal_status=terminal_status,
        sdk_body=body,
        density=density,
    )
    if reason is None:
        every = reflex_sample_every()
        if every and nth % every == 0:
            reason = "periodic_sample"
    if reason is None:
        return ReflexVerdict(False, "no_trigger")
    if _COUNTERS.spent(thread_id) >= budget:
        return ReflexVerdict(False, f"budget_exhausted:{budget}")
    return ReflexVerdict(True, reason)


__all__ = [
    "ReflexVerdict",
    "counters",
    "evaluate_reflex",
    "reflex_budget",
    "reflex_enabled",
    "reflex_sample_every",
]
