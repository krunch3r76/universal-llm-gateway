"""In-process job token parse. Unresolved tokens are refused. Cortex is not consulted.

Spellings: strip surrounding whitespace, then exact case-sensitive id.
A ``job:`` prefix does not resolve. Internal whitespace does not resolve.
``none``, ``pure-mechanical``, and ``consult`` are ``job_unknown``.
``job_retired`` is not emitted.
"""

from __future__ import annotations

from dataclasses import dataclass

from job_vocab.records import JobRecord, job_record

_RETIRED_TOKENS = frozenset({"none", "pure-mechanical", "consult"})
_SESSION_VALUES = frozenset({"operator-proxy", "mission", "ask"})


@dataclass(frozen=True, slots=True)
class JobParse:
    job: str | None
    record: JobRecord | None
    reason: str | None
    registry_ref: str

    @property
    def ok(self) -> bool:
        return self.reason is None and self.record is not None


def resolve_job_token(raw: str | None) -> JobParse:
    """Parse one job token. ``None`` or blank is ``job_missing``."""
    if raw is None:
        return JobParse(None, None, "job_missing", "job_vocab:unresolved")
    token = str(raw).strip()
    if not token:
        return JobParse(None, None, "job_missing", "job_vocab:unresolved")
    if token != str(raw).strip() or any(ch.isspace() for ch in token):
        return JobParse(token, None, "job_unknown", "job_vocab:unresolved")
    if token.startswith("job:") or token in _RETIRED_TOKENS or token == "review":
        return JobParse(token, None, "job_unknown", "job_vocab:unresolved")
    record = job_record(token)
    if record is None:
        return JobParse(token, None, "job_unknown", "job_vocab:unresolved")
    return JobParse(token, record, None, record.registry_ref)


def resolve_session(raw: str | None) -> str | None:
    """Omission is legal (``None``). An unknown present value is ``session_unknown``."""
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None
    if token not in _SESSION_VALUES:
        return "session_unknown"
    return None


def job_admitted_on(record: JobRecord, op: str) -> bool:
    return op in record.admitted_ops
