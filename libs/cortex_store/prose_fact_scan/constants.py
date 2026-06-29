"""Closed rules for the stale-prose scanner (todo:stale-prose-scanner)."""

from __future__ import annotations

TIER_A_GLOBS: tuple[str, ...] = (
    "notes/system/shared/operational-context-*.md",
    "notes/system/handoffs/*.md",
    "notes/system/session-handoff/**/*.md",
    "notes/system/session-handoffs/**/*.md",
    "notes/system/kickoffs/**/*.md",
    "notes/system/boot/**/*.md",
    "notes/system/context/**/*.md",
    "notes/system/contexts/**/*.md",
)

HARD_EXCLUDED_SUBTREES: tuple[str, ...] = (
    "transcripts/",
    "journal/",
    "audit/",
    "post-mortems/",
    "verifications/",
    "cases/",
    "consultations/",
    "consults/",
    "findings/",
    "investigations/",
    "reviews/",
    "panels/",
    "probes/",
    "recon/",
)

TARGET_COUNT_MIN = 80
TARGET_COUNT_MAX = 200

BIND_ADMIT_THRESHOLD = 0.75
ALIGNMENT_ADMIT_THRESHOLD = 0.85

ELIGIBLE_CONFIDENCE = frozenset({"confirmed", "believed"})
ELIGIBLE_REVIEW_STATUS = frozenset({"committed", "flagged"})

REPORT_DIR = "notes/system/audit/prose-fact-scan"
SERVICE_OWNER = "prose-fact-scanner"
SERVICE_ENTITY_ID = "service:prose-fact-scanner"

ANTONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("suspended", "reinstated"),
    ("suspended", "restored"),
    ("deactivated", "active"),
    ("banned", "restored"),
    ("terminated", "employed"),
    ("terminated", "onboarded"),
)

TRANSPORT_RE = (
    r"(Uber|Lyft|rideshare|driver).*(suspended|reinstated|deactivated|"
    r"banned|active|restored)|"
    r"(suspended|reinstated|deactivated|banned|active|restored).*"
    r"(Uber|Lyft|rideshare|driver)"
)
INCOME_RE = (
    r"(per-diem|RxRelief|pharmacy|income|runway|livelihood|shifts?|"
    r"placement)"
)
ROLE_RE = r"(durable[_ ]identity|decision authority|administrator of|principal)"
WORKFLOW_RE = (
    r"(in_progress|done|blocked|open).*(todo:|task:)|"
    r"(todo:|task:).*(in_progress|done|blocked|open)"
)

ENTITY_ID_RE = r"\b[a-z_]+:[a-z0-9-]+\b"

PAST_TENSE_RE = (
    r"\b(was|were|had|at write time|landed|applied in session|"
    r"as of \d{4}-\d{2}-\d{2}|this session)\b"
)
PRESENT_HEAD_RE = r"\b(now|currently|reads:|applied to \*\*)"

CITATION_RE = (
    r"(→ asrt \d+|→ entity:|cortex://assertion/\d+|\(asrt \d+)"
)
ANNOTATED_RESOLVED_RE = (
    r"(PRE-CORRECTION SNAPSHOT|annotated 20\d\d-|Do not read this line as current)"
)
WRONG_FENCED_RE = r"#\s*WRONG"
