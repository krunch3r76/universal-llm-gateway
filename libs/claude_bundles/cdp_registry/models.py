"""Define CDP registry row types, failure classes, and status vocabulary governing host lifecycle transitions and persistence."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from universal_protocol.errors import error_envelope

# A dormant row owns no Chrome process and no port: the CSE URL and the seeded
# profile are the durable identity, so relaunch picks whatever port is free.
STATUS_DORMANT = "dormant"
STATUS_TAB = "tab"

_RESERVED_STATUSES = frozenset(
    {
        "allocating",
        "active",
        "released",
        "retained",
        "orphaned_retry",
        "orphaned_alive",
    }
)
_RECLAIMABLE_STATUSES = frozenset({"released", "orphaned_retry"})
# Host-listable == "a Chrome process may still hold this CSE". Dormant is
# excluded so no live-attachment consumer mistakes a released host for an open
# tab. Seat identity is a separate axis (``seat_open``); do not put ``seat`` on
# this status set.
_HOST_LISTABLE_STATUSES = frozenset({"active", "orphaned_alive", "retained"})
# Occupancy axis — distinct from lifecycle status. Seating consults this set,
# not ``status == "active"``. ``retained`` still occupies a host after
# kill=False / hygiene keep; ``dormant`` does not (no Chrome). ``orphaned_alive``
# stays off this axis (existing seating contract). Registry-only: no CDP probe.
_CAPACITY_STATUSES = frozenset({"active", "retained"})
STALE_ACTIVE_TTL_S = 6 * 3600
_DEFAULT_DORMANT_TTL_S = 24 * 3600
_DEFAULT_DORMANT_MAX_ROWS = 16
# Chrome-host mission taxonomy (bus parent is parent_thread, not nest_under).
MISSION_KINDS = frozenset({"root", "hop", "side", "parallel"})

_LaunchFn = Callable[[int, Path], int]
_ListenFn = Callable[[int], bool]


class RegistryError(RuntimeError):
    """Base exception for failures while allocating, attaching, or managing CDP registry hosts and persistent rows."""


DEFAULT_ACCOUNT_ID = "default"
_ACCOUNT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_SEAT_ACCOUNTS_ENV = "CDP_SEAT_ACCOUNTS_PATH"
_ACCOUNT_SIGNIN_ENV = "CDP_ACCOUNT_SIGNIN_PATH"
_ACCOUNTS_CONFIG_ENV = "CDP_ACCOUNTS_CONFIG_PATH"
_LIVE_PROFILE_SUFFIX_STATUSES = frozenset(
    {"allocating", "active", "retained", "orphaned_alive", "orphaned_retry"}
)


def normalize_account_id(account_id: str | None) -> str:
    """Return a validated account id; empty/None maps to ``default``."""
    if account_id is None:
        return DEFAULT_ACCOUNT_ID
    text = str(account_id).strip()
    if not text:
        return DEFAULT_ACCOUNT_ID
    if text in {".", ".."} or "/" in text or ".." in text or text.startswith("."):
        raise RegistryError(f"invalid account_id: {account_id!r}")
    if not _ACCOUNT_ID_RE.fullmatch(text):
        raise RegistryError(f"invalid account_id: {account_id!r}")
    return text


def account_profile_suffix(account_id: str | None) -> str | None:
    """Persistent profile suffix for non-default accounts; ``None`` for default."""
    norm = normalize_account_id(account_id)
    if norm == DEFAULT_ACCOUNT_ID:
        return None
    if norm.startswith("account-"):
        return norm
    return f"account-{norm}"


def _seat_accounts_path() -> Path:
    raw = os.environ.get(_SEAT_ACCOUNTS_ENV, "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".gateway" / "cdp-registry" / "seat_accounts.json"


def _account_signin_path() -> Path:
    """Path to operator-maintained sign-in registry (no in-repo writer or TTL).

    Entries are written out-of-band after a verified claude.ai login; first
    account-2 use requires that manual step before routed asks may select it.
    """
    raw = os.environ.get(_ACCOUNT_SIGNIN_ENV, "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".gateway" / "cdp-registry" / "account_sign_in.json"


def account_id_from_profile_suffix(profile_suffix: str | None) -> str | None:
    """Map persistent ``account-*`` profile suffix back to account id."""
    suffix = str(profile_suffix or "").strip()
    if suffix.startswith("account-"):
        return normalize_account_id(suffix)
    return None


def resolve_reserve_account_id(
    *,
    account_id: str | None = None,
    carry: dict[str, Any] | None = None,
    prior_row: dict[str, Any] | None = None,
    profile_suffix: str | None = None,
) -> str:
    """Resolve account for ``reserve_allocating_row`` without dropping dormant identity."""
    explicit = str(account_id or "").strip()
    if explicit:
        return normalize_account_id(explicit)
    if isinstance(carry, dict) and str(carry.get("account_id") or "").strip():
        return normalize_account_id(str(carry["account_id"]))
    if isinstance(prior_row, dict) and str(prior_row.get("account_id") or "").strip():
        return normalize_account_id(str(prior_row["account_id"]))
    from_suffix = account_id_from_profile_suffix(profile_suffix)
    if from_suffix is not None:
        return from_suffix
    if isinstance(prior_row, dict):
        from_prior = account_id_from_profile_suffix(prior_row.get("profile_suffix"))
        if from_prior is not None:
            return from_prior
    return DEFAULT_ACCOUNT_ID


def signed_in_accounts() -> set[str]:
    """Account ids marked signed-in in the operator-written sign-in file."""
    path = _account_signin_path()
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    accounts = payload.get("accounts")
    if isinstance(accounts, dict):
        out: set[str] = set()
        for key, meta in accounts.items():
            if not str(key or "").strip():
                continue
            try:
                if isinstance(meta, dict) and meta.get("signed_in") is True:
                    out.add(normalize_account_id(str(key)))
                elif meta is True:
                    out.add(normalize_account_id(str(key)))
            except RegistryError:
                continue
        return out
    if isinstance(accounts, list):
        out_list: set[str] = set()
        for item in accounts:
            if not str(item or "").strip():
                continue
            try:
                out_list.add(normalize_account_id(str(item)))
            except RegistryError:
                continue
        return out_list
    return set()


def account_is_signed_in(account_id: str | None) -> bool:
    """True when *account_id* is default or listed in the sign-in registry."""
    norm = normalize_account_id(account_id)
    if norm == DEFAULT_ACCOUNT_ID:
        return True
    return norm in signed_in_accounts()


ACCOUNTS_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "cdp" / "accounts.yaml"
)
_VALID_DISPLAY_KINDS = frozenset(
    {"cdp_default", "messages", "cdp_account", "cursor"}
)
_FORBIDDEN_ACCOUNT_MAP_DISPLAYS = frozenset({":1", ":2", ":3", ":8"})


class AccountDisplayError(RegistryError):
    """Raised when a non-default account has no valid display binding in config."""


def _accounts_config_path() -> Path:
    raw = os.environ.get(_ACCOUNTS_CONFIG_ENV, "").strip()
    if raw:
        return Path(raw)
    return ACCOUNTS_CONFIG_PATH


def _normalize_display_binding(display_raw: str) -> str:
    text = display_raw.strip()
    if not text.startswith(":"):
        text = f":{text}"
    return text.split(".", 1)[0]


def _load_accounts_yaml_dict() -> dict[str, Any]:
    path = _accounts_config_path()
    if not path.is_file():
        raise AccountDisplayError(f"missing accounts config: {path}")
    try:
        import yaml
    except ImportError as exc:
        raise AccountDisplayError("PyYAML required for accounts config") from exc
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise AccountDisplayError(f"malformed accounts config: {path}") from exc
    if not isinstance(raw, dict):
        raise AccountDisplayError(f"malformed accounts config: {path}")
    return raw


@dataclass(frozen=True)
class _AccountsConfig:
    never: frozenset[str]
    displays: dict[str, dict[str, str]]
    cdp_account_displays: dict[str, str]


def _parse_accounts_config(raw: dict[str, Any]) -> _AccountsConfig:
    never_raw = raw.get("never")
    if never_raw is None:
        never_set = frozenset({":1"})
    elif isinstance(never_raw, list):
        never_set = frozenset(
            _normalize_display_binding(str(item)) for item in never_raw if str(item).strip()
        )
    else:
        raise AccountDisplayError("malformed never list in accounts config")

    displays_raw = raw.get("displays")
    if not isinstance(displays_raw, dict):
        raise AccountDisplayError("malformed displays section in accounts config")

    displays: dict[str, dict[str, str]] = {}
    for key, row in displays_raw.items():
        disp = _normalize_display_binding(str(key))
        if not isinstance(row, dict):
            raise AccountDisplayError(f"malformed display row for {disp!r}")
        owner = row.get("owner")
        kind = row.get("kind")
        if not isinstance(owner, str) or not owner.strip():
            raise AccountDisplayError(f"display {disp!r} missing owner")
        if kind not in _VALID_DISPLAY_KINDS:
            raise AccountDisplayError(f"display {disp!r} has invalid kind {kind!r}")
        if disp in displays:
            raise AccountDisplayError(f"duplicate display row {disp!r}")
        displays[disp] = {"owner": owner.strip(), "kind": str(kind)}

    mapping_raw = raw.get("cdp_account_displays")
    if mapping_raw is None:
        account_map: dict[str, str] = {}
    elif not isinstance(mapping_raw, dict):
        raise AccountDisplayError("malformed cdp_account_displays in accounts config")
    else:
        account_map = {}
        claimants: dict[str, str] = {}
        for acct_raw, disp_raw in mapping_raw.items():
            acct = normalize_account_id(str(acct_raw))
            disp = _normalize_display_binding(str(disp_raw))
            if disp in never_set or disp in _FORBIDDEN_ACCOUNT_MAP_DISPLAYS:
                raise AccountDisplayError(
                    f"cdp_account_displays may not map account {acct!r} to {disp!r}"
                )
            row = displays.get(disp)
            if row is None or row["kind"] != "cdp_account":
                raise AccountDisplayError(
                    f"cdp_account_displays {acct!r} -> {disp!r} is not a cdp_account display"
                )
            prior = claimants.get(disp)
            if prior is not None:
                raise AccountDisplayError(
                    f"duplicate display claim on {disp!r} ({prior!r} and {acct!r})"
                )
            claimants[disp] = acct
            account_map[acct] = disp

    return _AccountsConfig(
        never=never_set,
        displays=displays,
        cdp_account_displays=account_map,
    )


def _load_accounts_config() -> _AccountsConfig:
    return _parse_accounts_config(_load_accounts_yaml_dict())


def display_rows() -> dict[str, dict[str, str]]:
    """Display metadata rows from ``config/cdp/accounts.yaml``."""
    return dict(_load_accounts_config().displays)


def validate_default_display_resolved(resolved: str) -> None:
    """Fail closed when the default account's resolved display is forbidden or claimed."""
    key = _normalize_display_binding(resolved)
    cfg = _load_accounts_config()
    if key in cfg.never:
        raise RegistryError(f"display {key} is forbidden for default CDP fleet")
    for acct, disp in cfg.cdp_account_displays.items():
        if disp == key:
            raise RegistryError(f"display {key} claimed by account {acct!r}")
    row = cfg.displays.get(key)
    if row is not None and row["kind"] != "cdp_default":
        raise RegistryError(
            f"display {key} has kind {row['kind']!r}, expected cdp_default"
        )


