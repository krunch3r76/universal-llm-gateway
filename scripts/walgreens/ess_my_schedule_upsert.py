#!/usr/bin/env python3
"""Project an ESS My Schedule harvest JSON onto event:walgreens-shift-*.

    $HOME/.venvs/universal/bin/python scripts/walgreens/ess_my_schedule_upsert.py \\
      --from /tmp/ess-my-schedule.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

_ADDR_RE = re.compile(
    r"(\d{1,6} [^,]+, [A-Za-z .]+, [A-Z]{2} \d{5})"
)

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

_HARVEST_URI = "cortex://notes/personal/kaywan/ess-my-schedule-harvest.json"
_RUNBOOK_URI = "cortex://notes/runbooks/walgreens-sync-shift.md"
_PERSON = "person:kaywan-mansubi"
_TZ = "America/Los_Angeles"


def _q(entity_id: str) -> str:
    return urllib.parse.quote(entity_id, safe=":")


def _req(client, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    resp = client.request(method, path, json=body) if body is not None else client.request(
        method, path
    )
    try:
        data = resp.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {"items": data} if isinstance(data, list) else {"raw": data}
    return resp.status_code, data


def _scheduled_days(harvest: dict) -> list[dict]:
    days = []
    for week in harvest["weeks"]:
        days.extend(day for day in week["days"] if day.get("scheduled"))
    return days


def _window(harvest: dict) -> tuple[str, str]:
    dates = [day["date"] for week in harvest["weeks"] for day in week["days"]]
    return min(dates), max(dates)


def _org(client, store_number: str) -> dict | None:
    eid = f"organization:walgreens-{store_number}"
    status, body = _req(client, "GET", f"/entities/{_q(eid)}?intent=full")
    if status == 200:
        return body
    return None


def _city(org: dict | None) -> str | None:
    if not org:
        return None
    name = org.get("name") or ""
    if " — " in name:
        tail = name.split(" — ", 1)[1]
        return tail.split("(")[0].split("·")[0].strip() or None
    return None


def _street(org: dict | None) -> str | None:
    if not org:
        return None
    attrs = org.get("attributes") or {}
    for key in ("address", "street", "location"):
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    match = _ADDR_RE.search(org.get("description") or "")
    if match:
        return match.group(1)
    return None


def _payload(day: dict, org: dict | None, harvest_at: str) -> dict:
    store = day["store_number"]
    city = _city(org)
    street = _street(org) or day["location_label"]
    title = (
        f"Walgreens #{store} — {city} · {day['date']} {day['start_local']}–{day['end_local']}"
        if city
        else (
            f"Walgreens #{store} · {day['date']} "
            f"{day['start_local']}–{day['end_local']}"
        )
    )
    desc = (
        f"Pharmacist shift. Store {day['store_code']}. {street}. "
        f"{day['start_local']}–{day['end_local']} {_TZ}. "
        f"Projected from ESS My Schedule harvest {harvest_at}."
    )
    attrs = {
        "kind": "walgreens_pharmacist_shift",
        "store_number": store,
        "date": day["date"],
        "start_local": day["start_local"],
        "end_local": day["end_local"],
        "timezone": _TZ,
        "location": street,
        "calendar_title": f"Work - Walgreens {day['store_code']} ({day['location_label']})",
        "roster_state": "scheduled",
        "ess_location_label": day["location_label"],
        "roster_source": "ess_my_schedule",
    }
    if org:
        attrs["store_entity"] = org["id"]
    return {
        "id": f"event:walgreens-shift-{day['date']}",
        "type": "event",
        "name": title,
        "description": desc,
        "workflow_state": "scheduled",
        "source_uri": _HARVEST_URI,
        "attributes": attrs,
    }


def _same_roster(existing: dict, payload: dict) -> bool:
    left = existing.get("attributes") or {}
    right = payload["attributes"]
    keys = ("store_number", "date", "start_local", "end_local", "roster_state")
    return all(left.get(key) == right.get(key) for key in keys)


def _claim(payload: dict) -> str:
    attrs = payload["attributes"]
    store = attrs["store_number"]
    return (
        f"Pharmacist shift at organization:walgreens-{store} on {attrs['date']} "
        f"{attrs['start_local']}–{attrs['end_local']} PT, {attrs['location']}."
    )


def _ensure_rels(client, event_id: str, org_id: str | None) -> None:
    _req(
        client,
        "POST",
        "/relationships",
        {
            "source_id": event_id,
            "target_id": _PERSON,
            "type_id": "participant",
            "role": "pharmacist",
            "agent": "cursor",
        },
    )
    if org_id:
        _req(
            client,
            "POST",
            "/relationships",
            {
                "source_id": event_id,
                "target_id": org_id,
                "type_id": "related_to",
                "role": "store",
                "agent": "cursor",
            },
        )
    status, existing = _req(client, "GET", f"/entities/{_q(event_id)}?intent=full")
    if status != 200:
        return
    for rel in existing.get("relationships") or []:
        if rel.get("type_id") != "related_to" or rel.get("role") != "store":
            continue
        if org_id and rel.get("target_id") == org_id:
            continue
        rid = rel.get("id")
        if rid:
            _req(client, "DELETE", f"/relationships/{rid}")


def _assert_roster(client, payload: dict) -> str:
    status, body = _req(
        client,
        "POST",
        "/assertions",
        {
            "entity_id": payload["id"],
            "claim": _claim(payload),
            "confidence": "believed",
            "derivation_type": "agent_observation",
            "evidence": "ESS My Schedule iframe harvest (ess_emp_schedule.jsp).",
            "evidence_uris": [_HARVEST_URI, _RUNBOOK_URI],
            "valid_from": payload["attributes"]["date"],
            "agent": "cursor",
        },
    )
    if body.get("already_known") or status in {200, 201}:
        return "already_known" if body.get("already_known") else "asserted"
    return f"assert_{status}"


def _upsert_day(client, day: dict, harvest_at: str) -> dict:
    org = _org(client, day["store_number"])
    payload = _payload(day, org, harvest_at)
    eid = payload["id"]
    status, existing = _req(client, "GET", f"/entities/{_q(eid)}?intent=full")
    action = "create"
    if status == 200:
        if _same_roster(existing, payload):
            action = "unchanged"
        else:
            patch = {
                "name": payload["name"],
                "description": payload["description"],
                "attributes": payload["attributes"],
                "source_uri": _HARVEST_URI,
                "workflow_state": "scheduled",
            }
            _req(client, "PATCH", f"/entities/{_q(eid)}", patch)
            action = "updated"
    else:
        created = _req(client, "POST", "/entities", payload)
        if created[0] not in {200, 201}:
            return {"id": eid, "action": f"create_{created[0]}", "detail": created[1]}
    _ensure_rels(client, eid, org["id"] if org else None)
    asserted = _assert_roster(client, payload)
    return {"id": eid, "action": action, "assert": asserted, "store": day["store_number"]}


def _cancel(client, entity_id: str) -> dict:
    status, existing = _req(client, "GET", f"/entities/{_q(entity_id)}?intent=full")
    if status != 200:
        return {"id": entity_id, "action": "cancel_missing"}
    attrs = dict(existing.get("attributes") or {})
    attrs["roster_state"] = "cancelled"
    attrs["roster_source"] = "ess_my_schedule"
    _req(
        client,
        "PATCH",
        f"/entities/{_q(entity_id)}",
        {"attributes": attrs, "workflow_state": "cancelled", "source_uri": _HARVEST_URI},
    )
    return {"id": entity_id, "action": "cancelled"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="src", required=True)
    args = parser.parse_args()
    harvest = json.loads(Path(args.src).read_text(encoding="utf-8"))
    scheduled = _scheduled_days(harvest)
    start, end = _window(harvest)
    keep = {day["date"] for day in scheduled}
    report = {"window": [start, end], "scheduled": [], "cancelled": []}
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=60.0) as client:
        listed = _req(
            client, "GET", "/entities?type=event&query=walgreens-shift&limit=100"
        )
        items = listed[1].get("items") or []
        for day in scheduled:
            report["scheduled"].append(_upsert_day(client, day, harvest["harvested_at"]))
        for item in items:
            eid = item.get("id") or ""
            if not eid.startswith("event:walgreens-shift-"):
                continue
            parts = eid.split("event:walgreens-shift-", 1)[-1]
            if len(parts) != 10 or parts < start or parts > end:
                continue
            if parts not in keep:
                report["cancelled"].append(_cancel(client, eid))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
