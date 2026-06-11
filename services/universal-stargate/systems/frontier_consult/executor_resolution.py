"""Server-side executor + consult-review advisories for handoff admission."""

from __future__ import annotations

import re
from typing import Any

from implement_admission.admission_read import frontmatter_value

_COMPOSER_VARIANTS = frozenset({"composer", "composer-fast", "composer-thinking"})
_VALID_GAP_REASON_CODES = frozenset({"capability_gap", "protocol_heavy"})
_WEB_INLINE = "web-inline"
_PURE_CORTEX_DOC_EDIT = "pure_cortex_doc_edit"
_DESIGN_JUDGMENT_REMAINING = "design_judgment_remaining"

_CONSULT_REVIEW_DEFAULT = "cross-family-reconcile:default-on"


def _frontmatter_line_value(text: str, key: str) -> str | None:
    """Read a frontmatter scalar or quoted / rest-of-line string value."""
    from implement_admission.admission_read import _frontmatter_region

    region = _frontmatter_region(text)
    if region is None:
        return None
    quoted = re.search(
        rf'^{re.escape(key)}:\s*"([^"]*)"',
        region,
        flags=re.MULTILINE,
    )
    if quoted:
        return quoted.group(1)
    bare = re.search(
        rf"^{re.escape(key)}:\s*(.+)$",
        region,
        flags=re.MULTILINE,
    )
    if bare:
        return bare.group(1).strip()
    return None


def _read_packet_executor_inputs(
    packet_text: str,
) -> tuple[str | None, str | None, str | None]:
    override = frontmatter_value(packet_text, "executor_override")
    reason_code = frontmatter_value(packet_text, "executor_override_reason_code")
    reason = _frontmatter_line_value(packet_text, "executor_override_reason")
    return override, reason_code, reason


def _empty_executor_fields() -> dict[str, Any]:
    return {
        "recommended_executor": None,
        "recommended_executor_source": None,
        "recommended_executor_reason_code": None,
        "recommended_executor_reason": None,
        "executor_bindable": None,
        "executor_policy_warnings": [],
    }


def _composer_fields(
    *,
    source: str,
    warnings: list[str],
    reason_code: str | None,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "recommended_executor": "composer",
        "recommended_executor_source": source,
        "recommended_executor_reason_code": reason_code,
        "recommended_executor_reason": reason,
        "executor_bindable": True,
        "executor_policy_warnings": warnings,
    }


def derive_recommended_executor(
    handoff_contract: str,
    packet_text: str,
    *,
    executor_override: str | None = None,
    executor_override_reason_code: str | None = None,
    executor_override_reason: str | None = None,
) -> dict[str, Any]:
    """Resolve implement executor advisory from contract + override inputs."""
    if handoff_contract != "implement":
        return _empty_executor_fields()

    fm_override, fm_reason_code, fm_reason = _read_packet_executor_inputs(packet_text)
    override = executor_override if executor_override is not None else fm_override
    reason_code = (
        executor_override_reason_code
        if executor_override_reason_code is not None
        else fm_reason_code
    )
    reason = (
        executor_override_reason if executor_override_reason is not None else fm_reason
    )

    if override is None:
        return _composer_fields(
            source="server_default:contract_implement",
            warnings=[],
            reason_code=None,
            reason=None,
        )

    warnings: list[str] = []

    if reason_code == _DESIGN_JUDGMENT_REMAINING:
        warnings.append(
            "not_mechanical_implement; consider contract=consult or split handoff"
        )
        return _composer_fields(
            source="explicit_override",
            warnings=warnings,
            reason_code=reason_code,
            reason=reason,
        )

    if override in _COMPOSER_VARIANTS:
        if override != "composer":
            warnings.append("composer_variants_collapsed")
        return _composer_fields(
            source="explicit_override",
            warnings=warnings,
            reason_code=reason_code,
            reason=reason,
        )

    if override == _WEB_INLINE:
        if reason_code == _PURE_CORTEX_DOC_EDIT:
            return {
                "recommended_executor": _WEB_INLINE,
                "recommended_executor_source": "explicit_override",
                "recommended_executor_reason_code": reason_code,
                "recommended_executor_reason": reason,
                "executor_bindable": False,
                "executor_policy_warnings": warnings,
            }
        warnings.append("web_inline_requires_pure_cortex_doc_edit")
        return _composer_fields(
            source="coerced",
            warnings=warnings,
            reason_code=reason_code,
            reason=reason,
        )

    if reason_code in _VALID_GAP_REASON_CODES and reason is not None and reason.strip():
        return {
            "recommended_executor": override,
            "recommended_executor_source": "explicit_override",
            "recommended_executor_reason_code": reason_code,
            "recommended_executor_reason": reason,
            "executor_bindable": True,
            "executor_policy_warnings": warnings,
        }

    warnings.append("non_composer_override_coerced")
    return _composer_fields(
        source="coerced",
        warnings=warnings,
        reason_code=reason_code,
        reason=reason,
    )


def derive_recommended_review(handoff_contract: str) -> str | None:
    """Consult adversarial-pass default advisory (Q-sibling dual)."""
    if handoff_contract == "consult":
        return _CONSULT_REVIEW_DEFAULT
    return None


def should_emit_executor_override_audit(
    *,
    handoff_contract: str,
    recommended_executor: str | None,
    override_supplied: bool,
) -> bool:
    if handoff_contract != "implement":
        return False
    if override_supplied:
        return True
    return recommended_executor is not None and recommended_executor != "composer"
