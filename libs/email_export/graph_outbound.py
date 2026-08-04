"""Microsoft Graph outbound mail — draft create and send for M365 mailboxes."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from email_export.graph_client import (
    GRAPH_BASE,
    GraphAuthError,
    GraphClient,
    GraphNotFoundError,
)


def _recipient_rows(addresses: list[str]) -> list[dict[str, Any]]:
    return [
        {"emailAddress": {"address": addr.strip()}}
        for addr in addresses
        if addr and str(addr).strip()
    ]


def create_message_draft(
    client: GraphClient,
    mailbox_upn: str,
    *,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
) -> str:
    """Create an unsent message in Drafts. Returns Graph message id."""
    if not to:
        raise ValueError("to recipients required")
    content = body_html if body_html is not None else (body_text or "")
    content_type = "HTML" if body_html is not None else "Text"
    payload: dict[str, Any] = {
        "subject": subject or "",
        "body": {"contentType": content_type, "content": content},
        "toRecipients": _recipient_rows(to),
    }
    if cc:
        payload["ccRecipients"] = _recipient_rows(cc)
    if bcc:
        payload["bccRecipients"] = _recipient_rows(bcc)

    upn = quote(mailbox_upn, safe="@.")
    url = f"{GRAPH_BASE}/users/{upn}/messages"
    with client._client() as http:
        response = http.post(url, headers=client._headers(), json=payload)
        if response.status_code == 404:
            raise GraphNotFoundError(f"mailbox not found: {mailbox_upn}")
        if response.status_code >= 400:
            raise GraphAuthError(
                f"create draft failed: {response.status_code} "
                f"{response.text[:300]}"
            )
        created = response.json()
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError("Graph create message returned no id")
    return str(created["id"])


def send_draft_message(
    client: GraphClient,
    mailbox_upn: str,
    draft_id: str,
) -> dict[str, Any]:
    """Send an existing Graph draft. Returns transport metadata."""
    upn = quote(mailbox_upn, safe="@.")
    item_id = quote(draft_id, safe="=")
    url = f"{GRAPH_BASE}/users/{upn}/messages/{item_id}/send"
    with client._client() as http:
        response = http.post(url, headers=client._headers())
        if response.status_code == 404:
            raise GraphNotFoundError(f"draft not found for send: {draft_id}")
        if response.status_code >= 400:
            raise GraphAuthError(
                f"send draft failed: {response.status_code} "
                f"{response.text[:300]}"
            )
    return {
        "draft_id": draft_id,
        "status": "sent",
        "transport": "m365_graph",
        "mailbox": mailbox_upn,
    }
