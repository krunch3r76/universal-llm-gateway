# Archived code

Retired packages under this directory are not on the Python import path for production.
They are kept for reference and forensic replay only.

## charter_runner

Former location: `scripts/model_manager/ui/controller/charter_runner/`.

The in-process charter tick was excised in todo:excise-archive-charter-runner (S1–S3).
Propagation proof and store logic live outside this tree; see `ui/controller/propagation_proof.py`
and `libs/charter_runner_store/`.
