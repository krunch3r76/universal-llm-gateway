"""ProxyClient request header builder mixin.

Centralizes construction of X-Pipeline-* and X-Internal-* headers used
for execution tracing, token counting bypass, timeout propagation,
capacity tracking, and cancellation grouping inside Stargate.
"""

from __future__ import annotations


class _ProxyRequestHeaders:
    """Mixin providing _build_request_headers for pipeline-internal metadata."""

    def _build_request_headers(
        self,
        execution_id: str | None,
        step_id: str | None,
        skip_token_counting: bool,
        timeout: float | None = None,
        *,
        request_id: str | None = None,
        cancel_group: str | None = None,
    ) -> dict[str, str]:
        """Build internal request headers for pipeline identification.

        Args:
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            skip_token_counting: Whether to skip token counting
            timeout: Request timeout (passed to stargate for federation)
            request_id: Per-call unique ID for capacity tracking + snapshots.
                Becomes context.request_id in Stargate via X-Internal-Request-ID.
            cancel_group: Iteration-level ID for group cancellation.
                Stargate's MasterRequestTracker indexes requests by this group.
        """
        headers: dict[str, str] = {"X-Pipeline-Internal": "true"}

        if execution_id:
            headers["X-Pipeline-Execution-Id"] = execution_id
        if step_id:
            headers["X-Pipeline-Step-Id"] = step_id
        if skip_token_counting:
            headers["X-Skip-Token-Counting"] = "true"
        if timeout:
            headers["X-Request-Timeout"] = str(timeout)
        if request_id:
            headers["X-Internal-Request-ID"] = request_id
        if cancel_group:
            headers["X-Pipeline-Cancel-Group"] = cancel_group

        return headers
