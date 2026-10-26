"""Policy stamp: explicit ≻ todo attrs ≻ route_policy.yaml defaults (§5.1)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

POLICY_TOP_LEVEL_KEYS = frozenset(
    {
        "models",
        "review",
        "retry",
        "gate",
        "land",
        "restart",
        "probe",
        "close",
        "blocked_to",
        "ruling_authors",
    }
)

VALID_REVIEW_ROUTES = frozenset({"bus_request", "check_review"})


class PolicyInvalidError(Exception):
    """Policy override or merge result is not admissible."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def route_policy_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "config"
        / "mechanical_land"
        / "route_policy.yaml"
    )


def load_route_policy_defaults() -> dict[str, Any]:
    path = route_policy_path()
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise PolicyInvalidError("policy_invalid", f"defaults not a mapping: {path}")
    return data


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _todo_policy_slice(todo_attrs: dict[str, Any] | None) -> dict[str, Any]:
    if not todo_attrs:
        return {}
    out: dict[str, Any] = {}
    if todo_attrs.get("required_skills"):
        out.setdefault("models", {})
    if todo_attrs.get("files_expected"):
        out["files_expected"] = list(todo_attrs["files_expected"])
    return out


def validate_policy_override(override: dict[str, Any] | None) -> None:
    if not override:
        return
    unknown = set(override) - POLICY_TOP_LEVEL_KEYS
    if unknown:
        raise PolicyInvalidError(
            "policy_invalid",
            f"unknown policy keys: {sorted(unknown)}",
        )
    restart = override.get("restart")
    if isinstance(restart, dict):
        for forbidden, label in (
            ("wait_for_boundary", "restart.wait_for_boundary"),
            ("force_giw", "restart.force_giw"),
            ("propagate", "restart.propagate"),
        ):
            if restart.get(forbidden) is True:
                raise PolicyInvalidError("policy_invalid", f"{label}: true refused")
    review = override.get("review")
    if isinstance(review, dict):
        order = review.get("route_order")
        if order is not None:
            if not isinstance(order, list):
                raise PolicyInvalidError(
                    "policy_invalid", "review.route_order must be a list"
                )
            bad = [r for r in order if r not in VALID_REVIEW_ROUTES]
            if bad:
                raise PolicyInvalidError(
                    "policy_invalid",
                    f"review.route_order invalid elements: {bad}",
                )


def canonical_policy_json(policy: dict[str, Any]) -> str:
    return json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def policy_sha256(policy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_policy_json(policy).encode("utf-8")).hexdigest()


def stamp_policy(
    *,
    explicit: dict[str, Any] | None = None,
    todo_attrs: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Merge explicit ≻ todo attrs ≻ defaults; return policy and stable sha256."""
    validate_policy_override(explicit)
    base = copy.deepcopy(defaults or load_route_policy_defaults())
    merged = _deep_merge(base, _todo_policy_slice(todo_attrs))
    if explicit:
        validate_policy_override(explicit)
        merged = _deep_merge(merged, explicit)
    _validate_merged_restart_and_routes(merged)
    return merged, policy_sha256(merged)


def _validate_merged_restart_and_routes(policy: dict[str, Any]) -> None:
    restart = policy.get("restart") or {}
    if restart.get("wait_for_boundary") is True:
        raise PolicyInvalidError(
            "policy_invalid", "restart.wait_for_boundary: true refused"
        )
    if restart.get("force_giw") is True:
        raise PolicyInvalidError("policy_invalid", "restart.force_giw: true refused")
    if restart.get("propagate") is True:
        raise PolicyInvalidError("policy_invalid", "restart.propagate: true refused")
    review = policy.get("review") or {}
    order = review.get("route_order") or []
    bad = [r for r in order if r not in VALID_REVIEW_ROUTES]
    if bad:
        raise PolicyInvalidError(
            "policy_invalid",
            f"review.route_order invalid elements: {bad}",
        )
