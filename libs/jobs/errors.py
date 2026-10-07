"""Protocol error envelope for the jobs satellite.

Callers are the jobs HTTP routes. Every non-2xx response is a JobsError so
the FastAPI handler can emit ``{code, message, source, retryable, data}``
with ``source`` fixed to ``jobs``. The journal remains the authority; this
type only carries the wire refusal.
"""

from __future__ import annotations

from typing import Any

from universal_protocol.errors import ProtocolError


class JobsError(ProtocolError):
    """Wire refusal whose HTTP status is not the process exit code.

    Raised by route dependencies and handlers. ``status_code`` is the HTTP
    status. ``code`` is the stable machine identifier (``job_not_found``,
    ``body_field_forbidden``, and the other names in the stage-3 spec).
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        *,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            "jobs",
            retryable=retryable,
            data=data,
        )
        self.status_code = status_code
