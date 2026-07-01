"""doc_validate PASS attestation tokens and side-effect guard evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from implement_admission.dense_spec_schema import (
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.implement_ready_preflight import preflight_implement_ready

_DOC_VALIDATE_PASS_TOKEN = "doc_validate:pass"
_TEMPLATE_VERSION_PREFIX = "template_version:"
_SKILL_DIGEST_PREFIX = "skill_digest:"
_SPEC_SHA256_PREFIX = "spec_sha256:"
_DEFAULT_DOC_TYPE = "implement_dense_spec"


@dataclass(frozen=True, slots=True)
class DocValidateAttestation:
    spec_sha256: str
    template_version: str | None
    skill_digest: str | None
    has_pass_token: bool


@dataclass(frozen=True, slots=True)
class DocValidateAttestationVerdict:
    admitted: bool
    code: str | None = None
    reason: str | None = None


def extract_spec_sha256_token(evidence: list[str] | None) -> str | None:
    if not evidence:
        return None
    for entry in evidence:
        if isinstance(entry, str) and entry.startswith(_SPEC_SHA256_PREFIX):
            return entry
    return None


def extract_doc_validate_attestation(
    evidence: list[str] | None,
) -> DocValidateAttestation | None:
    if not evidence:
        return None
    spec_sha256 = extract_spec_sha256_token(evidence)
    if spec_sha256 is None:
        return None
    template_version: str | None = None
    skill_digest: str | None = None
    has_pass_token = False
    for entry in evidence:
        if not isinstance(entry, str):
            continue
        if entry == _DOC_VALIDATE_PASS_TOKEN:
            has_pass_token = True
        elif entry.startswith(_TEMPLATE_VERSION_PREFIX):
            template_version = entry.removeprefix(_TEMPLATE_VERSION_PREFIX).strip() or None
        elif entry.startswith(_SKILL_DIGEST_PREFIX):
            skill_digest = entry.removeprefix(_SKILL_DIGEST_PREFIX).strip() or None
    return DocValidateAttestation(
        spec_sha256=spec_sha256,
        template_version=template_version,
        skill_digest=skill_digest,
        has_pass_token=has_pass_token,
    )


def doc_validate_attestation_tokens(
    *,
    doc_type: str = _DEFAULT_DOC_TYPE,
    spec_text: str,
) -> list[str]:
    """Evidence tokens to cite after doc_validate PASS (Gate-2 / implement-ready)."""
    from cortex_store.dispatch_ops.adapters._doc_template import (  # noqa: PLC0415
        current_skill_digest,
        current_template_version,
    )

    version = current_template_version(doc_type) or "0.0.0"
    skill_digest = current_skill_digest(doc_type)
    tokens = [
        _DOC_VALIDATE_PASS_TOKEN,
        f"{_TEMPLATE_VERSION_PREFIX}{version}",
        dense_spec_hash_uri(spec_text),
    ]
    if skill_digest:
        tokens.append(f"{_SKILL_DIGEST_PREFIX}{skill_digest}")
    return tokens


def _template_version_compatible(
    attested: str | None,
    current: str | None,
) -> bool:
    """Semantic compatibility: older attested versions remain valid when validator PASSes."""
    if not attested or not current:
        return True
    if attested == current:
        return True

    def _parts(value: str) -> tuple[int, ...]:
        out: list[int] = []
        for piece in value.split("."):
            try:
                out.append(int(piece))
            except ValueError:
                out.append(0)
        return tuple(out)

    att_parts = _parts(attested)
    cur_parts = _parts(current)
    width = max(len(att_parts), len(cur_parts))
    att_parts = att_parts + (0,) * (width - len(att_parts))
    cur_parts = cur_parts + (0,) * (width - len(cur_parts))
    return att_parts <= cur_parts


def _live_doc_validate_passes(
    *,
    spec_text: str,
    preflight_kwargs: dict[str, Any],
) -> bool:
    """Aggregate doc_validate PASS without entering the side-effect guard."""
    schema = validate_dense_spec(spec_text)
    if not schema.passed:
        return False
    report = preflight_implement_ready(**preflight_kwargs)
    return bool(report.admitted)


def evaluate_doc_validate_attestation(
    *,
    doc_type: str = _DEFAULT_DOC_TYPE,
    spec_text: str | None,
    evidence_uris: list[str] | None,
    preflight_kwargs: dict[str, Any] | None = None,
    skip_side_effect_guard: bool = False,
) -> DocValidateAttestationVerdict:
    """Verify doc_validate PASS attestation for implement side-effect admission."""
    if skip_side_effect_guard:
        return DocValidateAttestationVerdict(admitted=True)

    if spec_text is None:
        return DocValidateAttestationVerdict(
            admitted=False,
            code="doc_validate_spec_unreadable",
            reason="dense spec bytes could not be resolved for doc_validate attestation",
        )

    live_sha = dense_spec_hash_uri(spec_text)
    attestation = extract_doc_validate_attestation(evidence_uris)
    if attestation is None or attestation.spec_sha256 != live_sha:
        return DocValidateAttestationVerdict(
            admitted=False,
            code="doc_validate_attestation_missing",
            reason=(
                "doc_validate PASS attestation required — cite doc_validate:pass, "
                f"template_version, and matching {live_sha} on the implement-ready "
                "assertion before implement dispatch"
            ),
        )

    from cortex_store.dispatch_ops.adapters._doc_template import (  # noqa: PLC0415
        current_skill_digest,
        current_template_version,
    )

    current_version = current_template_version(doc_type)
    if not _template_version_compatible(attestation.template_version, current_version):
        return DocValidateAttestationVerdict(
            admitted=False,
            code="doc_validate_template_version_incompatible",
            reason=(
                f"attested template_version {attestation.template_version!r} is not "
                f"compatible with current {current_version!r}"
            ),
        )

    kwargs = preflight_kwargs or {}
    if not _live_doc_validate_passes(spec_text=spec_text, preflight_kwargs=kwargs):
        return DocValidateAttestationVerdict(
            admitted=False,
            code="doc_validate_not_passing",
            reason=(
                "live doc_validate preflight does not PASS — re-run doc_validate and "
                "refresh attestation tokens before implement dispatch"
            ),
        )

    _ = current_skill_digest(doc_type)
    return DocValidateAttestationVerdict(admitted=True)


__all__ = [
    "DocValidateAttestation",
    "DocValidateAttestationVerdict",
    "doc_validate_attestation_tokens",
    "evaluate_doc_validate_attestation",
    "extract_doc_validate_attestation",
    "extract_spec_sha256_token",
]