def account_display(account_id: str) -> str:
    """X display for a non-default account from ``cdp_account_displays`` in accounts.yaml."""
    norm = normalize_account_id(account_id)
    if norm == DEFAULT_ACCOUNT_ID:
        raise AccountDisplayError("default account has no config display binding")
    cfg = _load_accounts_config()
    disp = cfg.cdp_account_displays.get(norm)
    if disp is None:
        raise AccountDisplayError(f"no display binding for account {norm!r}")
    return disp


class AccountNotSignedInError(RegistryError):
    """Raised when routing to a non-default account that is not signed in."""


def _resolve_account_id_raw(
    account_id: str | None = None,
    *,
    purpose: str | None = None,
    holder: str | None = None,
    parent_thread: str | None = None,
    model: str | None = None,
) -> tuple[str, bool]:
    """Return (account_id, explicit_request) before the signed-in gate."""
    _ = model  # reserved for future routing
    explicit = str(account_id or "").strip()
    if explicit:
        return normalize_account_id(explicit), True
    path = _seat_accounts_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            by_parent = payload.get("by_parent_thread") or {}
            by_holder = payload.get("by_holder") or {}
            by_purpose = payload.get("by_purpose") or {}
            parent = str(parent_thread or "").strip()
            if parent and parent in by_parent:
                return normalize_account_id(str(by_parent[parent])), False
            hold = str(holder or "").strip()
            if hold and hold in by_holder:
                return normalize_account_id(str(by_holder[hold])), False
            purp = str(purpose or "").strip()
            if purp and purp in by_purpose:
                return normalize_account_id(str(by_purpose[purp])), False
            default = payload.get("default")
            if default is not None and str(default).strip():
                return normalize_account_id(str(default)), False
    return DEFAULT_ACCOUNT_ID, False


