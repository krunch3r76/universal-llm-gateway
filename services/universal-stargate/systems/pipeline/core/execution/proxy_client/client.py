"""Public ProxyClient class.

Wires the transport lifecycle, request header builder, timeout diagnostics,
chat completion, vector (embeddings/rerank), and cancellation mixins into
the public API surface while keeping only initialization and the
from_environment factory here.
"""

from __future__ import annotations

import httpx

from .cancellation import _ProxyCancellation
from .chat_completion import _ProxyChatCompletion
from .chat_completion_streaming import _ProxyChatCompletionStream
from .configuration import ProxyClientConfig
from .request_headers import _ProxyRequestHeaders
from .timeout_diagnostics import _ProxyTimeoutDiagnostics
from .transport_lifecycle import _ProxyTransportLifecycle
from .vector_requests import _ProxyVectorRequests


class ProxyClient(
    _ProxyTransportLifecycle,
    _ProxyRequestHeaders,
    _ProxyTimeoutDiagnostics,
    _ProxyChatCompletion,
    _ProxyChatCompletionStream,
    _ProxyVectorRequests,
    _ProxyCancellation,
):
    """
    HTTP client for pipeline → Stargate internal communication.

    Submits requests through Stargate's full pipeline:
    - Transformations (generation_params, message transforms)
    - Profiles (model-specific defaults)
    - Token management
    - Request queue (wait for capacity)
    - Routing (gateway selection, model loading)

    Transport selection (UDS vs TCP) is fully delegated to
    transport_utils.make_async_client() using the resolved stargate_url
    from ProxyClientConfig (or DEFAULT_STARGATE_URL).

    Usage:
        client = ProxyClient.from_environment()
        response, map_req_id, snap_id = await client.chat_completion(
            model="model-id",
            messages=[{"role": "user", "content": "Hello"}],
            execution_id="pipeline-123",
            step_id="step-1",
        )
    """

    def __init__(self, config: ProxyClientConfig | None = None):
        """Initialize ProxyClient with configuration.

        Args:
            config: Transport configuration. If None, use default resolved
                via transport_utils (STARGATE_UNIX_SOCKET / STARGATE_URL / PORT).
        """
        self._config = config or ProxyClientConfig.from_environment()
        self._client: httpx.AsyncClient | None = None
        self._active_requests: int = 0

    @classmethod
    def from_environment(cls) -> ProxyClient:
        """Create ProxyClient with transport auto-detected from environment."""
        return cls(ProxyClientConfig.from_environment())
