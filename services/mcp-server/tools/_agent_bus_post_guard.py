"""Post-path fork guard for the MCP agent_bus surface (guardrail C).

The MCP-layer half of the 1140->1142 silent-fork incident fix. Guardrails A
(numeric-slug reject) and B (after_turn reject) live on the agent-bus REST
route (``libs/agent_bus_store``); this module reconciles the MCP ``post``
dispatch surface with that route so the same misuse is caught with actionable
guidance rather than an uninformative unknown-argument rejection.

Two concerns:
  - ``from`` -> ``from_agent`` alias reconciliation (parity with the route's
    ``Field(alias="from")`` + ``populate_by_name`` model).
  - Continuation-shaped keys (``thread`` / ``after_turn``) on ``post`` are
    rejected with a structured "use reply" envelope before the dispatch
    wrapper's unknown-argument gate mangles them.

Plus a reshaper that surfaces the REST route's structured 400 guard envelope
(e.g. guardrail A's numeric-slug rejection) instead of flattening it to
"HTTP 400" at the MCP layer.
"""

from __future__ import annotations

from typing import Any

# Fields that only make sense when continuing an existing thread via reply.
# Their presence on post is the signature of a caller who meant reply and
# would otherwise silently fork a new thread (the 1140->1142 footgun).
POST_CONTINUATION_KEYS: tuple[str, ...] = ("thread", "after_turn")

# `cursor-auto` is the Auto executor ROLE and a legitimate turn AUTHOR, but not a
# bus address: it normalizes to a mailbox no seat's inbox fetch expands to, so a
# send addressed there is a silent black hole — written, delivered to nobody,
# arming nothing (threads 6310/6314). Reject the spellings rather than allowlist
# `to`: live traffic legitimately addresses charter-runner, dispatch, all, and
# opaque cursor-sdk:dispatch:<uuid> mailboxes.
SEND_NON_ADDRESSABLE_TO: frozenset[str] = frozenset({"cursor-auto", "cursor_auto"})


def reconcile_post_arguments(
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Give the MCP ``post`` path parity with POST /threads/with-turn.

    The route's Pydantic model accepts a ``from`` alias (``populate_by_name``)
    and its guard rejects continuation-shaped misuse. Mirror both at the
    dispatch boundary, before the unknown-argument gate would mangle them:

      - ``from`` -> ``from_agent``: the route accepts either; the dispatch
        wrapper would otherwise reject the bare ``from`` key as unsupported.
        Explicit ``from_agent`` wins when both are supplied.
      - ``thread`` / ``after_turn``: continuation fields signalling the caller
        meant ``reply``. They never reach the route guard (rejected earlier as
        unknown args with an uninformative message), so the actionable envelope
        is produced here.

    Returns ``(normalized_args, rejection_envelope_or_None)``.
    """
    args = dict(parsed)
    if "from" in args:
        alias_value = args.pop("from")
        args.setdefault("from_agent", alias_value)

    for key in POST_CONTINUATION_KEYS:
        if key not in args:
            continue
        # after_turn=0 is the skip-sentinel (_post_impl injects it at REST);
        # align with _reply_impl's ``if after_turn > 0`` guard.
        if key == "after_turn" and args[key] == 0:
            args.pop("after_turn")
            continue
        return args, {
            "error": (
                f"post: {key!r} is a continuation field and has no effect "
                "on post, which always creates a NEW thread. To continue an "
                "existing thread use reply(thread=<id>, after_turn=<n>)."
            ),
            "reason": f"{key}_not_valid_on_post",
            key: args[key],
            "suggestion": "use_reply_to_continue",
        }
    return args, None


def structured_route_guard(result: dict[str, Any]) -> dict[str, Any] | None:
    """Surface a structured route-guard 400 as an actionable MCP envelope.

    The agent-bus route returns ``{"detail": {"reason", "message", ...}}`` for
    the post-fork guard (e.g. guardrail A's numeric-slug rejection); the relay
    forwards that ``detail`` alongside ``error``. Re-shape it into the
    ``{error, reason, ...}`` envelope MCP callers already discriminate on so the
    guard's guidance to use ``reply`` reaches the caller instead of a bare
    "HTTP 400". Returns None when there is no structured guard detail to surface.
    """
    detail = result.get("detail")
    if not (isinstance(detail, dict) and detail.get("reason")):
        return None
    envelope: dict[str, Any] = {
        "error": detail.get("message") or f"post rejected: {detail['reason']}"
    }
    for key, value in detail.items():
        if key != "message":
            envelope[key] = value
    return envelope


def reconcile_send_arguments(
    parsed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Reconcile ``from`` → ``from_agent`` for the MCP ``send`` dispatch surface."""
    args = dict(parsed)
    if "from" in args:
        alias_value = args.pop("from")
        args.setdefault("from_agent", alias_value)
    if "supersedes_turn_id" in args:
        legacy_id = args.pop("supersedes_turn_id")
        if args.get("supersedes_turn") is None:
            args["supersedes_turn"] = legacy_id
        elif args["supersedes_turn"] != legacy_id:
            return args, {
                "error": (
                    "send: supersedes_turn and supersedes_turn_id disagree — "
                    "pass only supersedes_turn (turn_number)"
                ),
                "reason": "supersedes_turn_alias_conflict",
            }
    raw_to = str(args.get("to", "")).strip()
    if raw_to.lower() in SEND_NON_ADDRESSABLE_TO:
        return args, {
            "error": (
                "send: to='cursor-auto' is not a bus address — cursor-auto is the Auto "
                "executor role (and a valid turn author), not a mailbox. A turn sent "
                "there reaches no inbox and arms nothing. To arm cursor-auto use "
                "agent_bus(tool='request', to='cursor', ...) which probes handler "
                "liveness and returns handler_status + poll_hint. To reach an attended "
                "cursor seat use send with to='cursor'."
            ),
            "reason": "send_to_cursor_auto_not_addressable",
            "provided": raw_to,
            "suggestion": "use_request_to_arm_auto_or_send_to_cursor",
        }
    return args, None


def structured_slug_exists(result: dict[str, Any]) -> dict[str, Any] | None:
    """Surface REST 409 slug_exists as an actionable MCP send envelope."""
    detail = result.get("detail")
    if not (isinstance(detail, dict) and detail.get("error") == "slug_exists"):
        return None
    slug = detail.get("slug", "")
    existing_id = detail.get("existing_thread_id", "")
    return {
        "error": (
            f"send: slug {slug!r} already exists (thread {existing_id}). "
            "Use send(thread=<id>, ...) to continue it or choose a different "
            "new_slug."
        ),
        "reason": "slug_exists",
        "slug": slug,
        "existing_thread_id": existing_id,
    }