def resolve_account_id(
    account_id: str | None = None,
    *,
    purpose: str | None = None,
    holder: str | None = None,
    parent_thread: str | None = None,
    model: str | None = None,
) -> str:
    """Resolve the effective account id for a CDP ask or registry mint.

    Non-default accounts require a sign-in entry in ``account_sign_in.json``.
    Explicit requests fail closed; config-driven mappings fall back to default.
    """
    resolved, explicit = _resolve_account_id_raw(
        account_id,
        purpose=purpose,
        holder=holder,
        parent_thread=parent_thread,
        model=model,
    )
    if resolved == DEFAULT_ACCOUNT_ID or account_is_signed_in(resolved):
        return resolved
    if explicit:
        raise AccountNotSignedInError(
            f"account {resolved!r} is not signed in; refusing routed ask"
        )
    return DEFAULT_ACCOUNT_ID


def assert_profile_suffix_available(
    active: dict[str, Any],
    profile_suffix: str,
    *,
    registration_id: str | None = None,
) -> None:
    """Refuse a second live host row on the same persistent profile suffix."""
    suffix = str(profile_suffix or "").strip()
    if not suffix:
        return
    for rid, row in active.items():
        if not isinstance(row, dict):
            continue
        if registration_id and str(rid) == str(registration_id):
            continue
        if str(row.get("profile_suffix") or "") != suffix:
            continue
        if row.get("status") not in _LIVE_PROFILE_SUFFIX_STATUSES:
            continue
        raise RegistryError(
            f"profile suffix {suffix!r} already reserved by live row {rid!r}"
        )


