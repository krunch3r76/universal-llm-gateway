"""Shared constants for the consult CLI."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_STARGATE_URL = "http://localhost:9999"

DEFAULT_MODELS: list[str] = [
    "qwen3-32b-awq-32768",
    "ernie-4-5-21b-a3b-pt-q8-0-65536",
    "openai/gpt-5.3-codex",
]

# Resolved once at import time; overridable via env for tests.
STARGATE_URL: str = os.getenv("STARGATE_URL", DEFAULT_STARGATE_URL).rstrip("/")

_ROLES_PATH: Path = Path(__file__).resolve().parents[1] / "consult-roles.yaml"

DEFAULT_ROLE = "researcher"

DEFAULT_CHAIN_DIRECTIVE = (
    "You are a reviewer in a chained consultation. The analysis above was "
    "produced by a prior model. Evaluate whether you agree with its "
    "recommendations, identify any gaps or risks it missed, and propose "
    "additional changes if warranted. Do not re-derive what the prior "
    "analysis already covers correctly — focus on validation and augmentation."
)

# Per-phase prior-response cap to prevent unbounded prompt growth.
MAX_PRIOR_CHARS = 24_000
