"""Map Glass house knobs onto model ``parameterDefinitions`` ids."""

from __future__ import annotations

from typing import Any


def _def_allowed_values(defn: dict[str, Any]) -> list[str] | None:
    for key in ("enum", "values", "allowedValues"):
        raw = defn.get(key)
        if isinstance(raw, list) and raw:
            return [str(v) for v in raw]
    if str(defn.get("type") or "").lower() == "boolean":
        return ["false", "true"]
    return None


def _suffix_token(def_id: str) -> str:
    normalized = def_id.replace("-", "_")
    parts = [p for p in normalized.split("_") if p]
    return parts[-1] if parts else def_id


def _resolve_knob_def_id(
    knob_key: str,
    defs: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    by_id = {
        str(d["id"]): d for d in defs if isinstance(d, dict) and d.get("id") is not None
    }
    if knob_key in by_id:
        return knob_key, None
    matches = [
        str(d["id"])
        for d in defs
        if isinstance(d, dict)
        and d.get("id") is not None
        and _suffix_token(str(d["id"])) == knob_key
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, f"ambiguous knob {knob_key!r} matches def ids {matches}"
    return None, f"unmapped knob {knob_key!r}"


def map_house_knob_parameters(
    defs: list[dict[str, Any]],
    knobs: dict[str, str],
    existing_parameters: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Resolve house knobs to catalog parameter ids and build the payload list.

    ``defs`` entries are Glass ``parameterDefinitions`` objects (``id`` plus optional
    enum/boolean constraints). ``knobs`` holds house keys such as ``effort`` and
    ``fast``. ``existing_parameters`` is the catalog or label-derived list to merge
    before applying knobs.

    Returns ``(parameters, warnings)`` where each parameter is ``{id, value}`` with
    string values, and warnings describe omitted knobs or missing definitions.
    """
    warnings: list[str] = []
    params: list[dict[str, str]] = []
    for entry in existing_parameters or []:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        val = entry.get("value")
        if val is None or str(val).strip() == "":
            continue
        params.append({"id": str(entry["id"]), "value": str(val)})

    valid_def_ids = {
        str(d["id"]) for d in defs if isinstance(d, dict) and d.get("id") is not None
    }
    if not valid_def_ids:
        warnings.append("missing parameterDefinitions for model")
        return params, warnings

    params = [p for p in params if p["id"] in valid_def_ids]

    def set_param(param_id: str, value: str) -> None:
        entry = {"id": param_id, "value": value}
        for idx, existing in enumerate(params):
            if existing["id"] == param_id:
                params[idx] = entry
                return
        params.append(entry)

    for knob_key, raw_value in knobs.items():
        if raw_value is None or str(raw_value).strip() == "":
            continue
        value = str(raw_value).strip()
        def_id, map_warn = _resolve_knob_def_id(knob_key, defs)
        if map_warn:
            warnings.append(map_warn)
            continue
        assert def_id is not None
        defn = next(
            (d for d in defs if isinstance(d, dict) and str(d.get("id")) == def_id),
            {},
        )
        allowed = _def_allowed_values(defn)
        if allowed is not None and value not in allowed:
            warnings.append(
                f"knob {knob_key!r} value {value!r} outside allowed {allowed}"
            )
            continue
        set_param(def_id, value)

    return params, warnings
