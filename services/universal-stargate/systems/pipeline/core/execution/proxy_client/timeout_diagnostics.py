"""ProxyClient timeout diagnostic reporting and error conversion.

Persists structured JSON reports on timeout for forensic analysis under
DATA_DIR/pipeline-timeout-diagnostics/. Converts TimeoutException into
standardized 504 ProxyClientError after writing the report.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import httpx
from universal_logging import get_logger

from .errors import ProxyClientError

if TYPE_CHECKING:
    from .configuration import ProxyClientConfig

logger = get_logger(__name__)


class _ProxyTimeoutDiagnostics:
    """Mixin providing timeout diagnostic persistence for ProxyClient.

    All timeout paths (chat_completion, embeddings, rerank) delegate here
    so that diagnostic format and location remain consistent.
    """

    _config: ProxyClientConfig

    async def _write_timeout_diagnostic(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        request_body: dict[str, Any],
        request_headers: dict[str, str],
        execution_id: str | None,
        step_id: str | None,
        detail: str,
        queue_wait_seconds: float | None = None,
        inference_elapsed_seconds: float | None = None,
        timeout_type: str | None = None,
    ) -> str | None:
        """Persist timeout diagnostic report for forensic debugging."""
        data_dir = Path(os.getenv("DATA_DIR", "/tmp"))
        report_dir = data_dir / "pipeline-timeout-diagnostics"
        request_id = request_headers.get("X-Internal-Request-ID", "")
        safe_request_id = request_id.replace("/", "-")[:32] or "unknown"
        timestamp = (
            datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        endpoint_part = endpoint.strip("/").replace("/", "-")
        filename = f"{timestamp}_{endpoint_part}_{safe_request_id}.json"
        report_path = report_dir / filename

        report_payload: dict[str, Any] = {
            "timestamp": timestamp,
            "endpoint": endpoint,
            "timeout_seconds": timeout_seconds,
            "execution_id": execution_id,
            "step_id": step_id,
            "request_id": request_id or None,
            "cancel_group": request_headers.get("X-Pipeline-Cancel-Group"),
            "queue_wait_seconds": queue_wait_seconds,
            "inference_elapsed_seconds": inference_elapsed_seconds,
            "timeout_type": timeout_type,
            "request_headers": request_headers,
            "request_body": request_body,
            "error_detail": detail,
            "transport": {
                "stargate_url": getattr(self._config, "stargate_url", None),
            },
        }

        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            json_payload = json.dumps(report_payload, indent=2, ensure_ascii=False)
            await asyncio.to_thread(report_path.write_text, json_payload, "utf-8")
            return str(report_path)
        except (
            Exception
        ) as dump_error:  # pragma: no cover - diagnostic-only best effort
            logger.warning("Failed to write timeout diagnostic report: %s", dump_error)
            return None

    async def _raise_timeout_error(
        self,
        *,
        endpoint: str,
        request_timeout: float,
        request_body: dict[str, Any],
        request_headers: dict[str, str],
        execution_id: str | None,
        step_id: str | None,
        exception: httpx.TimeoutException,
        request_kind: str,
    ) -> NoReturn:
        """Write diagnostic report and raise standardized timeout error."""
        report_path = await self._write_timeout_diagnostic(
            endpoint=endpoint,
            timeout_seconds=request_timeout,
            request_body=request_body,
            request_headers=request_headers,
            execution_id=execution_id,
            step_id=step_id,
            detail=str(exception),
        )
        if report_path:
            logger.error(
                "Pipeline %s request timed out after %.1fs; diagnostic=%s",
                request_kind,
                request_timeout,
                report_path,
            )
        raise ProxyClientError(
            f"Request timeout after {request_timeout}s",
            status_code=504,
            detail=str(exception),
        ) from exception
