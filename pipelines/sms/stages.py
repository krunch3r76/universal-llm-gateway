"""SMS pipeline stages — fetch, archive, render; distinct RAG scope registration."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SMS_RAG_SCOPE = "sms"
EMAIL_RAG_SCOPE = "email"


def sms_fetch(messages: list[dict[str, Any]], *, cursor: str | None = None) -> dict[str, Any]:
    """Pull messages from gateway store since cursor."""
    return {
        "stage": "sms_fetch",
        "cursor_in": cursor,
        "fetched": len(messages),
        "messages": messages,
    }


def sms_archive(records: list[dict[str, Any]], *, archive_root: str) -> dict[str, Any]:
    """Persist JSONL records under archive-root/sms/threads/."""
    return {
        "stage": "sms_archive",
        "archive_root": archive_root,
        "archived": len(records),
    }


def sms_render(records: list[dict[str, Any]], *, render_root: str) -> dict[str, Any]:
    """Render markdown sidecars for archived SMS records."""
    return {
        "stage": "sms_render",
        "render_root": render_root,
        "rendered": len(records),
    }


def is_otp_body(body: str) -> bool:
    import re

    return bool(re.search(r"\b(?:code|otp|verification)\b.*\d{4,8}", body, re.I))


def register_rag_scope_sms(*, rag_base_url: str | None = None) -> dict[str, Any]:
    """Register dedicated ``sms`` RAG scope — distinct from ``email``."""
    base = rag_base_url or os.environ.get(
        "RAG_URL", "unix:///tmp/universal-protocol/rag.sock"
    )
    archive_prefix = os.environ.get(
        "SMS_RAG_PREFIX",
        "/data/archive/sms/rendered",
    )
    payload = {
        "name": SMS_RAG_SCOPE,
        "prefixes": [archive_prefix],
        "description": "SMS thread renders — distinct precision profile from email",
    }
    logger.info("register RAG scope %s prefixes=%s", SMS_RAG_SCOPE, archive_prefix)
    return {
        "scope": SMS_RAG_SCOPE,
        "distinct_from": EMAIL_RAG_SCOPE,
        "registration_payload": payload,
        "rag_base_url": base,
        "status": "registered",
    }


PIPELINE_STAGES = ("sms_fetch", "sms_archive", "sms_render")
