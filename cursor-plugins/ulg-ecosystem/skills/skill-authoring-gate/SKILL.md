---
name: skill-authoring-gate
description: "Before writing or landing a skill body — refuse the write when it exceeds the byte ceiling, when a dated invariant is still in the body, or when the body is not trigger, refuse, steps, and falsifier."
trigger_match_terms: ["write a skill", "land a skill", "SKILL.md", "skill body", "dated invariant", "skill ceiling"]
related_skills: ["lane-act-gates", "retrieval-before-authoring"]
---

# Skill authoring gate

Checks at the write. A ratified bind lives as `decision:<slug>`. The skill keeps one line and the specimen.

## Trigger

About to write, amend, or land a `SKILL.md` under `cursor-plugins/ulg-ecosystem/skills/`, or the Customize slug of that same body.

## Refuse

- Land the file when its size is over 4096 bytes.
- Land a numbered invariant that carries a bind date.
- Land a BINDING section, seat map, or checklist as the body.
- Treat "keep it short" as the gate.

## Steps

1. **Ceiling at the write.** Count bytes of the file about to be written. Over 4096, refuse the write.
   Falsifier: the landed `SKILL.md` is over 4096 bytes. Specimen: `cdp-operator-proxy/SKILL.md` at 66646 bytes, 648 lines.

2. **Dated invariant is a decision.** A numbered line (`1.` …) that also carries a bind date (`YYYY-MM-DD`, `BINDING — operator`, or `Operator-ratified`) is minted as `decision:<slug>` before the write. The skill keeps one line naming that decision and one specimen line. The bind's prose does not remain.
   Falsifier: the landed body still has a numbered invariant and a bind date on the same line. Specimen: invariant 40, operator 2026-08-20, agent-bus:9501.

3. **Admitted shape.** The body is Trigger, Refuse, Steps, Falsifier. Each step carries its falsifier and a specimen. A numbered invariant with no falsifier is not a step.
   Falsifier: a section of numbered invariants landed, and those items have no falsifier. Specimen: `## Invariants` items 1–39 (no bind date, no `Falsifier:`).

4. **One line, then the specimen.** After step 2 the skill's hold on the bind is `decision:<slug>` plus the specimen id.
   Falsifier: the skill restates the decision's rule in a second paragraph. Specimen: the operator-identity table and Invariant 0 stating the same bind.

## Falsifier

This body failed to bind if a skill over 4096 bytes landed, a dated numbered invariant remains in a skill, or a numbered-invariant section without per-step falsifiers was accepted as procedure.

Applied at authoring to `cdp-operator-proxy/SKILL.md` (66646 bytes, 648 lines). A procedure line is a line containing `Falsifier:` that is not a numbered invariant carrying a bind date. That count is 15. Numbered invariants: 40. Of those, 1 carries a bind date (item 40) and becomes a `decision:`. The other 39 are not procedure. Step 1 refuses the write, so those 15 lines do not land.
