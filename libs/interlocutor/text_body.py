"""Resolve interlocutor steer/spawn text and large-body pointer delivery."""

from __future__ import annotations

from typing import Any

STEER_INLINE_MAX_BYTES = 4096


def _read_share_uri(uri: str) -> str:
    from implement_admission.scheme_resolve import resolve_fs_ingress

    ingress = resolve_fs_ingress(uri.strip())
    path = ingress.resolved
    if path is None:
        raise FileNotFoundError(uri)
    if not path.is_file():
        raise FileNotFoundError(uri)
    return path.read_text(encoding="utf-8")


def pointer_body_for_large_text(*, source_uri: str, body_bytes: int) -> str:
    """Short steer body that points at a durable URI instead of inlining bytes."""
    uri = (source_uri or "").strip() or "inline"
    return (
        f"Full commission text ({body_bytes} bytes) is at {uri}. "
        "Read that URI with fs or cortex read before acting."
    )


def resolve_interlocutor_text(
    *,
    text: str | None,
    text_uri: str | None,
) -> dict[str, Any]:
    """Merge inline ``text`` and ``text_uri`` into a steer/spawn body."""
    inline = (text or "").strip()
    uri = (text_uri or "").strip()
    if uri and inline:
        return {
            "ok": False,
            "code": "text_uri_conflict",
            "reason": "pass either text or text_uri, not both",
        }
    if not uri and not inline:
        return {"ok": False, "code": "text_required", "reason": "text or text_uri required"}
    if uri:
        try:
            body = _read_share_uri(uri).strip()
        except (OSError, ValueError, FileNotFoundError) as exc:
            return {
                "ok": False,
                "code": "text_uri_unreadable",
                "reason": type(exc).__name__,
                "text_uri": uri,
            }
        source = uri
    else:
        body = inline
        source = "inline"
    nbytes = len(body.encode("utf-8"))
    if nbytes > STEER_INLINE_MAX_BYTES:
        if source == "inline":
            return {
                "ok": False,
                "code": "text_too_large",
                "reason": (
                    f"inline text is {nbytes} bytes; use text_uri for bodies "
                    f"over {STEER_INLINE_MAX_BYTES} bytes"
                ),
                "bytes": nbytes,
            }
        pointer = pointer_body_for_large_text(source_uri=uri, body_bytes=nbytes)
        return {
            "ok": True,
            "body": pointer,
            "large_delivered_via_pointer": True,
            "text_uri": uri,
            "source_bytes": nbytes,
        }
    return {"ok": True, "body": body, "text_uri": uri or None}


__all__ = [
    "STEER_INLINE_MAX_BYTES",
    "pointer_body_for_large_text",
    "resolve_interlocutor_text",
]
