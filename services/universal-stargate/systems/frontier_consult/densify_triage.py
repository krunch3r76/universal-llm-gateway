"""Densify density triage discriminator and generate-path intake validation."""

from __future__ import annotations

from typing import Any, Literal, get_args

from .admission import FrontierEndpointError

DensityTriage = Literal[
    "mechanical",
    "judgment_required",
    "cross_cutting",
    "dispatch_surface",
    "admission_path",
    "trivial",
    "recon_pending",
]

DraftAdequacy = Literal["blank", "partial", "adequate"]

VALID_DENSITY_TRIAGE = frozenset(get_args(DensityTriage))

DENSIFY_DEFAULT_ON = frozenset(
    {
        "judgment_required",
        "cross_cutting",
        "dispatch_surface",
        "admission_path",
    }
)

REVIEW_OPT_OUT_REASON_CODES = frozenset(
    {
        "routine_single_subsystem",
        "suggestion_only_first_pass",
        "cost_exceeds_false_negative_risk",
    }
)

VALID_DRAFT_ADEQUACY = frozenset({"blank", "partial", "adequate"})

COMPOSER_DRAFT_SENTINEL = "COMPOSER_DRAFT_SENTINEL"
REASONING_TRACE_SENTINEL = "REASONING_TRACE_SENTINEL"
SEED_ONLY_SENTINEL = "SEED_ONLY_SENTINEL"


def is_default_on_density(density_triage: str | None) -> bool:
    return density_triage in DENSIFY_DEFAULT_ON


def validate_density_triage_value(
    density_triage: str | None,
    *,
    request_id: str,
) -> None:
    if density_triage is None:
        return
    if density_triage not in VALID_DENSITY_TRIAGE:
        accepted = ", ".join(sorted(VALID_DENSITY_TRIAGE))
        raise FrontierEndpointError(
            request_id=request_id,
            field="density_triage",
            reason=(
                f"unknown density_triage value: {density_triage!r} — "
                f"accepted: {accepted}"
            ),
            status_code=422,
            code="density_triage_unknown",
        )


def validate_generate_density_intake(
    *,
    request_id: str,
    contract: str,
    density_triage: str | None,
    review_opt_out_reason_code: str | None,
    auto_review_child: bool,
) -> None:
    """Step B3 / C1b negative-space matrix for generate + to_thread intake."""
    validate_density_triage_value(density_triage, request_id=request_id)

    if review_opt_out_reason_code is not None:
        if review_opt_out_reason_code not in REVIEW_OPT_OUT_REASON_CODES:
            raise FrontierEndpointError(
                request_id=request_id,
                field="review_opt_out_reason_code",
                reason=(
                    f"unknown review_opt_out_reason_code: "
                    f"{review_opt_out_reason_code!r}"
                ),
                status_code=422,
                code="review_opt_out_unknown",
            )
        if auto_review_child:
            raise FrontierEndpointError(
                request_id=request_id,
                field="review_opt_out_reason_code",
                reason="opt-out is invalid when auto_review_child=true",
                status_code=422,
                code="review_opt_out_child_lane",
            )
        if not is_default_on_density(density_triage):
            raise FrontierEndpointError(
                request_id=request_id,
                field="review_opt_out_reason_code",
                reason="opt-out is only valid for default-on density_triage",
                status_code=422,
                code="review_opt_out_non_default_on",
            )

    if contract == "pure-mechanical" and is_default_on_density(density_triage):
        raise FrontierEndpointError(
            request_id=request_id,
            field="density_triage",
            reason="pure-mechanical contract conflicts with default-on density_triage",
            status_code=422,
            code="density_triage_mechanical_conflict",
        )

    if contract == "implement" and is_default_on_density(density_triage):
        raise FrontierEndpointError(
            request_id=request_id,
            field="density_triage",
            reason=(
                "implement lane never default-reviews; density_triage must be "
                "mechanical, recon_pending, trivial, or unset"
            ),
            status_code=422,
            code="density_triage_implement_conflict",
        )


_AUTO_REVIEW_CHILD_WARNING = (
    "auto_review_child_not_honored:spawn_path_unimplemented "
    "(tracking: todo:auto-review-child-spawn-path-generate-lane)"
)


def build_generate_review_envelope(
    *,
    density_triage: str | None,
    review_opt_out_reason_code: str | None,
    auto_review_child: bool,
) -> dict[str, Any]:
    """Present-and-null ``recommended_review`` plus opt-out audit fields."""
    from .executor_resolution import derive_generate_review
    from .generate_admission_context_store import is_generate_review_child_lane_wired

    if auto_review_child:
        value = derive_generate_review(density_triage, auto_review_child=False)
        envelope: dict[str, Any] = {
            "recommended_review": value,
            "auto_review_child_requested": True,
            "auto_review_spawned": False,
        }
        if not is_generate_review_child_lane_wired():
            envelope["auto_review_child_warning"] = _AUTO_REVIEW_CHILD_WARNING
    else:
        value = derive_generate_review(density_triage, auto_review_child=False)
        envelope = {"recommended_review": value}
    if review_opt_out_reason_code is not None and is_default_on_density(density_triage):
        envelope["recommended_review"] = "cross-family-reconcile:default-on"
        envelope["review_opted_out"] = True
        envelope["review_opt_out_reason_code"] = review_opt_out_reason_code
        envelope["auto_review_spawned"] = False
    return envelope
