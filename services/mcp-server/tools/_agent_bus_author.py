"""Author-field reconciliation for agent_bus write and read dispatch.

MCP ``agent_bus`` handlers call this before a turn is posted. A blank author
is the literal ``unspecified`` plus a warning, never a borrowed seat name.
``default_from_for_surface`` remains for boot-seat and dispatch-caller
resolution and is not applied to bus posts.
"""

from __future__ import annotations

from typing import Any

from agent_bus_store.author_attestation import attest
from request_profile import current_request_metadata

# Canonical bus addresses when the caller omits from=/from_agent on a dual mount.
_SURFACE_DEFAULT_FROM: dict[str, str] = {
    "life": "web-anthropic",
    "code": "cursor",
}

AUTHOR_AUTOFILL_OPS: frozenset[str] = frozenset(
    {"send", "hop", "post", "reply", "triage", "update_thread"}
)

# Blank bus authors are this literal. Not a seat, and not a surface default.
UNSPECIFIED_AUTHOR = "unspecified"

# Private sidecar on the reconciled args dict. Dispatch loops pop it before
# handler kwargs are bound and copy it onto the send/post/reply success payload.
AUTHOR_OMITTED_WARNING_KEY = "_author_omitted_warning"

AUTHOR_OMITTED_WARNING = (
    'missing field from_agent; recorded as "unspecified". '
    "Accepted spellings: from, from_agent."
)


def turn_author_attestation_fields() -> tuple[str | None, str | None]:
    """Read request metadata for turn credential stamp columns."""
    meta = current_request_metadata()
    attested = attest(
        meta.get("auth_mode"),
        meta.get("caller_identity"),
        meta.get("surface"),
        client_ip=meta.get("client_ip"),
        user_agent=meta.get("user_agent"),
    )
    raw_ua = meta.get("user_agent")
    user_agent: str | None = None
    if isinstance(raw_ua, str) and raw_ua.strip():
        user_agent = raw_ua.strip()
    return attested, user_agent


def resolve_dispatch_from_agent(
    from_agent: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """Resolve a dispatcher author after alias normalization.

    A blank ``from_agent`` becomes ``unspecified``. The error dict is ``None``
    when resolution succeeds. The omit warning is not returned here; send,
    post, and reply stamp it on their success payload. No bus I/O.
    """
    args, err = reconcile_author_arguments({"from_agent": from_agent})
    if err is not None:
        return "", err
    return str(args["from_agent"]), None


def pop_omitted_author_warning(parsed: dict[str, Any]) -> str | None:
    """Remove the omit-warning sidecar before handler kwargs are bound.

    ``reconcile_author_arguments`` stores the warning on a private key so a
    blank author can still succeed. Handlers reject unknown kwargs, so the
    MCP dispatch loop pops the key and later copies it onto the success
    payload. Returns the warning text, or None when the author was explicit.
    """
    raw = parsed.pop(AUTHOR_OMITTED_WARNING_KEY, None)
    if isinstance(raw, str) and raw:
        return raw
    return None


def stamp_omitted_author_warning(
    result: dict[str, Any], warning: str | None
) -> dict[str, Any]:
    """Copy an omit-warning onto a successful send, post, or reply payload.

    Error results are returned unchanged so a rejected post does not claim
    the blank author was accepted. An existing ``warning`` key is left in
    place. The dict is mutated and returned.
    """
    if not warning or "error" in result or "warning" in result:
        return result
    result["warning"] = warning
    return result


def default_from_for_surface(surface: str | None) -> str | None:
    """Return the canonical bus author for an MCP mount surface, if known."""
    if not surface:
        return None
    return _SURFACE_DEFAULT_FROM.get(surface)


def reconcile_author_arguments(
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Normalize ``from`` / ``from_agent`` / ``agent`` and fill a blank author.

    An explicit author is stripped and returned unchanged, with no warning.
    A blank author sets ``from_agent`` to ``unspecified`` on every MCP surface,
    including an unknown surface, and records ``_author_omitted_warning``.
    The second element is ``None`` on success. Callers pop the warning key
    before binding handler kwargs. This does not write the bus and does not
    borrow a seat name.
    """
    args = dict(parsed)
    if "from" in args:
        args.setdefault("from_agent", args.pop("from"))
    if "agent" in args:
        args.setdefault("from_agent", args.pop("agent"))

    from_agent = args.get("from_agent")
    if isinstance(from_agent, str) and from_agent.strip():
        args["from_agent"] = from_agent.strip()
        return args, None

    args["from_agent"] = UNSPECIFIED_AUTHOR
    args[AUTHOR_OMITTED_WARNING_KEY] = AUTHOR_OMITTED_WARNING
    return args, None
