"""Live closeout adapters — source-specific lifecycle mutations."""

from __future__ import annotations

from implement_admission.closeout_helpers import (
    cortex_files_root,
    extract_embedded_source_ref,
    plan_slug_from_ref,
    source_from_ref,
    thread_id_from_bus_ref,
    workspaces_root,
)
from implement_admission.closeout_models import AdapterResult, ImplementCloseout
from implement_admission.closeout_runtime import get_runtime
from implement_admission.spec import CloseoutAdapterKind, CloseoutStatus, Source


def _result(
    *,
    adapter: str,
    status: str,
    mutation: str | None = None,
    error: str | None = None,
) -> AdapterResult:
    return AdapterResult(adapter=adapter, status=status, mutation=mutation, error=error)


class TodoCloseoutAdapter:
    def apply(
        self, closeout: ImplementCloseout, *, source: Source
    ) -> list[AdapterResult]:
        # Close the todo IN-PROCESS via cortex-api (the pure data layer), NOT by
        # re-entering Stargate over HTTP through pipeline:todo-close. This adapter
        # runs synchronously inside the async implement-closeout handler; a
        # blocking HTTP re-entry into Stargate stalls the event loop and times out
        # while the mutation still lands (false-negative closeout — see
        # todo:unified-admission-closeout-adapters AC10). Mirrors the todo-close
        # pipeline's core op sequence (sidecar -> assert -> workflow_state=done)
        # over the same cortex tools; references/depends_on/edges are not carried
        # on the ImplementCloseout envelope and are intentionally out of scope.
        rt = get_runtime()
        from implement_admission.closeout import flatten_evidence_uris

        todo_id = source.canonical_ref
        errors: list[str] = []

        sidecar = rt.dispatch(
            "todo_close_sidecar",
            {
                "todo_id": todo_id,
                "summary": closeout.summary,
                "evidence": closeout.summary,
                "agent": "pipeline:implement-closeout",
            },
        )
        evidence_uris = flatten_evidence_uris(closeout.evidence_uris)
        if sidecar.get("error"):
            errors.append(f"sidecar: {sidecar['error']}")
        else:
            uri = sidecar.get("closure_summary_uri")
            if uri and uri not in evidence_uris:
                evidence_uris.append(uri)

        assert_args: dict[str, object] = {
            "entity_id": todo_id,
            "claim": closeout.summary,
            "confidence": "confirmed",
            "evidence": closeout.summary,
            "derivation_type": "agent_observation",
            "confidence_score": 0.8,
            "seeded_by": "pipeline:implement-closeout",
        }
        if evidence_uris:
            assert_args["evidence_uris"] = evidence_uris
        assert_resp = rt.dispatch("assert", assert_args)
        if assert_resp.get("error"):
            errors.append(f"assert: {assert_resp['error']}")

        wf = rt.dispatch(
            "entity_update",
            {"entity_id": todo_id, "workflow_state": "done"},
        )
        if wf.get("error"):
            errors.append(f"workflow_update: {wf['error']}")

        if wf.get("error"):
            status = CloseoutStatus.FAILED.value
        elif errors:
            status = CloseoutStatus.PARTIAL.value
        else:
            status = CloseoutStatus.COMPLETE.value

        return [
            _result(
                adapter=CloseoutAdapterKind.TODO.value,
                status=status,
                mutation=f"in-process todo-close: {todo_id} workflow_state=done",
                error="; ".join(errors) or None,
            )
        ]


class PlanPhaseCloseoutAdapter:
    def apply(
        self, closeout: ImplementCloseout, *, source: Source
    ) -> list[AdapterResult]:
        rt = get_runtime()
        plan_ref = source.parent_ref
        if not plan_ref:
            return [
                _result(
                    adapter=CloseoutAdapterKind.PLAN_PHASE.value,
                    status=CloseoutStatus.FAILED.value,
                    error="parent_ref missing for plan_phase closeout",
                )
            ]
        phase_id = source.canonical_ref
        attrs = {
            "plan": plan_slug_from_ref(plan_ref),
            "phase": source.selector or phase_id.split("/")[-1],
            "files_modified": closeout.files_modified,
        }
        upsert = rt.dispatch(
            "entity_create",
            {
                "id": phase_id,
                "type": "plan_phase",
                "name": phase_id,
                "attributes": attrs,
                "workflow_state": "done",
            },
        )
        if upsert.get("error"):
            return [
                _result(
                    adapter=CloseoutAdapterKind.PLAN_PHASE.value,
                    status=CloseoutStatus.FAILED.value,
                    error=str(upsert.get("error")),
                )
            ]
        edge = rt.dispatch(
            "relationship_create",
            {
                "source_id": phase_id,
                "target_id": plan_ref,
                "type_id": "child_of",
            },
        )
        status = CloseoutStatus.COMPLETE.value
        error = None
        if edge.get("error"):
            status = CloseoutStatus.PARTIAL.value
            error = str(edge.get("error"))
        slug = plan_slug_from_ref(plan_ref)
        phase_name = source.selector or "phase"
        summary_path = (
            workspaces_root() / "tmp" / "prompts" / slug / f"{phase_name}-summary.md"
        )
        rt.write_text(summary_path, f"# {phase_id}\n\n{closeout.summary}\n")
        return [
            _result(
                adapter=CloseoutAdapterKind.PLAN_PHASE.value,
                status=status,
                mutation=f"upserted {phase_id}; child_of→{plan_ref}",
                error=error,
            )
        ]


