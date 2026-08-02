---
name: orchestrator-core
description: "Load on entering ANY lead/orchestrator session where you hold the operator goal and can delegate — domain-neutral decompose, fan-out, adjudicate, close-back core."
trigger_match_terms: ["orchestrator-core", "orchestrate", "orchestration", "lead", "fan-out", "fanout", "delegate", "delegation", "fork", "sub-agent", "decompose", "adjudicate", "close-back", "closeback", "context-conservation", "coordinate", "monitor", "conform", "converse", "binding-table", "composition-seam", "work-stream", "research-synthesis", "multi-doc-review", "steelman"]
related_skills: ["orchestrator-workflow", "consult-routing", "dispatch-shape", "dispatch-workflow", "handoff-packet-authoring", "agent-bus-discipline", "operator-posture", "consensus-steelman-posture", "session-close", "lead-seat-boot"]
skill_class: workflow
skill_category: dispatch-delegation
lifecycle: active
canonical: workspaces://universal-llm-gateway/.cursor/skills/orchestrator-core/SKILL.md
---

# Orchestrator Core

Domain-neutral lead orchestration. A specialization (coding = `orchestrator-workflow`; life-domain/research/scheduling = local binding) composes executor mechanics onto this core.

## Scope

`skill_class = workflow`. This skill owns domain-neutral `orchestrat*` / `delegat*` triggers.

`scope(this) = decompose→fan-out→adjudicate→close-back ∧ context_conservation ∧ delegation_grammar ∧ composition_seam ∧ {execute, conform, converse, coordinate, monitor}`.

Out of scope: atomic transport/call mechanics (`dispatch-shape`, `dispatch-workflow`, `consult-routing`), coding intake/repo mechanics (`orchestrator-workflow`), material-decision ratification (`consensus-steelman-posture`).

## Core invariant

`orchestration = lead conserves context`. The lead holds goal + arc + synthesis; forks carry heavy reading, authoring, verification legs, and durable deliverables.

`lead_session ⇒ orchestrator_session`. `operator_goal ⇒ lead + N bounded_work_streams`. Default: `∀ fork : |bounded_deliverable(fork)| = 1`.

Forks are deliberately blind to the full orchestration. Close the gap with explicit close contracts, not by making forks orchestration-aware.

## Composition skeleton

1. **Decompose** — goal ⇒ orchestrator + bounded work-streams.
2. **Fan out** — `lead_context := minimize`; push heavy reading/authoring/durable writes to forks.
3. **Fork protocol** — lean boot → priming → bounded work → durable deliverable → closing pointer on fork result thread. `fork ⇏ close(own_thread)` unless instructed.
4. **Adjudicate** — collect pointers, inspect durable artifacts, verify, synthesize.
5. **Close back** — author synthesis; close fork threads; record structural tooling/process frictions with `friction()` when applicable.

Ordering: `investigate/clarify ⇒ act ⇒ verify`. Never act before judgment-heavy investigation lands on the lead.

Close contract: every dispatch deliverable spec MUST require `durable_artifact + closing_pointer`. Lead collects pointers, never inline walls.

## Context conservation

- Offload breadth search / drafting / one-off consults; pull back only `locator + verdict` or the decision. Keep load-bearing reread on-seat.
- Coordinate mutable interdependent state through one durable addressable surface; never hold that state only in lead context.
- Long operation rule: `operation.shape_depends_on(shared_surface) ⇒ resync(shared_surface) before ∧ after`.

## Delegation grammar

- Route by `capability × cost_to_operator`, not capability alone. Zero-operator-cost autonomous executor = default workhorse. Manual/nonzero-cost channels = substantial context-heavy work, batched.
- Reasoning/conformance line: open-design judgment stays above the line on a reasoning tier. Environment-wall work (run worker, inspect live artifact, sweep state, execute harness) becomes a mechanical limb below the line. Compose reasoner + limb; never collapse judgment onto a mechanical executor that optimizes for appearing done.
- Recon-first with scope guard: first use cheap autonomous retrieval/scaffolding to gather anchors. Label outputs `candidate — re-derive, do not elaborate`. A recon that embeds design decisions can anchor the reasoner incorrectly.
- Low-judgment executor instructions MUST be determinate, constraint-repeated, self-checked, and destructive-op-gated.


- **D1 determinacy binds operator-facing steps too**, not only cursor-sdk dispatch turns. Every operator action step must carry the exact copy-pasteable command (name file + symbol + exact value; bind every fork) — a prose-only step without a runnable command is under-executed, because the operator executes exactly what is given (22055).

