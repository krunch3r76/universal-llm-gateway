"""H1–H3 holder join, S2 hop actuation, and S3 transfer retirement for :8 UUID tabs."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from giw_endpoint import giw_api_url

from bus_watch.fable_lock import WATCH_DIR
from bus_watch.ide_budget_exclude import pin_retired_resume_transcript
from bus_watch.ide_hop_retire import suffix_retired_tab_title

logger = logging.getLogger(__name__)

_HOUSELESS_DISPLAYS = frozenset({"8", ":8"})
_UUID_AGENT_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_NUMERIC_SCOPE_RE = re.compile(r"^\d+$")
_TAB_URL_UUID_RE = re.compile(
    r"cursor://(?:tab|agent)/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.I,
)
_CANONICAL_REQUEST_SUBJECT = "REQUEST| precompaction continuity"
_FORBIDDEN_SUBJECT_TOKENS = ("checkpoint", "hop-", "admitted", "progress")
_POLICY_KEY = "endpoint_subscription_hop_actuation"
_REQUEST_KEY_PREFIX = "hop-actuation-key:"


def parse_tab_uuid(tab_url: str | None) -> str | None:
    """Parse trailing tab UUID from ``cursor://tab/`` or ``cursor://agent/`` URLs."""
    text = str(tab_url or "").strip()
    if not text:
        return None
    match = _TAB_URL_UUID_RE.search(text)
    if match:
        return match.group(1).lower()
    return None


def is_uuid_agent_id(value: object) -> str | None:
    text = str(value or "").strip()
    if _UUID_AGENT_RE.match(text):
        return text.lower()
    return None


def enumerate_display8_uuid_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Every subscribed ``surface=cursor`` display ``:8``/``8`` row with a UUID ``agent_id``."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("surface") or "") != "cursor":
            continue
        disp = str(row.get("display") or "").strip()
        if disp not in _HOUSELESS_DISPLAYS:
            continue
        if str(row.get("state") or "") != "subscribed":
            continue
        uid = is_uuid_agent_id(row.get("agent_id"))
        if uid is None:
            continue
        out.append(row)
    return out


def h1_rows_for_scope(
    rows: list[dict[str, Any]], scope: str
) -> list[dict[str, Any]]:
    """H1 wake rows for one numeric scope (store already returns MAX id per agent+scope)."""
    scope_s = str(scope or "").strip()
    return [
        row
        for row in enumerate_display8_uuid_rows(rows)
        if str(row.get("thread_scope") or "").strip() == scope_s
    ]


@dataclass(frozen=True)
class RegistryClaims:
    entries: tuple[dict[str, Any], ...]
    refusal: str | None = None


def fetch_registry_claims(
    *,
    timeout_s: float = 10.0,
    fetch_json: Callable[[str], dict[str, Any]] | None = None,
) -> RegistryClaims:
    """Live work-registry claims; fail closed on transport error or truncation."""
    url = giw_api_url("/api/v1/cursor/registry?state=live")
    if fetch_json is not None:
        try:
            payload = fetch_json(url)
        except Exception:
            logger.exception("endpoint_subscription_holder: registry fetch failed")
            return RegistryClaims((), "claim_source_unavailable")
    else:
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.get(url)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("endpoint_subscription_holder: registry fetch failed")
            return RegistryClaims((), "claim_source_unavailable")
    if not isinstance(payload, dict):
        return RegistryClaims((), "claim_source_unavailable")
    if payload.get("truncated"):
        return RegistryClaims(tuple(payload.get("entries") or []), "claim_source_truncated")
    entries = payload.get("entries") or []
    return RegistryClaims(tuple(e for e in entries if isinstance(e, dict)))


def _claim_is_live(entry: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(entry.get("kind") or "") != "claim":
        return False
    if str(entry.get("status") or "") != "claimed":
        return False
    exp = str(entry.get("expires_at") or "").strip()
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
    except ValueError:
        return False
    now_dt = now or datetime.now(UTC)
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=UTC)
    return exp_dt > now_dt


