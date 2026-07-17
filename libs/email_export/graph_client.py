"""MSAL client-credentials auth and Microsoft Graph MIME fetch."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from email_export.intent import Selector

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphAuthError(Exception):
    """Azure credentials missing or token acquisition failed."""


class GraphNotFoundError(Exception):
    """Graph could not resolve the requested message."""


@dataclass(frozen=True, slots=True)
class AzureCredentials:
    tenant_id: str
    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls) -> AzureCredentials:
        missing = [
            name
            for name, value in (
                ("AZURE_TENANT_ID", os.environ.get("AZURE_TENANT_ID")),
                ("AZURE_CLIENT_ID", os.environ.get("AZURE_CLIENT_ID")),
                ("AZURE_CLIENT_SECRET", os.environ.get("AZURE_CLIENT_SECRET")),
            )
            if not (value and str(value).strip())
        ]
        if missing:
            raise GraphAuthError(
                "missing Azure env: "
                + ", ".join(missing)
                + " (set in ~/.gateway/m365-export.env or shell)"
            )
        return cls(
            tenant_id=str(os.environ["AZURE_TENANT_ID"]).strip(),
            client_id=str(os.environ["AZURE_CLIENT_ID"]).strip(),
            client_secret=str(os.environ["AZURE_CLIENT_SECRET"]).strip(),
        )


def _acquire_token(creds: AzureCredentials) -> str:
    try:
        import msal
    except ImportError as exc:
        raise GraphAuthError("msal package not installed") from exc

    app = msal.ConfidentialClientApplication(
        creds.client_id,
        authority=f"https://login.microsoftonline.com/{creds.tenant_id}",
        client_credential=creds.client_secret,
    )
    result = app.acquire_token_for_client(scopes=[GRAPH_SCOPE])
    if not isinstance(result, dict):
        raise GraphAuthError("token acquisition returned unexpected payload")
    token = result.get("access_token")
    if not token:
        error = result.get("error_description") or result.get("error")
        raise GraphAuthError(f"token acquisition failed: {error}")
    return str(token)


class GraphClient:
    def __init__(self, creds: AzureCredentials, *, timeout: float = 60.0) -> None:
        self._creds = creds
        self._timeout = timeout
        self._token: str | None = None

    @classmethod
    def from_env(cls, *, timeout: float = 60.0) -> GraphClient:
        return cls(AzureCredentials.from_env(), timeout=timeout)

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = _acquire_token(self._creds)
        return {"Authorization": f"Bearer {self._token}"}

    def _client(self) -> httpx.Client:
        # Public Graph HTTPS — do not use make_sync_client (defaults to RAG UDS).
        return httpx.Client(timeout=self._timeout, trust_env=False)

    def _get_bytes(self, url: str) -> bytes:
        with self._client() as client:
            response = client.get(url, headers=self._headers())
            if response.status_code == 404:
                raise GraphNotFoundError(f"Graph resource not found: {url}")
            response.raise_for_status()
            return response.content

    def _get_json(self, url: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(url, headers=self._headers())
            if response.status_code == 404:
                raise GraphNotFoundError(f"Graph resource not found: {url}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Graph returned non-object JSON")
            return payload

    def resolve_graph_id(self, mailbox_upn: str, selector: Selector) -> str:
        """Resolve selector → Graph message id (for fetch + archive move)."""
        if selector.kind == "graph_item_id":
            return selector.value
        if selector.kind == "message_id":
            return self._resolve_by_message_id(mailbox_upn, selector.value)
        if selector.kind == "conversation_id":
            return self._resolve_by_conversation_id(
                mailbox_upn, selector.value, expand=selector.expand
            )
        raise GraphNotFoundError(
            "fingerprint selectors are not supported in v0; "
            "use message_id or graph_item_id"
        )

    def fetch_mime(self, mailbox_upn: str, selector: Selector) -> bytes:
        graph_id = self.resolve_graph_id(mailbox_upn, selector)
        return self._fetch_by_graph_id(mailbox_upn, graph_id)

    def fetch_mime_by_graph_id(self, mailbox_upn: str, graph_id: str) -> bytes:
        return self._fetch_by_graph_id(mailbox_upn, graph_id)

    def ensure_mail_folder(self, mailbox_upn: str, display_name: str) -> str:
        """Return folder id for display_name; create under mailbox root if missing."""
        upn = quote(mailbox_upn, safe="@.")
        name = display_name.strip()
        if not name:
            raise ValueError("archive folder display_name must be non-empty")
        escaped_name = name.replace("'", "''")
        filt = quote(f"displayName eq '{escaped_name}'", safe="='")
        list_url = (
            f"{GRAPH_BASE}/users/{upn}/mailFolders"
            f"?$filter={filt}&$select=id,displayName"
        )
        payload = self._get_json(list_url)
        values = payload.get("value")
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, dict) and first.get("id"):
                return str(first["id"])
        create_url = f"{GRAPH_BASE}/users/{upn}/mailFolders"
        with self._client() as client:
            response = client.post(
                create_url,
                headers=self._headers(),
                json={"displayName": name},
            )
            if response.status_code >= 400:
                raise GraphAuthError(
                    f"create mailFolder {name!r} failed: "
                    f"{response.status_code} {response.text[:300]}"
                )
            created = response.json()
        folder_id = created.get("id") if isinstance(created, dict) else None
        if not folder_id:
            raise RuntimeError(f"mailFolder create returned no id for {name!r}")
        return str(folder_id)

    def move_message(
        self, mailbox_upn: str, graph_id: str, destination_folder_id: str
    ) -> str:
        """Move message to destination folder. Returns new graph id if changed."""
        upn = quote(mailbox_upn, safe="@.")
        item_id = quote(graph_id, safe="=")
        url = f"{GRAPH_BASE}/users/{upn}/messages/{item_id}/move"
        with self._client() as client:
            response = client.post(
                url,
                headers=self._headers(),
                json={"destinationId": destination_folder_id},
            )
            if response.status_code == 404:
                raise GraphNotFoundError(
                    f"message not found for move: {graph_id}"
                )
            if response.status_code >= 400:
                raise GraphAuthError(
                    f"move failed: {response.status_code} {response.text[:300]}"
                )
            payload = response.json()
        new_id = payload.get("id") if isinstance(payload, dict) else None
        return str(new_id or graph_id)

    def message_parent_folder_id(
        self, mailbox_upn: str, graph_id: str
    ) -> str | None:
        upn = quote(mailbox_upn, safe="@.")
        item_id = quote(graph_id, safe="=")
        url = (
            f"{GRAPH_BASE}/users/{upn}/messages/{item_id}"
            f"?$select=parentFolderId"
        )
        payload = self._get_json(url)
        parent = payload.get("parentFolderId")
        return str(parent) if parent else None

    def _fetch_by_graph_id(self, mailbox_upn: str, graph_id: str) -> bytes:
        upn = quote(mailbox_upn, safe="@.")
        item_id = quote(graph_id, safe="=")
        url = f"{GRAPH_BASE}/users/{upn}/messages/{item_id}/$value"
        return self._get_bytes(url)

    def _resolve_by_message_id(self, mailbox_upn: str, message_id: str) -> str:
        upn = quote(mailbox_upn, safe="@.")
        escaped = message_id.replace("'", "''")
        filt = quote(f"internetMessageId eq '{escaped}'", safe="='")
        list_url = f"{GRAPH_BASE}/users/{upn}/messages?$filter={filt}&$select=id"
        payload = self._get_json(list_url)
        values = payload.get("value")
        if not isinstance(values, list) or not values:
            raise GraphNotFoundError(f"message_id not found: {message_id}")
        first = values[0]
        if not isinstance(first, dict) or not first.get("id"):
            raise GraphNotFoundError(f"message_id not found: {message_id}")
        return str(first["id"])

    def _resolve_by_conversation_id(
        self, mailbox_upn: str, conversation_id: str, *, expand: bool
    ) -> str:
        upn = quote(mailbox_upn, safe="@.")
        escaped = conversation_id.replace("'", "''")
        filt = quote(f"conversationId eq '{escaped}'", safe="='")
        list_url = (
            f"{GRAPH_BASE}/users/{upn}/messages?$filter={filt}"
            f"&$select=id&$orderby=receivedDateTime asc"
        )
        payload = self._get_json(list_url)
        values = payload.get("value")
        if not isinstance(values, list) or not values:
            raise GraphNotFoundError(
                f"conversation_id not found: {conversation_id}"
            )
        if expand and len(values) > 1:
            raise GraphNotFoundError(
                "conversation_id expand is not implemented in v0; "
                "use message_id selectors for thread members"
            )
        first = values[0]
        if not isinstance(first, dict) or not first.get("id"):
            raise GraphNotFoundError(
                f"conversation_id not found: {conversation_id}"
            )
        return str(first["id"])


def fixture_mime_bytes(selector: Selector, account: str) -> bytes:
    """Deterministic RFC822 fixture for --dry-run without Graph."""
    if selector.kind == "message_id":
        message_id = selector.value
    elif selector.kind == "graph_item_id":
        message_id = f"<dry-run-graph-{selector.value[:32]}@example.com>"
    elif selector.kind == "conversation_id":
        message_id = f"<dry-run-conv-{selector.value[:32]}@example.com>"
    else:
        message_id = "<dry-run-fingerprint@example.com>"

    lines = [
        "From: dry-run-sender@example.com",
        f"To: {account}",
        f"Subject: Dry-run fixture ({selector.kind})",
        f"Message-ID: {message_id}",
        "Date: Thu, 17 Jul 2026 12:00:00 +0000",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "",
        f"Dry-run fixture for selector kind={selector.kind}.",
    ]
    return "\r\n".join(lines).encode("utf-8")
