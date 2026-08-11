"""Admission for symmetric ``## options`` menus on DIRECTIVE bodies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from implement_admission.options_block_parser import parse_options_block

from services.git_integration_worker.cursor_auto.fix_hints import (
    OPTIONS_SYMMETRY_FIX_HINT,
)

REQUIRED_OPTION_KEYS = frozenset({"cost", "benefit", "falsifier"})


@dataclass(frozen=True, slots=True)
class OptionsAdmission:
    """Verdict for one DIRECTIVE body's options block."""

    error: dict[str, Any] | None = None

    @property
    def approved(self) -> bool:
        return self.error is None


def _error(reason: str, summary: str, fix_hint: str, **extra: Any) -> dict[str, Any]:
    return {"reason": reason, "summary": summary, "fix_hint": fix_hint, **extra}


def _missing_required_key(
    option_id: str,
    key: str,
) -> OptionsAdmission:
    return OptionsAdmission(
        error=_error(
            "options_required_key_missing",
            f"Option `{option_id}` is missing required key `{key}`.",
            f"Add `{key}:` to option `{option_id}` (required on every menu option). "
            f"{OPTIONS_SYMMETRY_FIX_HINT}",
            option_id=option_id,
            missing_key=key,
        )
    )


def _empty_required_value(option_id: str, key: str) -> OptionsAdmission:
    return OptionsAdmission(
        error=_error(
            "options_required_key_empty",
            f"Option `{option_id}` has empty required key `{key}`.",
            f"Give `{key}:` a non-empty value on option `{option_id}`. "
            f"{OPTIONS_SYMMETRY_FIX_HINT}",
            option_id=option_id,
            missing_key=key,
        )
    )


def _asymmetric_keys(
    option_id: str,
    missing_key: str,
    *,
    reason: str,
    summary: str,
) -> OptionsAdmission:
    return OptionsAdmission(
        error=_error(
            reason,
            summary,
            f"Add `{missing_key}:` to option `{option_id}` so every option carries the "
            f"same key set. {OPTIONS_SYMMETRY_FIX_HINT}",
            option_id=option_id,
            missing_key=missing_key,
        )
    )


def _validate_required_values(
    options: list[tuple[str, dict[str, Any]]],
) -> OptionsAdmission | None:
    for option_id, mapping in options:
        for key in REQUIRED_OPTION_KEYS:
            if key not in mapping:
                return _missing_required_key(option_id, key)
            value = mapping[key]
            if value is None:
                return _empty_required_value(option_id, key)
            if isinstance(value, str) and not value.strip():
                return _empty_required_value(option_id, key)
    return None


def _validate_key_symmetry(
    options: list[tuple[str, dict[str, Any]]],
) -> OptionsAdmission | None:
    key_sets = [frozenset(mapping.keys()) for _, mapping in options]
    reference = key_sets[0]
    if all(keys == reference for keys in key_sets):
        return None
    union_keys = set().union(*key_sets)
    for option_id, mapping in options:
        missing = union_keys - set(mapping.keys())
        if missing:
            key = sorted(missing)[0]
            return _asymmetric_keys(
                option_id,
                key,
                reason="options_key_set_asymmetric",
                summary=(
                    f"Option `{option_id}` is missing key `{key}` present on sibling "
                    "options."
                ),
            )
    return None


def admit_options_body(body: str) -> OptionsAdmission:
    """Refuse loaded or malformed ``## options`` menus before nest dispatch."""
    rows, parse_error, block_present = parse_options_block(body)
    if not block_present:
        return OptionsAdmission()
    if parse_error is not None:
        return OptionsAdmission(
            error=_error(
                "options_block_unparseable",
                f"Options block could not be parsed: {parse_error}",
                f"Fix the YAML under ``## options`` ({parse_error}) and re-issue. "
                f"{OPTIONS_SYMMETRY_FIX_HINT}",
                parse_error=parse_error,
            )
        )
    if not rows:
        return OptionsAdmission(
            error=_error(
                "options_block_empty",
                "Options block present but listed zero options.",
                f"Add at least two symmetric options or remove the block. "
                f"{OPTIONS_SYMMETRY_FIX_HINT}",
            )
        )
    if len(rows) == 1:
        option_id, _ = rows[0]
        return OptionsAdmission(
            error=_error(
                "options_single_option",
                f"Options block lists exactly one option (`{option_id}`).",
                f"Remove the menu or add a second option with the same keys as "
                f"`{option_id}`. {OPTIONS_SYMMETRY_FIX_HINT}",
                option_id=option_id,
            )
        )
    required_error = _validate_required_values(rows)
    if required_error is not None:
        return required_error
    symmetry_error = _validate_key_symmetry(rows)
    if symmetry_error is not None:
        return symmetry_error
    return OptionsAdmission()


__all__ = [
    "OptionsAdmission",
    "REQUIRED_OPTION_KEYS",
    "admit_options_body",
]