def live_claims_for_scope(
    registry: RegistryClaims,
    scope: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    scope_s = str(scope or "").strip()
    out: list[dict[str, Any]] = []
    for entry in registry.entries:
        if str(entry.get("thread_id") or "").strip() != scope_s:
            continue
        if not _claim_is_live(entry, now=now):
            continue
        out.append(entry)
    return out


def claim_tab_uuids(claims: list[dict[str, Any]]) -> set[str]:
    uuids: set[str] = set()
    for claim in claims:
        address = claim.get("address")
        if not isinstance(address, dict):
            continue
        tab_url = address.get("tab_url")
        uid = parse_tab_uuid(str(tab_url) if tab_url is not None else None)
        if uid:
            uuids.add(uid)
    return uuids


def scope_claim_stale(claim_uuids: set[str], h1_rows: list[dict[str, Any]]) -> bool:
    """True when a live claim UUID is absent from every H1 row (claim-join staleness)."""
    if not claim_uuids:
        return False
    live_ids = {
        is_uuid_agent_id(row.get("agent_id"))
        for row in h1_rows
        if is_uuid_agent_id(row.get("agent_id"))
    }
    return any(uid not in live_ids for uid in claim_uuids)


@dataclass(frozen=True)
class HolderResolution:
    holder_uuid: str | None
    holder_row: dict[str, Any] | None
    actuation_refused: str | None
    scope_eligible: bool
    actuation_eligible: bool


def resolve_holder(
    scope: str,
    h1_rows: list[dict[str, Any]],
    registry: RegistryClaims,
    *,
    budget_probe: str | None = None,
    now: datetime | None = None,
) -> HolderResolution:
    """H1∧H2∧H3 holder join with typed ``actuation_refused`` codes."""
    if registry.refusal == "claim_source_unavailable":
        return HolderResolution(
            None, None, "claim_source_unavailable", False, False
        )
    if not h1_rows:
        return HolderResolution(None, None, "unknown", False, False)

    if registry.refusal == "claim_source_truncated":
        return HolderResolution(
            None, None, "claim_source_truncated", False, False
        )

    claims = live_claims_for_scope(registry, scope, now=now)
    claim_uuids = claim_tab_uuids(claims)

    if scope_claim_stale(claim_uuids, h1_rows):
        return HolderResolution(None, None, "stale", False, False)

    h12: list[dict[str, Any]] = []
    for row in h1_rows:
        uid = is_uuid_agent_id(row.get("agent_id"))
        if uid and uid in claim_uuids:
            h12.append(row)

    distinct = {
        is_uuid_agent_id(row.get("agent_id"))
        for row in h12
        if is_uuid_agent_id(row.get("agent_id"))
    }
    if len(distinct) >= 2:
        return HolderResolution(None, None, "ambiguous", False, False)

    if not h12:
        return HolderResolution(None, None, "no_live_claim", False, False)

    holder_row = h12[0]
    holder_uuid = is_uuid_agent_id(holder_row.get("agent_id"))
    assert holder_uuid is not None

    probe = budget_probe
    if probe is None or (isinstance(probe, str) and not str(probe).strip()):
        return HolderResolution(
            holder_uuid,
            holder_row,
            "incomplete",
            True,
            False,
        )
    if probe == "incomplete":
        return HolderResolution(
            holder_uuid,
            holder_row,
            "incomplete",
            True,
            False,
        )
    if probe != "glass.cdp":
        return HolderResolution(
            holder_uuid,
            holder_row,
            "probe_not_glass_cdp",
            True,
            False,
        )
    return HolderResolution(
        holder_uuid,
        holder_row,
        None,
        True,
        True,
    )


def per_row_actuation_refused(
    row: dict[str, Any],
    scope_resolution: HolderResolution,
    *,
    claim_uuids: set[str],
    h1_rows: list[dict[str, Any]],
) -> str | None:
    """Refusal label for one sampled UUID row (staleness via claim join only)."""
    if scope_resolution.actuation_refused in {
        "claim_source_unavailable",
        "claim_source_truncated",
        "unknown",
        "ambiguous",
    }:
        return scope_resolution.actuation_refused
    uid = is_uuid_agent_id(row.get("agent_id"))
    if uid and uid not in claim_uuids and claim_uuids:
        if uid not in {is_uuid_agent_id(r.get("agent_id")) for r in h1_rows}:
            return "stale"
    if scope_resolution.actuation_refused:
        return scope_resolution.actuation_refused
    if scope_resolution.holder_uuid and uid != scope_resolution.holder_uuid:
        return scope_resolution.actuation_refused or "no_live_claim"
    return scope_resolution.actuation_refused


def hop_actuation_policy_enabled(policy: dict[str, Any]) -> bool:
    """Per-root actuation policy; default off (AC9)."""
    return policy.get(_POLICY_KEY) is True


def request_subject_is_clean(subject: str) -> bool:
    lowered = (subject or "").lower()
    return not any(tok in lowered for tok in _FORBIDDEN_SUBJECT_TOKENS)


def build_note_subject() -> str:
    return "NOTE| prepare advisory"


def build_request_subject() -> str:
    return _CANONICAL_REQUEST_SUBJECT


def hop_actuation_key(scope: str, holder_uuid: str, band: str) -> str:
    return f"{_REQUEST_KEY_PREFIX} N={scope} U={holder_uuid} band={band}"


def prepare_note_key(scope: str, holder_uuid: str) -> str:
    return f"prepare-note: N={scope} U={holder_uuid}"


def actuation_record_path(scope: str) -> Path:
    slug = re.sub(r"[^0-9a-zA-Z_-]+", "_", str(scope))
    return WATCH_DIR / f"liaison-hop-actuation-{slug}.json"


def _load_actuation_record(scope: str) -> dict[str, Any]:
    path = actuation_record_path(scope)
    if not path.is_file():
        return {"keys": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"keys": []}
    if not isinstance(data, dict):
        return {"keys": []}
    keys = data.get("keys")
    if not isinstance(keys, list):
        keys = []
    return {"keys": [str(k) for k in keys]}


def _record_actuation_key(scope: str, key: str) -> None:
    path = actuation_record_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_actuation_record(scope)
    keys = list(state.get("keys") or [])
    if key not in keys:
        keys.append(key)
    path.write_text(json.dumps({"keys": keys}, indent=2) + "\n", encoding="utf-8")


def actuation_key_seen(scope: str, key: str) -> bool:
    return key in (_load_actuation_record(scope).get("keys") or [])


def build_request_body(
    *,
    scope: str,
    holder_uuid: str,
    band: str,
    as_of: str,
    used_tokens: int,
    window_limit_tokens: int,
) -> str:
    key = hop_actuation_key(scope, holder_uuid, band)
    return (
        f"{key}\n"
        f"scope={scope} holder={holder_uuid} band={band} as_of={as_of} "
        f"used={used_tokens} limit={window_limit_tokens}\n"
        "Order: reconcile receipts → CHECKPOINT → spawn successor → "
        "successor ACK on scope → re-subscribe and move claim.\n"
        "No hop protocol ⇒ reply BLOCKED|"
    )


def maybe_post_prepare_note(
    store: Any,
    *,
    scope: str,
    holder_row: dict[str, Any],
    prepare_advisory: dict[str, Any] | None,
    mailbox_agent: str | None = None,
) -> bool:
    holder_uuid = is_uuid_agent_id(holder_row.get("agent_id"))
    if not holder_uuid:
        return False
    key = prepare_note_key(scope, holder_uuid)
    if actuation_key_seen(scope, key):
        return False
    agent = mailbox_agent or str(holder_row.get("agent") or "").strip()
    if not agent:
        return False
    body = json.dumps(prepare_advisory or {}, indent=2)
    store.create_turn(
        thread=scope,
        from_agent="orion",
        to_agent=agent,
        subject=build_note_subject(),
        body=body,
    )
    _record_actuation_key(scope, key)
    return True


def maybe_post_hop_request(
    store: Any,
    *,
    scope: str,
    holder_row: dict[str, Any],
    band: str,
    policy_enabled: bool,
    glass_generating: bool,
    as_of: str,
    used_tokens: int,
    window_limit_tokens: int,
    mailbox_agent: str | None = None,
) -> dict[str, Any] | None:
    if not policy_enabled:
        return None
    if glass_generating:
        return None
    if band not in ("hop", "emergency"):
        return None
    holder_uuid = is_uuid_agent_id(holder_row.get("agent_id"))
    if not holder_uuid:
        return None
    key = hop_actuation_key(scope, holder_uuid, band)
    if actuation_key_seen(scope, key):
        return None
    agent = mailbox_agent or str(holder_row.get("agent") or "").strip()
    if not agent:
        return None
    subject = build_request_subject()
    turn = store.create_turn(
        thread=scope,
        from_agent="orion",
        to_agent=agent,
        subject=subject,
        body=build_request_body(
            scope=scope,
            holder_uuid=holder_uuid,
            band=band,
            as_of=as_of,
            used_tokens=used_tokens,
            window_limit_tokens=window_limit_tokens,
        ),
    )
    _record_actuation_key(scope, key)
    return turn if isinstance(turn, dict) else {"turn": turn}


def wake_ledger_delivered_for_turn(
    store: Any,
    *,
    scope: str,
    turn_id: int | None,
) -> bool:
    if turn_id is None:
        return False
    try:
        payload = store._bus(
            "GET",
            "/endpoint-wake/ledger",
            params={"thread": scope, "turn_id": turn_id, "limit": 10},
        )
    except Exception:
        return False
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = str(row.get("state") or "")
        if state == "delivered":
            return True
        if state in ("deferred", "pending") and row.get("delivered_at"):
            return True
    return False


@dataclass(frozen=True)
class TransferContext:
    scope: str
    mailbox_agent: str
    old_uuid: str
    new_uuid: str
    request_row_id: int
    rows: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    turns: list[dict[str, Any]]
    ledger_rows: list[dict[str, Any]]


def _row_for_mailbox(
    rows: list[dict[str, Any]], scope: str, mailbox_agent: str
) -> dict[str, Any] | None:
    matches = [
        row
        for row in h1_rows_for_scope(rows, scope)
        if str(row.get("agent") or "").strip() == mailbox_agent
    ]
    if not matches:
        return None
    return max(matches, key=lambda r: int(r.get("id") or 0))


def is_transfer_complete(ctx: TransferContext) -> bool:
    """T1∧T2∧T3∧T4 — rename only when complete (AC7)."""
    t1 = False
    current = _row_for_mailbox(ctx.rows, ctx.scope, ctx.mailbox_agent)
    if current is not None:
        current_id = int(current.get("id") or 0)
        current_uuid = is_uuid_agent_id(current.get("agent_id"))
        if (
            current_uuid == ctx.new_uuid.lower()
            and current_id > ctx.request_row_id
        ):
            t1 = True

    claim_uuids = claim_tab_uuids(ctx.claims)
    t2 = (
        ctx.new_uuid.lower() in claim_uuids
        and ctx.old_uuid.lower() not in claim_uuids
    )

    t3 = False
    for turn in ctx.turns:
        subject = str(turn.get("subject") or "")
        if not subject.upper().startswith("ACK|"):
            continue
        from_id = str(
            turn.get("from_agent_id") or turn.get("from_agent") or ""
        ).lower()
        if ctx.new_uuid.lower() in from_id or from_id.endswith(ctx.new_uuid.lower()):
            t3 = True
            break

    t4 = True
    for row in ctx.ledger_rows:
        state = str(row.get("state") or "")
        if state in ("pending", "deferred") and str(row.get("agent_id") or "") == ctx.old_uuid:
            t4 = False
            break

    return t1 and t2 and t3 and t4


def execute_transfer_retirement(
    *,
    scope: str,
    old_uuid: str,
    new_uuid: str,
    old_title: str,
    emit_transfer: Callable[..., None],
    rename_tab: Callable[[str, str], dict[str, Any]] | None = None,
    watch_dir: Path = WATCH_DIR,
) -> dict[str, Any]:
    """Suffix old tab, pin exclude, emit ``ide.hop.transfer.accepted`` (AC8)."""
    new_title = suffix_retired_tab_title(old_title)
    rename_result: dict[str, Any] = {"skipped": True}
    if rename_tab is not None:
        rename_result = rename_tab(old_uuid, new_title)
    pin = pin_retired_resume_transcript(scope, old_uuid, watch_dir=watch_dir)
    emit_transfer(scope=scope, departing_uuid=old_uuid, successor_uuid=new_uuid)
    return {
        "rename": rename_result,
        "pin": pin,
        "old_uuid": old_uuid,
        "new_uuid": new_uuid,
    }


def pressure_band_allows_request(band: str | None) -> bool:
    if band == "emergency":
        return True
    return band == "hop"


def brg_actuation_allowed(scope: str, policy: dict[str, Any]) -> bool:
    """BRG root 24600 requires explicit policy+operator auth (AC9)."""
    if str(scope) != "24600":
        return hop_actuation_policy_enabled(policy)
    return (
        hop_actuation_policy_enabled(policy)
        and policy.get("endpoint_subscription_hop_actuation_brg") is True
    )


__all__ = [
    "CANONICAL_REQUEST_SUBJECT",
    "HolderResolution",
    "RegistryClaims",
    "TransferContext",
    "actuation_key_seen",
    "brg_actuation_allowed",
    "build_note_subject",
    "build_request_body",
    "build_request_subject",
    "claim_tab_uuids",
    "enumerate_display8_uuid_rows",
    "execute_transfer_retirement",
    "fetch_registry_claims",
    "h1_rows_for_scope",
    "hop_actuation_key",
    "hop_actuation_policy_enabled",
    "is_transfer_complete",
    "is_uuid_agent_id",
    "live_claims_for_scope",
    "maybe_post_hop_request",
    "maybe_post_prepare_note",
    "parse_tab_uuid",
    "per_row_actuation_refused",
    "pressure_band_allows_request",
    "prepare_note_key",
    "request_subject_is_clean",
    "resolve_holder",
    "scope_claim_stale",
    "wake_ledger_delivered_for_turn",
]

CANONICAL_REQUEST_SUBJECT = _CANONICAL_REQUEST_SUBJECT