class JoinedReentryExhausted(RegistryError):  # noqa: N818 — spec-bound name, not an Error suffix
    """Raised when a nested join wait would reserve or wait a second time."""


class RegistryBusyError(RegistryError):
    """Raised when a second driver attempts to attach a registration already held by another driver."""


class SeatContended(RegistryError):  # noqa: N818 — spec-bound name, not an Error suffix
    """Raised when a row's status changed before this caller could claim it."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "seat.contended",
        source: Literal["rpc", "stream", "engine", "gateway", "edge", "master"] = "rpc",
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ) -> None:
        envelope = error_envelope(
            code=code,
            message=message,
            source=source,
            retryable=retryable,
            data=data,
        )
        self.code = str(envelope["code"])
        self.message = str(envelope["message"])
        self.source = envelope["source"]
        self.retryable = bool(envelope["retryable"])
        self.data = dict(envelope.get("data") or {})
        super().__init__(message)


class RegistryExhaustedError(RegistryError):
    """Raised when the configured CDP registry port pool has no free port remaining for a new host."""


class OperatorSeatDeferred(RegistryError):  # noqa: N818 — spec-bound name
    """Driving-seat mint refused for X headroom while an eligible host exists."""

    def __init__(
        self,
        message: str,
        *,
        display: str,
        host_registration_id: str,
        branch: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "display": display,
            "host_registration_id": host_registration_id,
            "branch": branch,
            **(data or {}),
        }
        envelope = error_envelope(
            code="cdp.operator_seat_deferred",
            message=message,
            source="rpc",
            retryable=False,
            data=payload,
        )
        self.code = str(envelope["code"])
        self.message = str(envelope["message"])
        self.display = display
        self.host_registration_id = host_registration_id
        self.branch = branch
        self.data = dict(envelope.get("data") or {})
        super().__init__(message)


@dataclass(frozen=True)
class Registration:
    """One listable Chrome host projected from active.json.

    Callers read this instead of the raw row. Seat-axis fields say which lane
    the host drives, when that seat bound and closed, and which registration
    superseded it. Absent seat fields stay None for rows minted before the axis.
    """

    registration_id: str
    port: int
    profile_suffix: str
    profile: Path
    cdp_url: str
    holder: str
    purpose: str | None = None
    display: str | None = None
    mission_kind: str | None = None
    parent_thread: str | None = None
    started_at: float | None = None
    execution_id: str | None = None
    seat_lane: str | None = None
    seat_bound_at: float | None = None
    seat_closed_at: float | None = None
    seat_close_reason: str | None = None
    superseded_by: str | None = None
    host_registration_id: str | None = None
    target_id: str | None = None
    tab_kept_reason: str | None = None
    account_id: str | None = None


@dataclass(frozen=True)
class DormantSeat:
    """A CSE whose Chrome process is gone while its URL and profile persist.

    Deliberately carries no ``cdp_url``: the last port is historical evidence and
    may already belong to another host, so a consumer must relaunch to get one.
    """

    registration_id: str
    chat_url: str
    profile_suffix: str
    profile: Path
    holder: str
    purpose: str | None = None
    mission_kind: str | None = None
    parent_thread: str | None = None
    dormant_at: float | None = None
    last_port: int | None = None
    account_id: str | None = None


@dataclass(frozen=True)
class HygieneReclaimResult:
    reclaimed_ports: list[int]
    removed_profiles: list[str]


def dormant_ttl_s() -> float:
    """Age after which a dormant row's profile is reclaimed (``CDP_DORMANT_TTL_S``)."""
    return _positive_env_float("CDP_DORMANT_TTL_S", _DEFAULT_DORMANT_TTL_S)


