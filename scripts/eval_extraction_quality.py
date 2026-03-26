"""Evaluate extraction metadata quality for a RAG corpus (e.g. 9b vs 1.7b).

Uses GET /extraction_export (single bulk request) instead of N+1 source queries.
Reports schema validity, per-chunk counts, document-level consistency, and vocabulary.
Export with --output-json, then compare two runs with --compare.
Exit codes: 0=ok, 1=no chunks for prefix, 2=service error.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transport_utils import DEFAULT_RAG_URL, make_sync_client


@dataclass(slots=True, kw_only=True)
class ChunkExtraction:
    source: str
    chunk_id: str
    chunk_index: int
    valid: bool
    entities: int = 0
    topics: int = 0
    facets: int = 0
    relations: int = 0
    entity_names: list[str] = field(default_factory=list)
    entity_types: list[str] = field(default_factory=list)
    topic_values: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    parse_error: str | None = None


def _parse_extraction(
    metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    raw = metadata.get("extraction")
    if not raw:
        return None, "missing"
    if isinstance(raw, dict):
        return raw, None
    if isinstance(raw, str):
        try:
            return json.loads(raw), None
        except json.JSONDecodeError as e:
            return None, str(e)
    return None, "not_dict_or_str"


def _validate_schema(data: dict[str, Any]) -> list[str]:
    """Return schema violations (empty = valid). Checks entities[].name/type/facets/relations, topics."""
    errs: list[str] = []
    if not isinstance(data.get("entities"), list):
        return ["entities not a list"]
    if not isinstance(data.get("topics"), list):
        errs.append("topics not a list")
    for i, ent in enumerate(data["entities"]):
        ep = f"entity[{i}]"
        if not isinstance(ent, dict):
            errs.append(f"{ep} not a dict")
            continue
        if "name" not in ent:
            errs.append(f"{ep} missing name")
        if not isinstance(ent.get("type"), list):
            errs.append(f"{ep}.type not a list")
        for j, fct in enumerate(ent.get("facets") or []):
            if not isinstance(fct, dict) or "name" not in fct or "value" not in fct:
                errs.append(f"{ep}.facets[{j}] missing name/value")
        for j, rel in enumerate(ent.get("relations") or []):
            if not isinstance(rel, dict) or not {"predicate", "target"} <= rel.keys():
                errs.append(f"{ep}.relations[{j}] missing predicate/target")
    return errs


def _summarize_chunk(
    source: str, chunk_id: str, chunk_index: int, extraction: str | None
) -> ChunkExtraction:
    data, err = _parse_extraction({"extraction": extraction})
    if data is None:
        return ChunkExtraction(
            source=source,
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            valid=False,
            parse_error=err,
        )
    errs = _validate_schema(data)
    entities = data.get("entities") or []
    topics = data.get("topics") or []
    names: list[str] = []
    types: list[str] = []
    facets = relations = 0
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        if isinstance(ent.get("name"), str):
            names.append(ent["name"])
        types.extend(t for t in (ent.get("type") or []) if isinstance(t, str))
        facets += len(ent.get("facets") or [])
        relations += len(ent.get("relations") or [])
    return ChunkExtraction(
        source=source,
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        valid=not errs,
        entities=len(entities),
        topics=len(topics),
        facets=facets,
        relations=relations,
        entity_names=names,
        entity_types=types,
        topic_values=[t for t in topics if isinstance(t, str)],
        schema_errors=errs,
    )


def _doc_stats(summaries: list[ChunkExtraction]) -> dict[str, dict[str, Any]]:
    """Per-doc: cross_chunk_entities (names in >1 chunk), dedup_ratio (unique/total mentions)."""
    by_source: dict[str, list[ChunkExtraction]] = {}
    for s in summaries:
        by_source.setdefault(s.source, []).append(s)
    result: dict[str, dict[str, Any]] = {}
    for src, chunks in by_source.items():
        valid = [c for c in chunks if c.valid]
        if not valid:
            result[src] = {"total": len(chunks), "valid": 0}
            continue
        name_sets = [set(c.entity_names) for c in valid]
        all_names = set().union(*name_sets)
        cross_chunk = sum(
            1 for n in all_names if sum(1 for ns in name_sets if n in ns) > 1
        )
        total_mentions = sum(len(ns) for ns in name_sets)
        result[src] = {
            "total": len(chunks),
            "valid": len(valid),
            "avg_entities": sum(c.entities for c in valid) / len(valid),
            "avg_topics": sum(c.topics for c in valid) / len(valid),
            "unique_entities": len(all_names),
            "cross_chunk_entities": cross_chunk,
            "dedup_ratio": len(all_names) / max(total_mentions, 1),
        }
    return result


def _fetch_export(client: Any, prefix: str) -> list[dict[str, Any]]:
    resp = client.get("/extraction_export", params={"prefix": prefix})
    resp.raise_for_status()
    return resp.json().get("items", [])


def run_eval(
    prefix: str, rag_url: str = DEFAULT_RAG_URL, timeout: float = 60.0
) -> list[ChunkExtraction]:
    with make_sync_client(rag_url, timeout=timeout) as client:
        items = _fetch_export(client, prefix)
    return [
        _summarize_chunk(
            source=item.get("source", ""),
            chunk_id=item.get("chunk_id", ""),
            chunk_index=int(item.get("chunk_index", 0)),
            extraction=item.get("extraction"),
        )
        for item in items
    ]


def _top_n(counter: Counter[str], n: int = 12) -> str:
    top = counter.most_common(n)
    return ", ".join(f"{v}({c})" for v, c in top) + ("..." if len(counter) > n else "")


def print_metrics(summaries: list[ChunkExtraction]) -> None:
    total = len(summaries)
    valid_s = [s for s in summaries if s.valid]
    n_valid = len(valid_s)
    n_missing = sum(1 for s in summaries if s.parse_error == "missing")
    n_parse_err = sum(1 for s in summaries if s.parse_error not in (None, "missing"))
    n_schema_err = sum(1 for s in summaries if s.schema_errors)
    print("=== Extraction quality ===")
    print(
        f"Chunks : {total} total | {n_valid} valid | {n_missing} missing | "
        f"{n_parse_err} parse-error | {n_schema_err} schema-error"
    )
    if not valid_s:
        return
    ep = [s.entities for s in valid_s]
    tp = [s.topics for s in valid_s]
    f_tot = sum(s.facets for s in valid_s)
    r_tot = sum(s.relations for s in valid_s)
    print(f"\nPer-chunk (valid={n_valid}):")
    print(f"  entities:  min={min(ep)} max={max(ep)} avg={sum(ep) / len(ep):.1f}")
    print(f"  topics:    min={min(tp)} max={max(tp)} avg={sum(tp) / len(tp):.1f}")
    print(f"  facets:    total={f_tot}  avg={f_tot / len(valid_s):.1f}")
    print(f"  relations: total={r_tot}  avg={r_tot / len(valid_s):.1f}")
    type_counter: Counter[str] = Counter()
    topic_counter: Counter[str] = Counter()
    for s in valid_s:
        type_counter.update(s.entity_types)
        topic_counter.update(s.topic_values)
    print("\nVocabulary:")
    print(f"  Unique entity types: {len(type_counter)}  top: {_top_n(type_counter)}")
    print(f"  Unique topics:       {len(topic_counter)}  top: {_top_n(topic_counter)}")
    docs = _doc_stats(summaries)
    valid_docs = {k: v for k, v in docs.items() if v.get("valid", 0) > 0}
    if valid_docs:
        n_d = len(valid_docs)
        avg_ue = sum(d["unique_entities"] for d in valid_docs.values()) / n_d
        avg_cc = sum(d["cross_chunk_entities"] for d in valid_docs.values()) / n_d
        avg_dr = sum(d["dedup_ratio"] for d in valid_docs.values()) / n_d
        avg_ch = sum(d["total"] for d in valid_docs.values()) / n_d
        print(f"\nDocument-level ({n_d} sources):")
        print(f"  avg chunks/doc:           {avg_ch:.1f}")
        print(f"  avg unique entities/doc:  {avg_ue:.1f}")
        print(
            f"  avg cross-chunk entities: {avg_cc:.1f}"
            "  (names appearing in >1 chunk of same doc)"
        )
        print(
            f"  avg dedup ratio:          {avg_dr:.2f}"
            "  (1.0 = no repeated entity names across chunks)"
        )


def compare_exports(
    path_a: str, path_b: str, labels: tuple[str, str] = ("A", "B")
) -> None:
    a_records = json.loads(Path(path_a).read_text())
    b_records = json.loads(Path(path_b).read_text())
    la, lb = labels
    _key = lambda r: (r.get("source", ""), int(r.get("chunk_index", 0)))  # noqa: E731
    _avg = lambda recs, k: sum(r.get(k, 0) for r in recs) / len(recs)  # noqa: E731
    a_map = {_key(r): r for r in a_records}
    b_map = {_key(r): r for r in b_records}
    both = set(a_map) & set(b_map)
    a_src = len({r.get("source") for r in a_records})
    b_src = len({r.get("source") for r in b_records})
    print(f"=== Comparison: {la} vs {lb} ===")
    print(f"{la}: {path_a}  ({len(a_records)} chunks, {a_src} sources)")
    print(f"{lb}: {path_b}  ({len(b_records)} chunks, {b_src} sources)")
    a_only = len(set(a_map) - set(b_map))
    b_only = len(set(b_map) - set(a_map))
    print(f"\nAlignment: matched={len(both)}  {la}-only={a_only}  {lb}-only={b_only}")
    if not both:
        print("No matched pairs — cannot compute deltas.")
        return
    a_m, b_m = [a_map[k] for k in both], [b_map[k] for k in both]
    a_vr = sum(1 for r in a_m if r.get("valid", False)) / len(both)
    b_vr = sum(1 for r in b_m if r.get("valid", False)) / len(both)
    print(f"\nSchema-valid rate: {la}={a_vr:.1%}  {lb}={b_vr:.1%}")
    print(f"\nPer matched chunk (N={len(both)}):")
    for metric in ("entities", "topics", "facets", "relations"):
        av, bv = _avg(a_m, metric), _avg(b_m, metric)
        sign = "+" if av >= bv else ""
        print(f"  {metric:<10} {la}={av:.1f}  {lb}={bv:.1f}  delta={sign}{av - bv:.1f}")
    jaccards: list[float] = []
    for k in both:
        an = set(a_map[k].get("entity_names") or [])
        bn = set(b_map[k].get("entity_names") or [])
        jaccards.append(len(an & bn) / len(an | bn) if (an | bn) else 1.0)
    avg_j = sum(jaccards) / len(jaccards)
    print(f"\nJaccard entity names (per pair): avg={avg_j:.2f}  (1.0 = identical)")


def main() -> int:
    import argparse

    _epilog = (
        "Eval:    --prefix /abs/path [--output-json /tmp/9b.json]\n"
        "Compare: --compare /tmp/9b.json /tmp/1.7b.json [--labels 9b,1.7b]"
    )
    p = argparse.ArgumentParser(
        description="Evaluate or compare extraction metadata quality for a RAG corpus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog,
    )
    p.add_argument("--prefix", help="Source path prefix (absolute path)")
    p.add_argument("--rag-url", default=DEFAULT_RAG_URL, help="RAG URL (default: UDS)")
    p.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout (s)")
    p.add_argument("--output-json", metavar="PATH", help="Write chunk summary JSON")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"), help="Compare two exports")
    p.add_argument(
        "--labels", default="A,B", help="Labels for --compare (default: A,B)"
    )
    args = p.parse_args()

    if args.compare:
        la, lb = (args.labels.split(",", 1) + ["B"])[:2]
        compare_exports(args.compare[0], args.compare[1], labels=(la, lb))
        return 0

    if not args.prefix:
        p.error("--prefix is required unless --compare is used")

    try:
        summaries = run_eval(
            prefix=args.prefix, rag_url=args.rag_url, timeout=args.timeout
        )
    except httpx.ConnectError:
        print(f"Error: RAG service unreachable at {args.rag_url}", file=sys.stderr)
        return 2
    except httpx.HTTPStatusError as e:
        print(f"Error: RAG returned HTTP {e.response.status_code}", file=sys.stderr)
        return 2

    if not summaries:
        print(
            f"No chunks found for prefix '{args.prefix}'.\n"
            "Check that the prefix matches stored source paths (usually absolute).",
            file=sys.stderr,
        )
        return 1

    print_metrics(summaries)

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([dataclasses.asdict(s) for s in summaries], indent=2)
        )
        print(f"\nWrote {len(summaries)} chunk summaries to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
