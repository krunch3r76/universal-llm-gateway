"""Persist a hunt run to Cortex files + entities + dated assertions.

Raw payload is written under CORTEX_FILES_ROOT before any claim. Property
entities are created once (`asset:ca-sco-{property_id}`); each run appends a
new observation assertion. Probe runs never mint property entities.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from unclaimed_property_hunter.cortex_client import (
    assert_claim,
    create_entity,
    get_entity,
    list_entities,
)
from unclaimed_property_hunter.diff_runs import RunDiff, diff_runs
from unclaimed_property_hunter.models import Hit, RunRecord
from unclaimed_property_hunter.result_surface import (
    format_assertion_claim,
    format_entity_attributes,
    format_entity_description,
)

_FILES_ROOT = Path(os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files"))
_RUNS_REL = Path("notes/system/unclaimed-property/runs")


def _write_bytes(rel: Path, data: bytes) -> tuple[str, str]:
    """Write `rel` under the cortex files root; return (cortex_uri, sha256)."""
    dest = (_FILES_ROOT / rel).resolve()
    dest.relative_to(_FILES_ROOT.resolve())
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    uri = "cortex://" + rel.as_posix()
    return uri, digest


def write_raw_and_normalized(record: RunRecord, raw_bytes: bytes) -> RunRecord:
    """Save raw payload + normalized JSON; return record with raw_payload_uri set."""
    if record.run_kind in ("bulk_extract", "estates_extract"):
        if record.corpus_fingerprint is None or not record.corpus_fingerprint.zip_sha256:
            raise RuntimeError(
                f"refuse {record.run_kind} persist without corpus zip_sha256"
            )
    raw_rel = _RUNS_REL / f"{record.run_id}.raw"
    uri, digest = _write_bytes(raw_rel, raw_bytes)
    updated = RunRecord(
        run_id=record.run_id,
        utc_timestamp=record.utc_timestamp,
        query=record.query,
        run_kind=record.run_kind,
        search_executed=record.search_executed,
        raw_payload_uri=uri,
        raw_sha256=digest,
        hits=record.hits,
        notes=record.notes,
        corpus_fingerprint=record.corpus_fingerprint,
        notify_outcome=record.notify_outcome,
        check_failed=record.check_failed,
    )
    norm_rel = _RUNS_REL / f"{record.run_id}.normalized.json"
    _write_bytes(norm_rel, json.dumps(updated.to_json_dict(), indent=2).encode())
    return updated


def _ensure_entity(**payload: Any) -> dict[str, Any]:
    created = create_entity(**payload)
    if created.get("error") and (
        "409" in str(created) or created.get("status_code") == 409
    ):
        return get_entity(str(payload["id"]))
    if created.get("error"):
        err = str(created.get("error", "")).lower()
        if "exist" in err or "conflict" in err or "duplicate" in err:
            return get_entity(str(payload["id"]))
        raise RuntimeError(f"entity_create failed: {created}")
    return created


def persist_run(record: RunRecord) -> dict[str, Any]:
    """Create the run document, assert the run, and upsert hit assets.

    Returns the write payloads so a closeout can quote entity/assertion IDs.
    """
    if not record.raw_payload_uri:
        raise RuntimeError("refuse to claim without raw_payload_uri")
    run_entity_id = f"document:ca-sco-run-{record.run_id}"
    run_entity = _ensure_entity(
        id=run_entity_id,
        type="document",
        name=f"CA SCO hunt {record.query.surname} {record.utc_timestamp}",
        description=format_entity_description(record),
        source_uri=record.raw_payload_uri,
        attributes=format_entity_attributes(record),
    )
    run_assert = assert_claim(
        entity_id=run_entity_id,
        claim=format_assertion_claim(record),
        confidence="confirmed",
        evidence=(
            f"raw={record.raw_payload_uri} sha256={record.raw_sha256} "
            f"http={record.query.exact_http_request}"
        ),
        evidence_uris=[record.raw_payload_uri],
        derivation_type="agent_observation",
        agent="cursor-sdk",
        valid_from=record.utc_timestamp[:10],
    )
    hit_writes: list[dict[str, Any]] = []
    for hit in record.hits:
        hit_writes.append(_persist_hit(record, hit))
    return {
        "run_entity": run_entity,
        "run_assertion": run_assert,
        "hits": hit_writes,
    }


def _persist_hit(record: RunRecord, hit: Hit) -> dict[str, Any]:
    entity_id = f"asset:ca-sco-{hit.property_id}"
    entity = _ensure_entity(
        id=entity_id,
        type="asset",
        name=f"CA SCO property {hit.property_id}",
        description=f"Holder {hit.holder}; owner {hit.owner_name}",
        source_uri=record.raw_payload_uri,
        attributes={"property_id": hit.property_id, "holder": hit.holder},
    )
    flag = " PRUDENTIAL-HOLDER" if hit.is_prudential() else ""
    assertion = assert_claim(
        entity_id=entity_id,
        claim=(
            f"Observed on {record.utc_timestamp} query={record.query.intended_query_string}: "
            f"holder={hit.holder} owner={hit.owner_name} type={hit.property_type} "
            f"amount={hit.amount_or_range}{flag}"
        ),
        confidence="confirmed",
        evidence=f"raw={record.raw_payload_uri}",
        evidence_uris=[record.raw_payload_uri],
        derivation_type="agent_observation",
        agent="cursor-sdk",
        valid_from=record.utc_timestamp[:10],
    )
    return {"entity": entity, "assertion": assertion, "prudential": hit.is_prudential()}


def load_run_from_normalized(path: Path) -> RunRecord:
    """Rehydrate a RunRecord from a normalized JSON sidecar on disk."""
    from unclaimed_property_hunter.models import Query

    data = json.loads(path.read_text(encoding="utf-8"))
    query = Query(**data["query"])
    hits = [Hit(**row) for row in data.get("hits", [])]
    return RunRecord(
        run_id=data["run_id"],
        utc_timestamp=data["utc_timestamp"],
        query=query,
        run_kind=data["run_kind"],
        search_executed=bool(data["search_executed"]),
        raw_payload_uri=data["raw_payload_uri"],
        raw_sha256=data["raw_sha256"],
        hits=hits,
        notes=data.get("notes", ""),
        corpus_fingerprint=_load_fingerprint(data.get("corpus_fingerprint")),
        notify_outcome=data.get("notify_outcome"),
        check_failed=bool(data.get("check_failed", False)),
    )


def _load_fingerprint(raw: dict[str, Any] | None):
    from unclaimed_property_hunter.models import CorpusFingerprint

    if not raw:
        return None
    return CorpusFingerprint(
        url=str(raw.get("url", "")),
        last_modified=str(raw.get("last_modified", "")),
        etag=str(raw.get("etag", "")),
        content_length=int(raw.get("content_length", 0)),
        zip_sha256=str(raw.get("zip_sha256", "")),
        rows_scanned=int(raw.get("rows_scanned", 0)),
    )


def prior_runs_for_surname(surname: str) -> list[Path]:
    """Normalized sidecars for `surname`, oldest first (filename sorts by run_id)."""
    folder = _FILES_ROOT / _RUNS_REL
    if not folder.is_dir():
        return []
    needle = surname.strip().lower()
    matches: list[Path] = []
    for path in sorted(folder.glob("*.normalized.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if str(data.get("query", {}).get("surname", "")).lower() == needle:
            matches.append(path)
    return matches


def diff_latest(surname: str) -> RunDiff | None:
    """Diff the two most recent persisted runs for `surname`, or None if <2."""
    paths = prior_runs_for_surname(surname)
    if len(paths) < 2:
        return None
    previous = load_run_from_normalized(paths[-2])
    current = load_run_from_normalized(paths[-1])
    return diff_runs(previous, current)


def consecutive_check_failed_runs(surname: str) -> int:
    """Count trailing persisted runs with check_failed=True for ``surname``."""
    paths = prior_runs_for_surname(surname)
    streak = 0
    for path in reversed(paths):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("check_failed"):
            streak += 1
        else:
            break
    return streak


def latest_run_for_kinds(surname: str, run_kinds: tuple[str, ...]) -> RunRecord | None:
    """Most recent persisted run for ``surname`` whose ``run_kind`` is in ``run_kinds``."""
    wanted = set(run_kinds)
    for path in reversed(prior_runs_for_surname(surname)):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("run_kind") in wanted:
            return load_run_from_normalized(path)
    return None


def list_run_documents(surname: str) -> dict[str, Any]:
    """Cortex entity list for run documents (coverage bound, not the diff source)."""
    return list_entities(entity_type="document", query=f"ca-sco-run-{surname.lower()}", limit=50)