def dormant_max_rows() -> int:
    """Dormant row cap before oldest profiles are reclaimed (``CDP_DORMANT_MAX_ROWS``)."""
    return int(_positive_env_float("CDP_DORMANT_MAX_ROWS", _DEFAULT_DORMANT_MAX_ROWS))


def seat_open(row: dict[str, Any], lane: str | None = None) -> bool:
    """Return True when *row* holds an unclosed seat, optionally for *lane*.

    ``seat_open(row, L) ≡ row.seat_lane == L ∧ row.seat_closed_at is None``.
    Status is irrelevant: a dormant row may still be the driving seat.
    """
    seat_lane = str(row.get("seat_lane") or "").strip()
    if not seat_lane:
        return False
    if row.get("seat_closed_at") is not None:
        return False
    if lane is not None and seat_lane != str(lane).strip():
        return False
    return True


def _positive_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _row_to_dormant_seat(row: dict[str, Any]) -> DormantSeat:
    from claude_bundles import cdp_lane

    suffix = str(row["profile_suffix"])
    port = row.get("port")
    return DormantSeat(
        registration_id=str(row["registration_id"]),
        chat_url=str(row.get("chat_url") or ""),
        profile_suffix=suffix,
        profile=cdp_lane.profile_for(suffix),
        holder=str(row["holder"]),
        purpose=row.get("purpose"),
        mission_kind=row.get("mission_kind"),
        parent_thread=row.get("parent_thread"),
        dormant_at=row.get("dormant_at"),
        last_port=port if isinstance(port, int) else None,
        account_id=_optional_str(row.get("account_id")),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_to_registration(row: dict[str, Any]) -> Registration:
    from claude_bundles import cdp_lane

    suffix = str(row["profile_suffix"])
    port = int(row["port"])
    return Registration(
        registration_id=str(row["registration_id"]),
        port=port,
        profile_suffix=suffix,
        profile=cdp_lane.profile_for(suffix),
        cdp_url=f"http://127.0.0.1:{port}",
        holder=str(row["holder"]),
        purpose=row.get("purpose"),
        display=row.get("display"),
        mission_kind=row.get("mission_kind"),
        parent_thread=row.get("parent_thread"),
        started_at=_optional_float(row.get("started_at")),
        execution_id=_optional_str(row.get("execution_id")),
        seat_lane=_optional_str(row.get("seat_lane")),
        seat_bound_at=_optional_float(row.get("seat_bound_at")),
        seat_closed_at=_optional_float(row.get("seat_closed_at")),
        seat_close_reason=_optional_str(row.get("seat_close_reason")),
        superseded_by=_optional_str(row.get("superseded_by")),
        host_registration_id=_optional_str(row.get("host_registration_id")),
        target_id=_optional_str(row.get("target_id")),
        tab_kept_reason=_optional_str(row.get("tab_kept_reason")),
        account_id=_optional_str(row.get("account_id")),
    )
