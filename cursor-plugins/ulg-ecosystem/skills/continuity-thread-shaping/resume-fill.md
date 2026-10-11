# Resume fill sources — load when speaking the resume

Slot **order** = `operator-posture` Rule 3. This file owns **fill sources**. The imprint line is the only slot `continuity-thread-shaping` originates — spoken form of "the imprint is already on the graph." It follows the picture. It does not open it.

| Slot | Filled from | ¬ |
|---|---|---|
| 1 Aim | card `## Why this house`, plus the defect the tip established | next-pickup · a path · "follow the reply" · slug · seeded todo |
| 2 In / Out | card `## Objective` | a hop written as the objective · widen silently |
| 3 Where we left off | `mission.tape_tail` (notices dropped when the letter is bound; `mission.tape_tail_source` names the source). When source is `letter`, quote `letter.where_we_left_off` (`TAPE_LEFT_OFF_LETTER` in code: "nothing after the letter but relays; where we left off is letter.where_we_left_off"). Empty pour: `tape_left_off_gap` when set ("the last few turns aren't sealed yet, so I'm going from the checkpoint"); otherwise say the pour has no recent exchange and you are going from the checkpoint | skip the beat · quote the checkpoint command · a paraphrase that is not the chat · the receipts line standing in for the quote |
| 4 Rules I'm holding | Read `bundle.rulings` / `rulings.items` after the letter, before standing on any letter ruling. Speak `mission.rules_held.rows` in brief (`mission.rules_held.source`). Not a receipt | skip · recite all rulings verbatim · invent rules |
| 4b Memos to check | Only when `mission.memos_to_check.shown > 0`. Name each memo ref with one line and what you will do. Read the full turn when `body_truncated` is true. Run `bundle.memos_to_check.mark_read` only after acting or explicitly deferring each memo | skip when shown is 0 · mark read before acting · paraphrase a truncated body |
| 5 Already | what the tip says landed, and the gap that left the house open | a passing test spoken as the live check · `## Windows` · linear thread · repeating this as Been |
| 6 Object | `checkpoint_object`, else tip residue `Object:` — one noun phrase, then the order of moves | the hop itself · an object that lives only on another house |
| 7 Are / Going | tip state; Going = the current move only (`Next`) | Been · later moves inside Going · scoreboard paste |
| 8 `In one line:` | the aim again — outcome and constraint together | the hop · omit the label |
| 8b Lane rules | card `## Rules` + named `runbook:*` bodies (walk the pointed todo; ¬ treat a status sentence on the runbook as SOT) | omit and follow a global skill omit-path |
| 9 Receipts | one skippable line after the picture: tip CHECKPOINT turn# · `seal_status` · `summary_row_source` · `fence_id` · `head_sha` · leftover imprint assertion/edge ids. Label it a receipt. The fence id and the commit hash are this line. This is not the proof the tape was read | these facts leading the reply · chat as memory |
| 9b Skills Used | `mission.skills_to_use` slugs actually Used before the first governed act (or `none` when the list was empty) | listing the card row without a Use · inventing slugs not in the pour |
| 10 What I need from you | `OPERATOR_GATE` / `HOLD_MERGE` · hub deadlines | model-seat work · direction quiz |
