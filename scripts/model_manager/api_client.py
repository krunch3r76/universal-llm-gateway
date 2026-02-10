"""Gateway/Stargate API client for model-manager."""

from __future__ import annotations

import os
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import requests
from requests.adapters import HTTPAdapter

DEFAULT_GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:9998")
DEFAULT_API_KEY = os.getenv("GATEWAY_API_KEY")
DEFAULT_TIMEOUT = float(os.getenv("MODEL_MANAGER_TIMEOUT", "10"))
DEFAULT_RETRIES = int(os.getenv("MODEL_MANAGER_RETRIES", "2"))


@dataclass
class ModelSummary:
    """Model summary from catalog API."""

    model_id: str
    filename: str
    hf_repo: str | None
    format: str
    display_name: str | None


@dataclass
class ModelDetail:
    """Full model detail from catalog API."""

    model_id: str
    metadata: dict[str, Any]
    download: dict[str, Any]
    configurations: dict[str, Any]


class GatewayAPIClient:
    """Client for Gateway catalog API via Stargate federation proxy.

    Always routes through Stargate's /gateway/* endpoints to reach isolated Gateway.

    Args:
        base_url: Stargate URL (e.g., http://localhost:9999)
        api_key: Optional API key for authentication
        timeout: Request timeout in seconds
        retries: Number of retries for failed requests
        federated: Must be True (kept for backward compatibility)
    """

    def __init__(
        self,
        base_url: str = DEFAULT_GATEWAY_URL,
        *,
        api_key: str | None = None,
        timeout: float | None = None,
        retries: int | None = None,
        federated: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        if not federated:
            logger = __import__("universal_logging").get_logger(__name__)
            logger.warning(
                "Direct Gateway access (federated=False) is deprecated. "
                "All requests now route through Stargate federation proxy."
            )
        self._session: requests.Session | None = None
        self._api_key = api_key if api_key is not None else DEFAULT_API_KEY
        self._timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self._retries = retries if retries is not None else DEFAULT_RETRIES

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            if self._retries > 0:
                adapter = HTTPAdapter(max_retries=self._retries)
                self._session.mount("http://", adapter)
                self._session.mount("https://", adapter)
        return self._session

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def health_check(self) -> bool:
        """Check if Gateway is reachable."""
        try:
            resp = self.session.get(
                f"{self.base_url}/health",
                headers=self._headers(),
                timeout=self._timeout,
            )
            return resp.status_code == HTTPStatus.OK
        except requests.RequestException:
            return False

    def list_models(self, format_filter: str | None = None) -> list[ModelSummary]:
        """Get list of catalog models."""
        url = f"{self.base_url}/api/v1/catalog/models/list"
        params = {}
        if format_filter:
            params["format_filter"] = format_filter

        resp = self.session.get(
            url, params=params, headers=self._headers(), timeout=self._timeout
        )
        resp.raise_for_status()

        data = resp.json()
        models = data.get("models")
        if not isinstance(models, list):
            raise ValueError("Malformed response: missing 'models' list")

        return [
            ModelSummary(
                model_id=m["model_id"],
                filename=m["filename"],
                hf_repo=m.get("hf_repo"),
                format=m["format"],
                display_name=m.get("display_name"),
            )
            for m in models
            if isinstance(m, dict)
        ]

    def get_model(self, model_id: str) -> ModelDetail | None:
        """Get full model details."""
        url = f"{self.base_url}/api/v1/catalog/models/{model_id}"

        try:
            resp = self.session.get(url, headers=self._headers(), timeout=self._timeout)
            if resp.status_code == HTTPStatus.NOT_FOUND:
                return None
            resp.raise_for_status()

            data = resp.json()
            for key in ("model_id", "metadata", "download", "configurations"):
                if key not in data:
                    return None  # Malformed response treated as not found
            return ModelDetail(
                model_id=data["model_id"],
                metadata=data["metadata"],
                download=data["download"],
                configurations=data["configurations"],
            )
        except requests.RequestException:
            return None

    def add_model(
        self,
        model_key: str,
        config: dict[str, Any],
        *,
        allow_overwrite: bool = True,
        static: bool = False,
    ) -> tuple[dict[str, Any], int]:
        """
        Add or update model via Stargate federation proxy.

        Routes to Gateway through: POST /gateway/models

        Args:
            model_key: Model identifier
            config: Catalog entry (metadata, download, configurations)
            allow_overwrite: If True, overwrite existing model
            static: If True, write to static catalog (maintainer mode)

        Returns:
            Tuple of (response_dict, status_code).

        Raises:
            requests.HTTPError: On 4xx/5xx errors.
        """
        # Always use federation proxy endpoint
        url = f"{self.base_url}/gateway/models"

        payload = {
            "model_key": model_key,
            "config": config,
            "allow_overwrite": allow_overwrite,
            "static": static,
        }
        headers = self._headers()
        headers["Content-Type"] = "application/json"

        resp = self.session.post(
            url, json=payload, headers=headers, timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.json(), resp.status_code


def get_api_client(
    gateway_url: str | None = None,
    *,
    api_key: str | None = None,
    timeout: float | None = None,
    retries: int | None = None,
    federated: bool = True,
) -> GatewayAPIClient | None:
    """
    Get API client for Stargate (federated gateway access).

    Args:
        gateway_url: Stargate URL (e.g., http://localhost:9999)
        api_key: Optional API key
        timeout: Request timeout
        retries: Retry count
        federated: Must be True (kept for backward compatibility)

    Returns None if Stargate is unreachable (fallback to file-based).
    """
    client = GatewayAPIClient(
        gateway_url or DEFAULT_GATEWAY_URL,
        api_key=api_key,
        timeout=timeout,
        retries=retries,
        federated=federated,
    )

    if client.health_check():
        return client
    return None
