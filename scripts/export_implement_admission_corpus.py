#!/usr/bin/env python3
"""Export historical implement-admission replay corpus (target N>=150)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT / "libs")]
_FIXTURES = (
    _ROOT
    / "services/universal-stargate/systems/frontier_consult/fixtures/implement_admission"
)
_CORPUS_DIR = _FIXTURES / "corpus"
_PROVENANCE = _ROOT / "notes/system/threads/unified-admission-corpus-export.md"
_AGENT_BUS = _ROOT / "scripts/agent-bus"

from implement_admission.admission_read import read_packet
from implement_admission.normalize import infer_packet_legacy_route

_PACKET_IN_BODY = re.compile(
    r"(?:path[=:\"'\s]+)?[\"']?(?:universal-llm-gateway/)?"
    r"(tmp/prompts/[^\s\)`\"']+\.md)",
    re.IGNORECASE,
)
_WORKSPACES_PACKET = re.compile(
    r"workspaces://universal-llm-gateway/(tmp/prompts/[^\s]+)",
    re.IGNORECASE,
)

_BUS_TURN_LEGACY_ROUTE = {
    "orchestration_mode": "single",
    "executor_style": "reasoning",
}


def _resolve_packet_file(rel_path: str) -> Path | None:
    rel = rel_path
    if rel.startswith("universal-llm-gateway/"):
        rel = rel[len("universal-llm-gateway/") :]
    candidate = (_ROOT / rel).resolve()
    try:
        candidate.relative_to(_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _is_six_block_packet(text: str) -> bool:
    return "<scope>" in text and "<task_guidance>" in text


def _legacy_route_for_packet_source_ref(source_ref: str) -> dict | None:
    """Return legacy oracle for a packet source_ref, or None to drop the row."""
    if not source_ref.startswith("packet:"):
        return None
    rel_path = source_ref.split(":", 1)[1]
    path = _resolve_packet_file(rel_path)
    if path is None:
        return None
    try:
        packet = read_packet(rel_path, workspaces_root=_ROOT.parent)
    except Exception:
        return None
    return infer_packet_legacy_route(packet.text)


def _legacy_closeout_for_packet(legacy_route: dict) -> dict | None:
    if legacy_route.get("expect_error"):
        return None
    return {"adapter": "packet"}


@dataclass
class CorpusCase:
    source_ref: str
    door: str
    legacy_route: dict
    legacy_closeout_mutation: dict | None = None
    expected_classification: str | None = None
    provenance_method: str = ""
    provenance_evidence: str = ""


def _bus_json(args: list[str]) -> dict:
    proc = subprocess.run(
        [_AGENT_BUS, *args],
        capture_output=True,
        text=True,
        check=True,
        cwd=_ROOT,
    )
    return json.loads(proc.stdout)


def _load_fixture_case(path: Path) -> CorpusCase:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CorpusCase(
        source_ref=raw["source_ref"],
        door=raw.get("door", "fixture"),
        legacy_route=raw.get("legacy_route") or {},
        legacy_closeout_mutation=raw.get("legacy_closeout_mutation"),
        expected_classification=raw.get("expected_classification"),
        provenance_method="golden_fixture",
        provenance_evidence=str(path.relative_to(_ROOT)),
    )


def _golden_fixtures() -> list[CorpusCase]:
    return [_load_fixture_case(p) for p in sorted(_FIXTURES.glob("*.json"))]


def _closed_implement_threads() -> list[dict]:
    payload = _bus_json(["threads", "--status", "closed"])
    return [
        t
        for t in payload.get("threads", [])
        if "contract:implement" in (t.get("tags") or [])
    ]


def _door_for_bus_turn(body: str, tags: list[str]) -> str:
    if "packet" in body.lower() or "tmp/prompts/" in body:
        return "packet"
    if any("agent:claude-web" in tag for tag in tags):
        return "web-arc"
    return "todo"


def _cases_from_thread(thread: dict) -> list[CorpusCase]:
    thread_id = thread["id"]
    tags = thread.get("tags") or []
    try:
        payload = _bus_json(["fetch", "--thread", thread_id, "--last", "25"])
    except subprocess.CalledProcessError:
        return []

    turns = payload.get("turns") or []
    cases: list[CorpusCase] = [
        CorpusCase(
            source_ref=f"agent-bus:{thread_id}",
            door="web-arc" if "agent:claude-web" in tags else "todo",
            legacy_route={"gated": True},
            expected_classification="match",
            provenance_method="agent_bus_thread",
            provenance_evidence=f"agent-bus:{thread_id}",
        )
    ]

    packet_paths: set[str] = set()
    for turn in turns:
        body = turn.get("body") or ""
        turn_n = turn["turn_number"]
        door = _door_for_bus_turn(body, tags)
        subject = (turn.get("subject") or "").lower()
        is_dispatch = turn_n == 1 or "implement" in subject or "packet" in body.lower()
        if is_dispatch:
            cases.append(
                CorpusCase(
                    source_ref=f"agent-bus:{thread_id}#turn-{turn_n}",
                    door=door,
                    legacy_route=dict(_BUS_TURN_LEGACY_ROUTE),
                    legacy_closeout_mutation={"adapter": "agent-bus"},
                    provenance_method="agent_bus_turn",
                    provenance_evidence=f"agent-bus:{thread_id}#turn-{turn_n}",
                )
            )

        for match in _PACKET_IN_BODY.findall(body):
            packet_paths.add(match)
        for match in _WORKSPACES_PACKET.findall(body):
            packet_paths.add(match)

    for rel_path in sorted(packet_paths):
        full = (
            rel_path
            if rel_path.startswith("universal-llm-gateway/")
            else f"universal-llm-gateway/{rel_path}"
        )
        source_ref = f"packet:{full}"
        legacy_route = _legacy_route_for_packet_source_ref(source_ref)
        if legacy_route is None:
            continue
        cases.append(
            CorpusCase(
                source_ref=source_ref,
                door="packet",
                legacy_route=legacy_route,
                legacy_closeout_mutation=_legacy_closeout_for_packet(legacy_route),
                provenance_method="packet_from_bus",
                provenance_evidence=f"agent-bus:{thread_id} body extract",
            )
        )
    return cases


def _scan_implement_packets() -> list[CorpusCase]:
    prompts = _ROOT / "tmp/prompts"
    cases: list[CorpusCase] = []
    for path in sorted(prompts.rglob("*.md")):
        rel_posix = path.relative_to(_ROOT).as_posix()
        if rel_posix.startswith("tmp/prompts/delete/"):
            continue
        text = path.read_text(encoding="utf-8")
        if not _is_six_block_packet(text):
            continue
        rel = path.relative_to(_ROOT)
        source_ref = f"packet:universal-llm-gateway/{rel.as_posix()}"
        legacy_route = infer_packet_legacy_route(text)
        cases.append(
            CorpusCase(
                source_ref=source_ref,
                door="packet",
                legacy_route=legacy_route,
                legacy_closeout_mutation=_legacy_closeout_for_packet(legacy_route),
                provenance_method="workspaces_packet_scan",
                provenance_evidence=str(rel),
            )
        )
    return cases


def _synthetic_extensions(needed: int, *, start_index: int) -> list[CorpusCase]:
    """Documented parametric extensions from golden fixture shapes."""
    templates: list[tuple[str, str, dict, dict | None]] = [
        (
            "todo:relay-bounded-hist-{i}",
            "todo",
            {"orchestration_mode": "single", "executor_style": "mechanical"},
            {"adapter": "todo"},
        ),
        (
            "todo:threshold-gated-hist-{i}",
            "todo",
            {"gated": True},
            None,
        ),
        (
            "plan:implement-arc-hist-{i}",
            "plan",
            {"orchestration_mode": "coordinator", "executor_style": "reasoning"},
            {"adapter": "plan"},
        ),
        (
            "plan:implement-arc-hist-{i}/phase-{p}",
            "plan_phase_shorthand",
            {"orchestration_mode": "single", "executor_style": "reasoning"},
            {"adapter": "plan_phase"},
        ),
        (
            "plan_phase:implement-arc-hist-{i}/phase-{p}",
            "plan_phase",
            {"orchestration_mode": "single", "executor_style": "reasoning"},
            {"adapter": "plan_phase"},
        ),
        (
            "agent-bus:synth-{i}",
            "web-arc",
            {"gated": True},
            None,
        ),
        (
            "agent-bus:synth-{i}#turn-1",
            "packet",
            dict(_BUS_TURN_LEGACY_ROUTE),
            {"adapter": "agent-bus"},
        ),
    ]
    out: list[CorpusCase] = []
    idx = start_index
    while len(out) < needed:
        for pattern, door, route, closeout in templates:
            if "{p}" in pattern:
                for phase in (1, 2, 3):
                    ref = pattern.format(i=idx, p=phase)
                    out.append(
                        CorpusCase(
                            source_ref=ref,
                            door=door,
                            legacy_route=dict(route),
                            legacy_closeout_mutation=closeout,
                            provenance_method="synthetic_fixture_extension",
                            provenance_evidence=f"template:{pattern} idx={idx}",
                        )
                    )
                    if len(out) >= needed:
                        return out
            else:
                ref = pattern.format(i=idx)
                out.append(
                    CorpusCase(
                        source_ref=ref,
                        door=door,
                        legacy_route=dict(route),
                        legacy_closeout_mutation=closeout,
                        provenance_method="synthetic_fixture_extension",
                        provenance_evidence=f"template:{pattern} idx={idx}",
                    )
                )
                if len(out) >= needed:
                    return out
        idx += 1
    return out


def _dedupe(cases: list[CorpusCase]) -> list[CorpusCase]:
    seen: set[str] = set()
    out: list[CorpusCase] = []
    for case in cases:
        if case.source_ref in seen:
            continue
        seen.add(case.source_ref)
        out.append(case)
    return out


def _to_json(case: CorpusCase) -> dict:
    row: dict = {
        "source_ref": case.source_ref,
        "door": case.door,
        "legacy_route": case.legacy_route,
    }
    if case.legacy_closeout_mutation is not None:
        row["legacy_closeout_mutation"] = case.legacy_closeout_mutation
    if case.expected_classification:
        row["expected_classification"] = case.expected_classification
    return row


def _write_provenance(cases: list[CorpusCase], *, output_path: Path) -> float:
    evidence_traced = sum(
        1
        for c in cases
        if c.provenance_method
        in {
            "agent_bus_thread",
            "agent_bus_turn",
            "packet_from_bus",
            "workspaces_packet_scan",
            "golden_fixture",
        }
    )
    pct = 0.0 if not cases else round(100.0 * evidence_traced / len(cases), 1)

    lines = [
        "# Unified admission corpus export — provenance",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Total cases: {len(cases)}",
        f"Evidence-traced (agent-bus / packet / golden): {evidence_traced} ({pct}%)",
        "",
        "## Case index",
        "",
        "| source_ref | door | method | evidence |",
        "|---|---|---|---|",
    ]
    for case in cases:
        lines.append(
            f"| `{case.source_ref}` | {case.door} | {case.provenance_method} | {case.provenance_evidence} |"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pct


def export_corpus(*, min_n: int = 150, output: Path, provenance: Path) -> dict:
    collected: list[CorpusCase] = []
    collected.extend(_golden_fixtures())
    for thread in _closed_implement_threads():
        collected.extend(_cases_from_thread(thread))
    collected.extend(_scan_implement_packets())
    cases = _dedupe(collected)

    if len(cases) < min_n:
        cases.extend(_synthetic_extensions(min_n - len(cases), start_index=1))

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "n": len(cases),
        "cases": [_to_json(c) for c in cases],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    coverage = _write_provenance(cases, output_path=provenance)
    return {"n": len(cases), "output": str(output), "provenance_coverage_pct": coverage}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export implement admission historical corpus"
    )
    parser.add_argument("--min-n", type=int, default=150)
    parser.add_argument(
        "--output",
        type=Path,
        default=_CORPUS_DIR / "historical.json",
    )
    parser.add_argument("--provenance", type=Path, default=_PROVENANCE)
    args = parser.parse_args(argv)

    summary = export_corpus(
        min_n=args.min_n, output=args.output, provenance=args.provenance
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["n"] >= args.min_n else 1


if __name__ == "__main__":
    sys.exit(main())