## Adjudication discipline

- `¬trust(fork_self_report)`. Lead verifies deliverables by direct inspection of the durable artifact.
- Verify presented options against live state before choosing; unverified option sets create false dichotomies.
- Intake preflight for gated deliverables returns ALL unmet gates at once; avoid serial onion-peel failures.
- Acceptance contract density: bounded deliverable spec must be dense enough that executor judgment is not required.
- `declared_shape ⇔ actual_shape ⇔ delivery_channel`; mismatch = routing error.
- Commit approved mutating fork output to the authoritative surface before redispatch; stale derived state drops approved changes.
- Read fork outputs addressably/selectively, not in bulk.
- Use one durable coordination surface; deliverables land there + closing pointer.

## Composition seam

A specialization fills each abstract fork-class with executor, intake gates, close-contract mechanics, and instruction standard.

| Fork class | Function | Coding binding | Life/research binding |
|---|---|---|---|
| autonomous mechanical limb | zero-cost recon/retrieval/verification/harness below conformance line | cursor-sdk | retrieval / scheduling / email API agent |
| reasoning fork | open-design judgment; may dispatch a limb | web-consult / Opus | domain reasoning consult |
| manual reasoning handoff | nonzero operator push; batch substantial work | web-claude push | same channel, different corpus |
| second-opinion role | independent one-off frontier verdict | reviewer / skeptic | domain-neutral |
| monitor/recovery fork | watch shared/external state; escalate drift | CI/watch/re-sweep | portal/calendar/inbox watcher |
| evidence-curator | auditable provenance packet: source ledger, authority/freshness, retrieval paths, observed-vs-synthesis, confidence labels | cursor-sdk or higher synthesis tier | provenance-disciplined retrieval agent |

## Delegation modes

1. `execute` — bounded deliverable; collect durable result.
2. `conform` — mechanical limb shapes loose intent into structured item; no judgment.
3. `converse` — reasoning worker runs bounded turn-capped clarification; returns resolved spec.
4. `coordinate` — forks operate on shared durable state; lead reconciles through the surface.
5. `monitor` — watch after deliverable; escalate drift/exception into adjudication.

`conform` / `converse` / `coordinate` / `monitor` name lanes; server contracts are deferred unless another skill/tool implements them.

## Standing root threads

`standing_root_orchestration_thread ⇒ charter carries {Lessons & frictions, Pertinent skills, Session-effectiveness hints}`.

Birth a standing root when known upfront: multi-session, multi-seat independent deliverables, multi-wave, child forks, or resume-from-chat-alone would lose state. Migrate when density signals appear: second wave, second seat WIP, active item at close, child spawned, roadmap needed, or lead must summarize for future session. Exempt: single-step same-session fan-out consumed before close with no durable sequencing; exemption ends on context-window cross, new wave, unresolved item at close, or roadmap need.

Lessons/frictions lifecycle: `live → recurring → resolved → retire`. Per-arc lessons stay on charter. Cross-arc procedures live on `workflow:*` entities and are referenced by charters. **Remit:** L&F = behavioral guards / recurring protocol lessons — **not** the mid-flight work-item parking lot. Parked feature asks and non-gating tangents live on the scoreboard **Tangentials** lane (`cortex://notes/system/templates/charter-scoreboard.md`), then graduate to `todo:`/`friction` or drop — do not stuff them into L&F (Fable unify F2, agent-bus:5201).


- **Promotion is mid-session and late by default.** Under context pressure (a fresh turn is needed to conserve context), promote to a standing root *at checkpoint/resume*, not at boot. The mechanic is **promote the existing arc thread in place** — retro-create the parent bus thread and adopt the arc's child threads — **not** mint a new separate orchestrator thread; the spawn-a-fresh-thread reflex is an over-engineered misfire, and promote-in-place preserves the arc's full history on one thread. Source: `insight:orchestration-thread-promotion` (22489 / 22487).

## Compose by reference

Load, do not restate: `orchestrator-workflow` (coding specialization), `consult-routing`, `dispatch-shape`, `dispatch-workflow`, `handoff-packet-authoring`, `agent-bus-discipline`, `operator-posture`, `consensus-steelman-posture`, `session-close`, `lead-seat-boot`.

## Minimal operating summary

Lead conserves context: decompose bounded work, fan out heavy context, require durable deliverables + closing pointers, verify artifacts independently, synthesize, close back, and keep shared mutable state on durable surfaces rather than in the lead window.