class PlanCloseoutAdapter:
    def apply(
        self, closeout: ImplementCloseout, *, source: Source
    ) -> list[AdapterResult]:
        rt = get_runtime()
        plan_id = source.canonical_ref
        slug = plan_slug_from_ref(plan_id)
        rels = rt.dispatch(
            "relationships",
            {"entity_id": plan_id, "type_id": "child_of", "limit": 100},
        )
        linked = []
        if isinstance(rels.get("relationships"), list):
            linked = [
                r.get("source_id")
                for r in rels["relationships"]
                if r.get("target_id") == plan_id
            ]
        deviations: list[str] = []
        status = CloseoutStatus.COMPLETE.value
        if not linked:
            status = CloseoutStatus.PARTIAL.value
            deviations.append(f"no child_of plan_phase edges for {plan_id}")
        wrap_path = (
            workspaces_root()
            / "tmp"
            / "prompts"
            / slug
            / "summaries"
            / f"00-{slug}-wrap-up.md"
        )
        rt.write_text(wrap_path, f"# Arc wrap-up — {plan_id}\n\n{closeout.summary}\n")
        wf = rt.dispatch(
            "entity_update",
            {"entity_id": plan_id, "workflow_state": "done"},
        )
        if wf.get("error"):
            status = CloseoutStatus.PARTIAL.value
            deviations.append(str(wf.get("error")))
        mutation = f"wrap-up at {wrap_path}; phases={len(linked)}"
        error = (
            "; ".join(deviations)
            if deviations and status != CloseoutStatus.COMPLETE.value
            else None
        )
        return [
            _result(
                adapter=CloseoutAdapterKind.PLAN.value,
                status=status,
                mutation=mutation,
                error=error,
            )
        ]


class PacketCloseoutAdapter:
    def apply(
        self, closeout: ImplementCloseout, *, source: Source
    ) -> list[AdapterResult]:
        rt = get_runtime()
        packet_ref = source.canonical_ref
        packet_path = packet_ref.removeprefix("packet:")
        report_path = (
            workspaces_root()
            / "tmp"
            / "reviews"
            / f"{packet_path.replace('/', '_')}-closeout.md"
        )
        body = f"# Closeout — {packet_ref}\n\n{closeout.summary}\n"
        rt.write_text(report_path, body)
        results = [
            _result(
                adapter=CloseoutAdapterKind.PACKET.value,
                status=CloseoutStatus.COMPLETE.value,
                mutation=f"report at {report_path}",
            )
        ]
        embedded = _embedded_from_packet(packet_path)
        if embedded:
            from implement_admission.closeout import run_composition

            results.extend(run_composition(closeout, [source_from_ref(embedded)]))
        return results


class AgentBusCloseoutAdapter:
    def apply(
        self, closeout: ImplementCloseout, *, source: Source
    ) -> list[AdapterResult]:
        rt = get_runtime()
        thread_id = thread_id_from_bus_ref(source.canonical_ref)
        sidecar = (
            cortex_files_root()
            / "notes"
            / "system"
            / "threads"
            / f"{thread_id}-closeout.md"
        )
        rt.write_text(
            sidecar, f"# Closeout — thread {thread_id}\n\n{closeout.summary}\n"
        )
        reply = rt.agent_bus_reply(
            thread_id,
            "Implement closeout",
            f"Closeout recorded: cortex://notes/system/threads/{thread_id}-closeout.md",
            "pipeline:implement-closeout",
        )
        status = CloseoutStatus.COMPLETE.value
        error = None
        if reply.get("error"):
            status = CloseoutStatus.FAILED.value
            error = str(reply.get("error"))
        results = [
            _result(
                adapter=CloseoutAdapterKind.AGENT_BUS.value,
                status=status,
                mutation=f"sidecar {sidecar}",
                error=error,
            )
        ]
        embedded = (
            closeout.source_ref if closeout.source_ref != source.canonical_ref else None
        )
        if embedded:
            try:
                from implement_admission.closeout import run_composition

                results.extend(run_composition(closeout, [source_from_ref(embedded)]))
            except Exception as exc:
                results.append(
                    _result(
                        adapter=CloseoutAdapterKind.MIXED.value,
                        status=CloseoutStatus.FAILED.value,
                        error=str(exc),
                    )
                )
        return results


def _embedded_from_packet(packet_path: str) -> str | None:
    root = workspaces_root()
    candidate = root / packet_path
    if not candidate.is_file():
        return None
    return extract_embedded_source_ref(
        candidate.read_text(encoding="utf-8", errors="replace")
    )


ADAPTER_INSTANCES = {
    CloseoutAdapterKind.TODO.value: TodoCloseoutAdapter(),
    CloseoutAdapterKind.PLAN.value: PlanCloseoutAdapter(),
    CloseoutAdapterKind.PLAN_PHASE.value: PlanPhaseCloseoutAdapter(),
    CloseoutAdapterKind.PACKET.value: PacketCloseoutAdapter(),
    CloseoutAdapterKind.AGENT_BUS.value: AgentBusCloseoutAdapter(),
}
