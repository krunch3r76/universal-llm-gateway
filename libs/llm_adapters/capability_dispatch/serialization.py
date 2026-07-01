"""Wire serializer for ``CapabilityDispatch`` — the libs-resident projection seam.

``to_wire_dict`` turns a resolved :class:`CapabilityDispatch` dataclass into the
plain JSON-able dict that rides ``/v1/models`` (the ``dispatch`` facet). It is the
SINGLE canonical serializer reused by the cloud-catalog owner
(``universal_cloud_proxy``); cloud_proxy and stargate resolve + serialize through
libs and MUST NOT import the gateway service schema ([universal:libs-first]).

The wire shape is pinned EXACTLY to the gateway pydantic mirror's
``CapabilityDispatchFacet.from_dispatch(d).model_dump(mode="json", exclude_none=True)``
(``services/_universal-llm-gateway/src/schemas/capabilities.py``). That facet stays
the parity ORACLE (drift-guarded by the gateway-side parity test), never the runtime
path. Consequences of the pinned shape, reproduced here verbatim:

- ``reasoning is None`` / ``specializations is None`` -> key ABSENT.
- a knob whose ``default is OMIT`` (or genuinely ``None``) -> ``default`` ABSENT.
- ``max_output.ceiling`` / ``floor`` == ``None`` -> ABSENT (recursive exclude-none):
  Responses/Google carry ``max_output`` without ceiling/floor; Anthropic carries a
  ceiling and no floor.
- ``over_ceiling`` is a non-None default ("clamp") -> ALWAYS present (no
  exclude-defaults).
- tuples (``accepted_values``, knob ``accepted``, ``unsupported_values``) -> lists;
  ``budget_map`` preserved.
- ``params`` is ``{}`` (not None) for a registry ``resolve()`` output -> wire carries
  ``"params": {}``.
"""

from __future__ import annotations

from typing import Any

from .types import OMIT, CapabilityDispatch, KnobSpec


def _knob_to_wire(spec: KnobSpec) -> dict[str, Any]:
    """Serialize one declared knob; OMIT/None default and None accepted drop out."""
    wire: dict[str, Any] = {"name": spec.name}
    if spec.accepted is not None:
        wire["accepted"] = list(spec.accepted)
    if spec.default is not OMIT and spec.default is not None:
        wire["default"] = spec.default
    return wire


def to_wire_dict(dispatch: CapabilityDispatch) -> dict[str, Any]:
    """Serialize a resolved ``CapabilityDispatch`` to the ``/v1/models`` wire dict.

    Matches ``CapabilityDispatchFacet.from_dispatch(dispatch).model_dump(
    mode="json", exclude_none=True)`` exactly (see module docstring).
    """
    mo = dispatch.max_output
    max_output: dict[str, Any] = {
        "default": mo.default,
        "native_field": mo.native_field,
        "over_ceiling": mo.over_ceiling,
    }
    if mo.ceiling is not None:
        max_output["ceiling"] = mo.ceiling
    if mo.floor is not None:
        max_output["floor"] = mo.floor

    wire: dict[str, Any] = {
        "api_surface": dispatch.api_surface,
        "max_output": max_output,
        "params": {name: _knob_to_wire(spec) for name, spec in dispatch.params.items()},
    }

    if dispatch.reasoning is not None:
        r = dispatch.reasoning
        reasoning: dict[str, Any] = {
            "native_field_path": r.native_field_path,
            "value_kind": r.value_kind,
            "accepted_values": list(r.accepted_values),
        }
        if r.default is not None:
            reasoning["default"] = r.default
        if r.budget_map is not None:
            reasoning["budget_map"] = dict(r.budget_map)
        wire["reasoning"] = reasoning

    if dispatch.specializations is not None:
        s = dispatch.specializations
        specializations: dict[str, Any] = {
            "unsupported_values": [list(uv) for uv in s.unsupported_values],
        }
        if s.behavioral_note is not None:
            specializations["behavioral_note"] = s.behavioral_note
        if s.evidence_uri is not None:
            specializations["evidence_uri"] = s.evidence_uri
        wire["specializations"] = specializations

    return wire


def _knob_card_entry(spec: KnobSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": spec.name}
    if spec.accepted is not None:
        entry["accepted"] = list(spec.accepted)
    if spec.default is not OMIT and spec.default is not None:
        entry["default"] = spec.default
    return entry


def to_model_card_dict(dispatch: CapabilityDispatch) -> dict[str, Any]:
    """Neutral model-card projection for the cloud-api substrate.

    Shared key vocabulary: ``knobs``, ``fixed_params``, and ``api_surface`` only
    where genuinely cross-substrate. Distinct from ``to_wire_dict`` which stays
    scoped to provider HTTP request body projection.
    """
    return {
        "api_surface": dispatch.api_surface,
        "knobs": {
            name: _knob_card_entry(spec) for name, spec in dispatch.params.items()
        },
        "fixed_params": {},
    }
