# /consult-review

The `code-review` pipeline is removed. This command no longer gathers files and
posts them to model `code-review`.

## Where the review runs

**In this tab.** Apply `review-task-guidance` Code Review Dimension to the
current modified and untracked files. Do not dispatch.

**Dispatch.** A code review that leaves the tab is:

`team_dispatch(op=generate, model=cdp/opus-5.5, purpose=review, contract=none)`

`cdp/opus-5` is the same class. `contract=none`. There is no `contract=review`.
`role=reviewer` is not this request. `scripts/consult -r reviewer` exits with
that shape. `scripts/consult_review_submit.py` exits the same way.

Packet body is `review-task-guidance` Code Review Dimension. The reviewer reads
whole files. Verdicts are ADMIT / RATIFY / REJECT / RETURN / SCOPE-DRIFT.

Apply accepted fixes with `/review-apply`.
