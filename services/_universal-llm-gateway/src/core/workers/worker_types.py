"""
Worker-specific types and data structures.

This module defines types used across the workers module for better type safety
and code organization.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class WorkerStatus(Enum):
    """Worker process status enumeration."""

    INITIALIZING = "initializing"
    READY = "ready"
    BUSY = "busy"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class ModelFormat(Enum):
    """Supported model formats."""

    GGUF = "gguf"
    AWQ = "awq"
    GPTQ = "gptq"


@dataclass
class WorkerConfig:
    """Configuration for worker processes."""

    worker_logs_dir: str = field(
        default_factory=lambda: os.getenv(
            "WORKER_LOG_DIR", "/tmp/llm_gateway/worker-logs"
        )
    )
    ipc_socket_dir: str = "/tmp/universal-protocol"
    python_executable: str | None = None
    startup_timeout: float = 300.0
    shutdown_timeout: float = 30.0
    worker_timeout: float = 300.0
    model_info_timeout: float = 30.0
    force_stop_timeout: float = 5.0
    sigterm_wait_timeout: float = 5.0
    sigkill_wait_timeout: float = 3.0
    detached: bool = True


@dataclass
class ModelConfig:
    """Model configuration sent to workers."""

    name: str
    format: str
    path: str
    loader_config: dict[str, Any]


@dataclass
class InferenceRequest:
    """Inference request structure."""

    messages: list[dict[str, str]] | None = None
    prompt: str | None = None
    parameters: dict[str, Any] = None


@dataclass
class InferenceResponse:
    """Inference response structure."""

    content: str
    finish_reason: str
    usage: dict[str, Any]
    model_id: str
    timestamp: datetime


@dataclass
class TokenCountRequest:
    """Token counting request structure."""

    messages: list[dict[str, str]] | None = None
    prompt: str | None = None
    context_length: int | None = None


@dataclass
class TokenCountResponse:
    """Token counting response structure."""

    token_count: int
    method_used: str
    confidence: float
    model_id: str
    timestamp: datetime


@dataclass
class WorkerHealth:
    """Worker health information."""

    status: WorkerStatus
    model_loaded: bool
    last_health_check: datetime
    error_message: str | None = None
