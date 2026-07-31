# Question

Should the closeout relay re-project an executor's canonical closeout into a
fixed eleven-field table, or should it relay a status plus `source_ref`?

The question is unchanged. The investigation distinguishes a terse, derived
summary from a second representation of executor judgment.

# What you found

- The sidecar is already the richest executor-authored artifact. The relay reads
  `tmp/reviews/closeouts/<dispatch_id>.md` and gives an authored §2 sidecar
  priority over the SDK wrapper, then passes it through `project_section2_table`
  (`services/git_integration_worker/cursor_auto/closeout_relay.py:222-236`,
  `:239-350`).
- `project_section2_table` makes a second representation by selecting a fixed
  `SECTION2_FIELDS` list and extracting each cell. When a field cannot be
  extracted, the current implementation emits a relay-local parse observation
  and source pointer, not a claim about the author
  (`services/git_integration_worker/cursor_auto/closeout_relay_project.py:44-55`,
  `:58-88`; `services/git_integration_worker/cursor_auto/closeout_relay_common.py:124-126`).
- Commit `4f7367ff0501fe7346cef5b06b2493d0e681902c` removed the earlier hardcoded
  “unauthored” defaults and adds `source_ref` to synthesized payloads. Its
  predecessor contains those defaults in
  `closeout_relay_project.py:_RELAY_FIELD_DEFAULTS` and
  `closeout_relay.py:synthesize_section2`; those values were assertions about an
  unread or unparseable author field, not observations.
- The projector still truncates and normalizes. A relay body above 2,000
  characters is cell-budgeted and eventually sliced, then appended with a full
  closeout pointer (`services/git_integration_worker/cursor_auto/closeout_relay_briefing.py:157-206`,
  `:283-305`). This is necessarily lossy even when it is honest.
- The relay has accrued compensating policy: synthesized closeouts are forced
  `partial` (`services/git_integration_worker/cursor_auto/relay_trust.py:33-37`);
  machine-captured write URIs can overwrite an empty prose `effects` field and
  clamp status (`services/git_integration_worker/cursor_auto/closeout_relay_effects.py:406-429`);
  missing reporting fields can add deviations and downgrade a blind caller
  (`services/git_integration_worker/cursor_auto/closeout_relay_reporting.py:99-163`).
  These guards are useful, but they are evidence that the projector has become
  a second semantic authority.
- The reported “first AC only plus empty `files_modified`” symptom is not one
  proven root cause. The table extractor now has fixtures for multi-AC sidecars
  and bold table headings (`services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py:689-801`,
  `:1236-1307`). Separately, the crack-specimen wrapper deliberately has
  `files_modified: []` with `capture_status: "partial"` while the sidecar names
  the changed file (`:1105-1160`). Capture code also permits
  `work_outcome=shipped`, `status=complete`, and
  `capture_status=unavailable` together (`services/git_integration_worker/tests/test_cursor_sdk_capture_status.py:706-750`).
  Therefore empty capture fields must be presented as capture observations with
  their status, not used to negate the sidecar. The supplied fixtures do not
  reproduce a current first-AC-only loss after `4f7367ff`; a real failing
  dispatch ID and its sidecar/wrapper pair are required to localize any
  remaining failure.

# What you changed

- No production relay behavior was changed by this workstream. This is a
  deletion decision, not a justification for another parser patch.
- Baseline behavior was changed before this investigation by
  `4f7367ff0501fe7346cef5b06b2493d0e681902c`: parser misses now identify the
  relay's inability to locate a field and point to `source_ref`.
- Verdict: delete the eleven-field prose re-projection as the normal bus
  representation. Relay a small typed envelope containing
  `status`, `status_basis`, `closeout_source`, `source_ref`,
  `capture_status`, and bounded machine-observed `effects`; carry the canonical
  sidecar unchanged at `source_ref`. A status may remain derived only when its
  derivation inputs and rule are named. This preserves the small-turn benefit
  without duplicating executor judgment.

# What you did NOT change and why

- I did not delete the projector in this shared-worktree investigation. It is
  live protocol behavior with callers and policy clamps outside the named six
  files (for example `nested_outcome.py:172-278`); deleting it safely requires
  an API/transport migration owned with Workstream A's response-schema work.
- I did not treat empty `files_modified` as evidence of no file edits. The
  wrapper fixture and capture-status tests show that it can mean partial or
  unavailable capture, while the sidecar remains the canonical authored
  account.
- I did not add another extractor heuristic. That would reduce one known
  parser miss while retaining the duplicate-truth failure class.

# PROPAGATION REQUIRED

- `git_integration_worker` at this workstream's report commit: no runtime code
  changed, so no restart is needed for this investigation artifact.
- For the already-landed relay fix
  `4f7367ff0501fe7346cef5b06b2493d0e681902c`, restart
  `git_integration_worker`; it is otherwise landed-not-live.
- Any future deletion/schema migration will also require the bus/API surface
  identified by Workstream A, in addition to `git_integration_worker`.

# Open questions and residuals

- Does a current dispatch still produce first-AC-only projection after
  `4f7367ff`? Settle with one actual dispatch ID, its exact sidecar, wrapper
  manifest, relayed body, and the running worker's code version.
- Can all consumers dereference `workspaces://` `source_ref` without another
  round trip or authorization boundary? Settle with Workstream A's endpoint and
  client inventory. If not, the replacement needs a bounded immutable excerpt,
  explicitly labelled as an excerpt rather than a second closeout.
- What typed envelope can replace the Markdown table without maintaining a
  parallel schema? Settle by having Workstream A define the response contract
  and source/provenance fields, then migrate the relay and its consumers in one
  change.
