"""Shared constants and SQL for T13 provenance reconstruct."""

from __future__ import annotations

import re
from pathlib import Path

MARKER = "reconstruct-2026-06-02"
REVIEW_NOTES = (
    "provenance gap: confirmed lacks locatable source; reconstruct 2026-06-02"
)

STAGED_DISPOSITION_SQL = """
SELECT id, entity_id, claim, reviewer, review_status
FROM assertions
WHERE superseded_by IS NULL
  AND review_status = 'staged'
  AND reviewer = ?
"""
EXPECTED_RECONSTRUCT_STAGED_COUNT = 2993
STAGED_ONLY_WRONG_COUNT_HINT = 7018
FILES_ROOT = Path(
    __import__("os").environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")
).expanduser()
WORKSPACES_ROOT = Path(
    __import__("os").environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")
).expanduser()

CANDIDATE_SQL = """
SELECT id, entity_id, claim, evidence, evidence_uris, chunk_id, derivation_type,
       confidence
FROM assertions
WHERE superseded_by IS NULL
  AND confidence = 'confirmed'
  AND (
    derivation_type = 'inference'
    OR evidence_uris IS NULL
    OR evidence_uris = ''
    OR evidence_uris = '[]'
  )
  AND (seeded_by IS NULL OR seeded_by != ?)
  AND (reviewer IS NULL OR reviewer != ? OR review_status != 'staged')
"""

PATH_RE = re.compile(
    r"(?:"
    r"(?:legal|notes|dropbox|evidence)/[\w./ -]+\.(?:pdf|eml|txt|png|jpg|md)"
    r"|files://[^\s,;]+"
    r"|workspaces://[^\s,;]+"
    r"|cortex://[^\s,;]+"
    r"|https?://[^\s,;]+"
    r")",
    re.IGNORECASE,
)
