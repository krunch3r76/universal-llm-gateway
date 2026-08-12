"""Unit tests for CDP model-endpoint adapter + staging."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from claude_bundles.cdp_model_endpoint import (
    CDP_SUBSTRATE,
    SUBMIT_RETRY_BACKOFF_S,
    UPSTREAM_OVERLOADED,
    _has_proof,
    _has_unresolved_artifact_card,
    _is_chrome_only_body,
    _is_overload_only_harvest,
    _is_user_prompt_echo_body,
    has_proof,
    picker_from_model_id,
    run_cdp_generate,
)
from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    StagedPrompt,
    stage_cdp_prompt_with_skills,
    stage_prompt_uri,
    sweep_ephemeral,
)


def test_picker_from_model_id_passthrough() -> None:
    assert picker_from_model_id("cdp/opus-4.8") == "opus-4.8"
    assert picker_from_model_id("cdp/fable-5") == "fable-5"
    assert picker_from_model_id("cdp/fable") == "fable-5"


def test_stage_prompt_text_writes_ephemeral(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    staged = stage_prompt_uri(execution_id="exec-1", prompt_text="hello CDP")
    assert staged.staged is True
    assert (
        staged.prompt_uri
        == "cortex://notes/system/ephemeral/cdp-endpoint/exec-1/prompt.md"
    )
    on_disk = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-1/prompt.md"
    assert on_disk.read_text(encoding="utf-8") == "hello CDP"
    assert sweep_ephemeral("exec-1") is True
    assert not on_disk.exists()


def test_ensure_cdp_judgment_skills_prepends_missing() -> None:
    from claude_bundles.cdp_model_endpoint_staging import ensure_cdp_judgment_skills

    assert ensure_cdp_judgment_skills(None) == ["reasoning-posture"]
    assert ensure_cdp_judgment_skills([]) == ["reasoning-posture"]
    assert ensure_cdp_judgment_skills(["consult-posture"]) == [
        "reasoning-posture",
        "consult-posture",
    ]
    assert ensure_cdp_judgment_skills(
        ["reasoning-posture", "consult-posture", "path-sim"]
    ) == ["reasoning-posture", "consult-posture", "path-sim"]
    # Slash-prefixed caller entry still counts as present (``have`` lstrips "/").
    assert ensure_cdp_judgment_skills(["/reasoning-posture"]) == ["/reasoning-posture"]


def test_stage_cdp_prompt_with_skills_prepends_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-skills",
        prompt_text="## Task\n",
        skills=["reasoning-posture", "consult-posture"],
    )
    on_disk = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills/prompt.md"
    text = on_disk.read_text(encoding="utf-8")
    # Judgment skill is always on, even when the caller lists others.
    assert text.startswith("/reasoning-posture\n/consult-posture\n")
    assert "## Task" in text
    assert staged.prompt_uri.endswith("exec-skills/prompt.md")


def test_stage_cdp_prompt_omitted_skills_still_gets_judgment_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Light-bounded / skills=None still attaches the judgment skill."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-skills-default",
        prompt_text="## light ask\n",
        skills=None,
    )
    on_disk = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-default/prompt.md"
    )
    text = on_disk.read_text(encoding="utf-8")
    assert text.startswith("/reasoning-posture\n")
    assert "## light ask" in text
    assert staged.staged is True


def test_stage_cdp_prompt_with_skills_rejects_path_sim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """a:27430 — path-sim on CDP skills= fails closed; judgment skill still stages."""
    from claude_bundles.cdp_model_endpoint_staging import (
        ensure_cdp_judgment_skills,
        reject_cdp_skills_path_sim,
    )

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    with pytest.raises(CdpStagingError) as excinfo:
        stage_cdp_prompt_with_skills(
            execution_id="exec-skills-path-sim",
            prompt_text="## architect bind\n",
            skills=["path-sim", "reasoning-posture", "consult-posture"],
        )
    assert excinfo.value.code == "cdp_skills_path_sim_rejected"
    assert "path-sim" in excinfo.value.reason

    # Same reject when path-sim is the only caller skill (judgment slug would merge).
    with pytest.raises(CdpStagingError) as excinfo2:
        reject_cdp_skills_path_sim(["path-sim"])
    assert excinfo2.value.code == "cdp_skills_path_sim_rejected"

    # Judgment skill alone still stages (codework / architect default).
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-skills-judgment-ok",
        prompt_text="## architect bind\n",
        skills=["reasoning-posture", "consult-posture"],
    )
    on_disk = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-judgment-ok/prompt.md"
    )
    text = on_disk.read_text(encoding="utf-8")
    assert text.startswith("/reasoning-posture\n/consult-posture\n")
    assert "path-sim" not in text
    assert "## architect bind" in text
    assert staged.staged is True
    assert ensure_cdp_judgment_skills(["consult-posture"]) == [
        "reasoning-posture",
        "consult-posture",
    ]


def test_stage_cdp_prompt_with_skills_inlines_cursor_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-skills-mixed",
        prompt_text="## Task\n",
        skills=["investigation-economy", "reasoning-posture"],
    )
    on_disk = tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-skills-mixed/prompt.md"
    text = on_disk.read_text(encoding="utf-8")
    assert text.startswith("/reasoning-posture\n")
    assert '<skill slug="investigation-economy"' in text
    assert "## Task" in text
    assert staged.prompt_uri.endswith("exec-skills-mixed/prompt.md")


def test_stage_cortex_passthrough(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    uri = "cortex://notes/system/threads/already.md"
    staged = stage_prompt_uri(execution_id="exec-2", prompt_uri=uri)
    assert staged.staged is False
    assert staged.prompt_uri == uri
    assert staged.ephemeral_root is None


def test_stage_cdp_presealed_cortex_uri_passes_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A caller URI already carrying the effective manifest is not rewritten."""
    from claude_bundles.cowork_skill_delivery import prepend_cdp_dispatch_skills

    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    sealed, _, _ = prepend_cdp_dispatch_skills(
        "## pre-staged ask\nbody\n", ["reasoning-posture"]
    )
    src = tmp_path / "notes/system/specs/presealed.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(sealed, encoding="utf-8")
    uri = "cortex://notes/system/specs/presealed.md"

    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-presealed", prompt_uri=uri, skills=None
    )

    assert staged.prompt_uri == uri
    assert staged.staged is False
    assert staged.ephemeral_root is None
    assert not (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-presealed/prompt.md"
    ).exists()
    assert src.read_text(encoding="utf-8") == sealed


def test_stage_cdp_bare_cortex_uri_is_rewritten_with_rails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail-closed: a URI missing the judgment rail is rewritten, not passed through."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    src = tmp_path / "notes/system/specs/bare.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("## bare ask\nno rails here\n", encoding="utf-8")

    staged = stage_cdp_prompt_with_skills(
        execution_id="exec-bare", prompt_uri="cortex://notes/system/specs/bare.md",
        skills=None,
    )

    assert staged.staged is True
    assert staged.prompt_uri.endswith("exec-bare/prompt.md")
    text = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-bare/prompt.md"
    ).read_text(encoding="utf-8")
    assert text.startswith("/reasoning-posture\n")
    assert "## bare ask" in text


def test_stage_workspaces_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cortex = tmp_path / "cortex"
    ws = tmp_path / "ws" / "universal-llm-gateway"
    cortex.mkdir()
    ws.mkdir(parents=True)
    src = ws / "prompt.md"
    src.write_text("from workspaces", encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex))
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    staged = stage_prompt_uri(
        execution_id="exec-3",
        prompt_uri="workspaces://universal-llm-gateway/prompt.md",
    )
    assert staged.staged is True
    dest = cortex / "notes/system/ephemeral/cdp-endpoint/exec-3/prompt.md"
    assert dest.read_text(encoding="utf-8") == "from workspaces"


def test_stage_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    with pytest.raises(CdpStagingError) as exc:
        stage_prompt_uri(
            execution_id="exec-4",
            prompt_uri="workspaces://universal-llm-gateway/missing.md",
        )
    assert exc.value.code == "cdp_prompt_unstageable"


class _FakeClient:
    """Minimal httpx-shaped client for adapter unit tests."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def request(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> _FakeResp:
        self.calls.append((method, url))
        if method == "POST" and "/abort" in url:
            return _FakeResp({"ok": True, "status": "aborted"})
        if not self._responses:
            raise AssertionError(f"unexpected request {method} {url}")
        payload = self._responses.pop(0)
        return _FakeResp(payload)

    def close(self) -> None:
        return None


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.content = b"{}" if payload else b""
        self.status_code = int(payload.get("_status_code", 200))
        self.text = ""

    def raise_for_status(self) -> None:
        if self._payload.get("_http_error"):
            raise RuntimeError("http error")

    def json(self) -> dict[str, Any]:
        return {k: v for k, v in self._payload.items() if not k.startswith("_")}


def _mock_run_cdp_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, execution_id: str
) -> None:
    """Avoid skill-catalog SOT validation in sparse git worktrees."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")

    def _fake_stage(**_kwargs: Any) -> StagedPrompt:
        return StagedPrompt(
            prompt_uri=(
                f"cortex://notes/system/ephemeral/cdp-endpoint/{execution_id}/prompt.md"
            ),
            ephemeral_root=None,
            staged=True,
        )

    monkeypatch.setattr(
        "claude_bundles.cdp_model_endpoint.stage_cdp_prompt_with_skills",
        _fake_stage,
    )


def test_run_cdp_generate_proof_before_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-1", "status": "running"},
            {
                "execution_id": "sat-1",
                "status": "running",
                "completion_phase": "running",
                "body_len": 0,
            },
            {
                "execution_id": "sat-1",
                "status": "complete",
                "completion_phase": "content_proof",
                "content_proof_uri": "cortex://notes/system/threads/proof.md",
                "content_proof_sha256": "abc",
                "body": "harvested",
                "body_len": 9,
            },
        ]
    )
    sleeps: list[float] = []
    submit_bodies: list[dict[str, Any]] = []

    _orig_request = client.request

    def _capture(method: str, url: str, json: dict[str, Any] | None = None) -> Any:
        if method == "POST" and json is not None:
            submit_bodies.append(json)
        return _orig_request(method, url, json=json)

    client.request = _capture  # type: ignore[method-assign]
    result = run_cdp_generate(
        execution_id="dispatch-1",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        harvest_source="output-file",
        expected_size="large",
        download_output=True,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )
    assert result.ok is True
    assert result.body == "harvested"
    assert result.substrate == CDP_SUBSTRATE
    assert result.cost_source == "unavailable"
    assert result.content_proof_uri is not None
    assert result.picker_model == "opus-4.8"
    # submit + 2 polls
    assert len(client.calls) == 3
    assert submit_bodies[0]["harvest_source"] == "output-file"
    assert submit_bodies[0]["expected_size"] == "large"
    assert submit_bodies[0]["download_output"] is True


def test_run_cdp_generate_stall_wall_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-2", "status": "running"},
            {
                "execution_id": "sat-2",
                "status": "running",
                "completion_phase": "running",
                "body_len": 0,
                "liveness_observed_at": "t1",
            },
        ]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(_s: float) -> None:
        # Jump past max_wall_s after first poll interval.
        clock["t"] = 50.0

    result = run_cdp_generate(
        execution_id="dispatch-2",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=10,
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is False
    assert result.stall_stage == "wall_clock_exceeded"


def test_run_cdp_generate_progress_resets_wall_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cumulative elapsed > max_wall_s with fingerprint deltas still delivers proof (R1)."""
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-r1")
    client = _FakeClient(
        [
            {"execution_id": "sat-r1", "status": "running"},
            {
                "execution_id": "sat-r1",
                "status": "running",
                "completion_phase": "running",
                "body_len": 0,
                "liveness_observed_at": "t0",
            },
            {
                "execution_id": "sat-r1",
                "status": "running",
                "completion_phase": "running",
                "body_len": 1,
                "liveness_observed_at": "t1",
            },
            {
                "execution_id": "sat-r1",
                "status": "running",
                "completion_phase": "running",
                "body_len": 2,
                "liveness_observed_at": "t2",
                "streaming": True,
            },
            {
                "execution_id": "sat-r1",
                "status": "completed",
                "body": "done after long stream",
                "attested_model": "Model: Opus 4.8",
            },
        ]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(_s: float) -> None:
        clock["t"] += 6.0

    result = run_cdp_generate(
        execution_id="dispatch-r1",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=10,
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is True
    assert result.stall_stage is None
    assert clock["t"] >= 18.0


def test_run_cdp_generate_mission_wall_does_not_abort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """purpose=operator-proxy must not Stop-click on max_wall — keep polling to proof."""
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-mission-wc")
    archive = "cortex://notes/system/threads/mission-wc.md"
    client = _FakeClient(
        [
            {"execution_id": "sat-m", "status": "running"},
            {
                "execution_id": "sat-m",
                "status": "running",
                "completion_phase": "running",
                "body_len": 1,
                "liveness_observed_at": "t1",
            },
            {
                "execution_id": "sat-m",
                "status": "completed",
                "archive_uri": archive,
                "body": "mission ok",
                "attested_model": "Model: Opus 5",
            },
        ]
    )
    clock = {"t": 0.0}
    aborts: list[str] = []

    def _now() -> float:
        return clock["t"]

    def _sleep(_s: float) -> None:
        # First sleep jumps past wall; mission path resets and continues to proof.
        if clock["t"] < 1.0:
            clock["t"] = 50.0
        else:
            clock["t"] += 1.0

    from claude_bundles import cdp_model_endpoint as mod

    orig = mod._abort_then_sweep

    def _track(*args, **kwargs):
        aborts.append("abort")
        return orig(*args, **kwargs)

    monkeypatch.setattr(mod, "_abort_then_sweep", _track)

    result = run_cdp_generate(
        execution_id="dispatch-mission-wc",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        purpose="operator-proxy",
        max_wall_s=10,
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is True
    assert result.archive_uri == archive
    assert aborts == []


def test_run_cdp_generate_wall_clock_preserves_archive_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-ok wall_clock_exceeded must retain archive_uri from last status snapshot (B3).

    ``archive_uri`` on a polled snapshot triggers ``ok=True`` via ``_has_proof``,
    so this case carries ``archive_uri`` on the submit ack, polls a running
    snapshot without re-emitting it, then fires wall_clock on the next loop head.
    """
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-wc")
    archive = "cortex://notes/system/threads/archive-wc.md"
    client = _FakeClient(
        [
            {
                "execution_id": "sat-wc",
                "status": "running",
                "archive_uri": archive,
            },
            {
                "execution_id": "sat-wc",
                "status": "running",
                "archive_uri": archive,
            },
        ]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(_s: float) -> None:
        clock["t"] = 50.0

    result = run_cdp_generate(
        execution_id="dispatch-wc",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=10,
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is False
    assert result.stall_stage == "wall_clock_exceeded"
    assert result.archive_uri == archive


def test_has_proof_archiving_phase_not_failed() -> None:
    """Archiving + content_proof_uri counts; failed phase must not (B4)."""
    proof_uri = "cortex://notes/system/threads/proof-arch.md"
    assert _has_proof(
        {"completion_phase": "archiving", "content_proof_uri": proof_uri}
    )
    assert not _has_proof(
        {"completion_phase": "failed", "content_proof_uri": proof_uri}
    )


def test_has_proof_rejects_083e6e4a_echo_archive() -> None:
    """AC-S1-b negative: prompt-echo archive with attested_model None is not proof."""
    echo_body = (
        "You said: /reasoning-posture\n\n"
        "/reasoning-posture\n"
    )
    snap = {
        "status": "completed",
        "completion_phase": "terminal",
        "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-new-083e6e4a.md",
        "body": echo_body,
        "attested_model": None,
        "harvest_provenance": "chat",
    }
    assert _is_user_prompt_echo_body(echo_body)
    assert _is_chrome_only_body(echo_body)
    assert has_proof(snap) is False


def test_has_proof_accepts_assistant_with_attested_model() -> None:
    """AC-S1-b positive: real assistant turn + attested_model is proof."""
    snap = {
        "status": "completed",
        "body": "Here is the pharmacology analysis you requested.",
        "attested_model": "Model: Fable 5 High",
        "harvest_provenance": "chat",
    }
    assert has_proof(snap) is True


def test_has_proof_outputs_path_requires_content_proof_uri() -> None:
    """Outputs provenance without content_proof_uri is not terminal proof."""
    snap = {
        "harvest_provenance": "output-file",
        "archive_uri": "cortex://notes/system/threads/out.md",
        "body": "structured output",
        "attested_model": "Model: Fable 5",
    }
    assert has_proof(snap) is False
    snap["content_proof_uri"] = "cortex://notes/system/threads/proof.md"
    assert has_proof(snap) is True


def test_has_proof_rejects_superseded_archive_only() -> None:
    """Archive under _superseded/ without attested_model or content_proof is not success (R5)."""
    snap = {
        "status": "completed",
        "archive_uri": "cortex://notes/system/threads/run/_superseded/old.md",
        "body": "superseded bytes only",
        "attested_model": None,
    }
    assert has_proof(snap) is False


def test_has_unresolved_artifact_card_predicate() -> None:
    assert _has_unresolved_artifact_card({"artifact_cards_unresolved": True})
    assert not _has_unresolved_artifact_card({"artifact_cards_unresolved": False})


def test_has_proof_rejects_specimen_unresolved_card() -> None:
    """AC5 negative: substantive prose + attested_model + unresolved card chrome."""
    snap = {
        "status": "completed",
        "body": (
            "Bind complete. BIND: merge wins on the sidecar question.\n"
            "Bind sidecar reasoning posture merge\nDocument · MD\nGoogle Drive"
        ),
        "attested_model": "Model: Fable 5 High",
        "harvest_provenance": "chat",
        "artifact_cards": [{"title": "Bind sidecar reasoning posture merge", "kind": "MD"}],
        "artifact_cards_unresolved": True,
    }
    assert has_proof(snap) is False


def test_has_proof_accepts_specimen_when_cards_resolved() -> None:
    """AC5 positive: same shape with artifact_cards_unresolved=False."""
    snap = {
        "status": "completed",
        "body": (
            "Bind complete. BIND: merge wins.\n\n"
            "## Artifact card: Bind sidecar reasoning posture merge\n\n"
            "# Sidecar\n\nfull body bytes"
        ),
        "attested_model": "Model: Fable 5 High",
        "harvest_provenance": "artifact-card",
        "artifact_cards": [{"title": "Bind sidecar reasoning posture merge", "kind": "MD"}],
        "artifact_cards_unresolved": False,
    }
    assert has_proof(snap) is True


def test_run_cdp_generate_stall_no_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-3")
    perpetual = {
        "execution_id": "sat-3",
        "status": "running",
        "completion_phase": "running",
        "body_len": 0,
        "streaming": True,
        "tool_pause": False,
    }
    client = _FakeClient(
        [
            {"execution_id": "sat-3", "status": "running"},
            perpetual,
            perpetual,
            perpetual,
        ]
    )
    # no_progress_s=5; clock advances 6s with identical fingerprints.
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(s: float) -> None:
        clock["t"] += max(s, 6.0)

    result = run_cdp_generate(
        execution_id="dispatch-3",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=1800,
        no_progress_s=5,
        poll_interval_s=1,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is False
    assert result.stall_stage == "no_progress"
    assert result.stall_stage != "wall_clock_exceeded"


def test_run_cdp_generate_post_idle_wall_since_last_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Frozen POST_IDLE trips wall_clock_exceeded at max_wall_s since last delta (R3)."""
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-post-idle-wall")
    idle = {
        "execution_id": "sat-post-idle-wall",
        "status": "running",
        "completion_phase": "turn_idle",
        "body_len": 2,
        "liveness_observed_at": "t2",
    }
    client = _FakeClient(
        [
            {"execution_id": "sat-post-idle-wall", "status": "running"},
            {
                "execution_id": "sat-post-idle-wall",
                "status": "running",
                "completion_phase": "running",
                "body_len": 0,
                "liveness_observed_at": "t0",
            },
            {
                "execution_id": "sat-post-idle-wall",
                "status": "running",
                "completion_phase": "running",
                "body_len": 1,
                "liveness_observed_at": "t1",
            },
            {
                "execution_id": "sat-post-idle-wall",
                "status": "running",
                "completion_phase": "running",
                "body_len": 2,
                "liveness_observed_at": "t2",
            },
            idle,
            idle,
            idle,
        ]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(_s: float) -> None:
        clock["t"] += 6.0

    result = run_cdp_generate(
        execution_id="dispatch-post-idle-wall",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=10,
        no_progress_s=5,
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is False
    assert result.stall_stage == "wall_clock_exceeded"
    since_last = (result.extras or {}).get("since_last_progress_s")
    assert since_last is not None
    assert since_last >= 10.0
    trace = (result.extras or {}).get("progress_trace") or {}
    history = trace.get("history") or []
    at_s_values = [entry["at_s"] for entry in history]
    assert at_s_values == sorted(at_s_values)
    assert trace.get("frozen_for_s", 0.0) >= 10.0


def test_run_cdp_generate_no_progress_exempt_post_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A frozen fingerprint at ``turn_idle`` must not trip the no-progress abort.

    Post-idle the satellite is resolving harvest and emits no per-sample signal,
    so the watchdog cannot distinguish that from a hang — ``max_wall_s`` is the
    only honest bound there (friction a:26175).
    """
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    idle = {
        "execution_id": "sat-4",
        "status": "running",
        "completion_phase": "turn_idle",
        "body_len": 0,
    }
    proof = {
        "execution_id": "sat-4",
        "status": "running",
        "completion_phase": "content_proof",
        "content_proof_uri": "cortex://notes/system/threads/proof.md",
        "body": "late harvest",
    }
    client = _FakeClient(
        [{"execution_id": "sat-4", "status": "running"}, idle, idle, idle, proof]
    )
    clock = {"t": 0.0}

    def _now() -> float:
        return clock["t"]

    def _sleep(s: float) -> None:
        clock["t"] += max(s, 6.0)

    result = run_cdp_generate(
        execution_id="dispatch-4",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        max_wall_s=1800,
        no_progress_s=5,
        poll_interval_s=1,
        client=client,  # type: ignore[arg-type]
        sleep=_sleep,
        now=_now,
    )
    assert result.ok is True
    assert result.body == "late harvest"
    assert result.stall_stage is None


def test_run_cdp_generate_reports_satellite_id_at_submit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``on_submitted`` fires before polling so the id is discoverable in flight."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    seen: list[str] = []
    client = _FakeClient(
        [
            {"execution_id": "sat-5", "status": "running"},
            {
                "execution_id": "sat-5",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/archive.md",
                "body": "done",
                "attested_model": "Model: Opus 4.8",
            },
        ]
    )

    def _on_submitted(satellite_execution_id: str) -> None:
        # Recorded against the poll count to prove ordering, not just arrival.
        seen.append(f"{satellite_execution_id}@{len(client.calls)}")

    result = run_cdp_generate(
        execution_id="dispatch-5",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
        on_submitted=_on_submitted,
    )
    assert seen == ["sat-5@1"]
    assert result.satellite_execution_id == "sat-5"


_OVERLOAD_ONLY_BODY = (
    "Claude responded: API Error: 529 Overloaded.\n\n"
    "API Error: 529 Overloaded. This is a server-side issue, usually temporary "
    "— try again in a moment. If it persists, check https://status.claude.com."
)


def test_is_overload_only_harvest_matches_archive_fixture() -> None:
    assert _is_overload_only_harvest(_OVERLOAD_ONLY_BODY) is True
    assert _is_overload_only_harvest("legitimate short answer") is False
    assert (
        _is_overload_only_harvest(
            "Done. API Error: 529 was transient during the run."
        )
        is False
    )


def test_run_cdp_generate_proof_short_answer_with_529_quote_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4 negative: harvest quoting 529 in prose must not trip overload gate."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    short_answer = "Summary complete. API Error: 529 was mentioned once."
    client = _FakeClient(
        [
            {"execution_id": "sat-quote", "status": "running"},
            {
                "execution_id": "sat-quote",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/archive.md",
                "body": short_answer,
                "attested_model": "Model: Opus 4.8",
            },
        ]
    )

    result = run_cdp_generate(
        execution_id="dispatch-quote",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is True
    assert result.body == short_answer


def test_run_cdp_generate_submit_529_then_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC1: one retry after backoff; on_submitted fires once; proof completes."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"_status_code": 529},
            {"execution_id": "sat-retry", "status": "running"},
            {
                "execution_id": "sat-retry",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/archive.md",
                "body": "harvested after retry",
                "attested_model": "Model: Opus 4.8",
            },
        ]
    )
    sleeps: list[float] = []
    seen: list[str] = []

    result = run_cdp_generate(
        execution_id="dispatch-retry",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
        on_submitted=seen.append,
    )
    assert result.ok is True
    assert result.body == "harvested after retry"
    assert seen == ["sat-retry"]
    assert sleeps[0] == SUBMIT_RETRY_BACKOFF_S
    assert sleeps.count(SUBMIT_RETRY_BACKOFF_S) == 1


def test_run_cdp_generate_submit_529_twice_upstream_overloaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC2: double 529 exhausts retry; no satellite id; overload carrier."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient([{"_status_code": 529}, {"_status_code": 529}])
    sleeps: list[float] = []
    seen: list[str] = []

    result = run_cdp_generate(
        execution_id="dispatch-exhaust",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
        on_submitted=seen.append,
    )
    assert result.ok is False
    assert result.satellite_execution_id is None
    assert result.stall_stage == UPSTREAM_OVERLOADED
    assert result.extras.get("reason") == UPSTREAM_OVERLOADED
    assert seen == []
    assert sleeps == [SUBMIT_RETRY_BACKOFF_S]


def test_run_cdp_generate_submit_transport_ambiguous_no_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3: status_code None does not retry or stamp upstream_overloaded."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")

    class _UnreachableClient(_FakeClient):
        def request(
            self, method: str, url: str, json: dict[str, Any] | None = None
        ) -> _FakeResp:
            if method == "POST" and "/abort" not in url:
                self.calls.append((method, url))
                raise httpx.ConnectError("connection refused")
            return super().request(method, url, json=json)

    client = _UnreachableClient([])
    sleeps: list[float] = []

    result = run_cdp_generate(
        execution_id="dispatch-transport",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=sleeps.append,
    )
    assert result.ok is False
    assert result.stall_stage is None
    assert result.extras.get("reason") != UPSTREAM_OVERLOADED
    assert sleeps == []
    assert len(client.calls) == 1


def test_run_cdp_generate_proof_overload_only_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4: overload-only harvest body is not ok=True."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-ol", "status": "running"},
            {
                "execution_id": "sat-ol",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-new.md",
                "body": _OVERLOAD_ONLY_BODY,
                "attested_model": "Model: Opus 4.8",
            },
        ]
    )

    result = run_cdp_generate(
        execution_id="dispatch-ol-body",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is False
    assert result.stall_stage == UPSTREAM_OVERLOADED
    assert result.extras.get("reason") == UPSTREAM_OVERLOADED


def test_run_cdp_generate_proof_empty_body_with_archive_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Archive without assistant body or attested_model must not terminalize as success."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite.test")
    client = _FakeClient(
        [
            {"execution_id": "sat-empty", "status": "running"},
            {
                "execution_id": "sat-empty",
                "status": "completed",
                "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-new.md",
                "body": "",
                "completion_phase": "terminal",
            },
        ]
    )

    result = run_cdp_generate(
        execution_id="dispatch-ol-empty",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is False
    assert result.stall_stage == "completed_without_proof"


def test_run_cdp_generate_mission_completed_without_proof_retain_cse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mission purpose must pass retain_cse=True at completed_without_proof abort."""
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-cwp-mission")
    client = _FakeClient(
        [
            {"execution_id": "sat-cwp", "status": "running"},
            {
                "execution_id": "sat-cwp",
                "status": "completed",
                "body": "",
                "completion_phase": "terminal",
            },
        ]
    )
    retain_calls: list[bool] = []
    from claude_bundles import cdp_model_endpoint as mod

    orig = mod._abort_then_sweep

    def _track(*args, **kwargs):
        retain_calls.append(bool(kwargs.get("retain_cse")))
        return orig(*args, **kwargs)

    monkeypatch.setattr(mod, "_abort_then_sweep", _track)
    result = run_cdp_generate(
        execution_id="dispatch-cwp-mission",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        purpose="operator-proxy",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is False
    assert result.stall_stage == "completed_without_proof"
    assert retain_calls == [True]
    assert result.extras.get("abort", {}).get("abort_skipped") is True


def test_run_cdp_generate_non_mission_completed_without_proof_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-mission purpose still aborts+sweeps on completed_without_proof."""
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-cwp-ask")
    client = _FakeClient(
        [
            {"execution_id": "sat-ask", "status": "running"},
            {
                "execution_id": "sat-ask",
                "status": "completed",
                "body": "",
                "completion_phase": "terminal",
            },
        ]
    )
    retain_calls: list[bool] = []
    from claude_bundles import cdp_model_endpoint as mod

    orig = mod._abort_then_sweep

    def _track(*args, **kwargs):
        retain_calls.append(bool(kwargs.get("retain_cse")))
        return orig(*args, **kwargs)

    monkeypatch.setattr(mod, "_abort_then_sweep", _track)
    result = run_cdp_generate(
        execution_id="dispatch-cwp-ask",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        purpose="ask",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is False
    assert retain_calls == [False]


def test_run_cdp_generate_mission_overload_retain_cse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Overload proof-reject path passes retain_cse=mission_retain."""
    _mock_run_cdp_staging(monkeypatch, tmp_path, "dispatch-ol-mission")
    client = _FakeClient(
        [
            {"execution_id": "sat-ol-m", "status": "running"},
            {
                "execution_id": "sat-ol-m",
                "status": "running",
                "archive_uri": "cortex://notes/system/threads/cdp-ask-archive-new.md",
                "body": _OVERLOAD_ONLY_BODY,
                "attested_model": "Model: Opus 4.8",
            },
        ]
    )
    retain_calls: list[bool] = []
    from claude_bundles import cdp_model_endpoint as mod

    orig = mod._abort_then_sweep

    def _track(*args, **kwargs):
        retain_calls.append(bool(kwargs.get("retain_cse")))
        return orig(*args, **kwargs)

    monkeypatch.setattr(mod, "_abort_then_sweep", _track)
    result = run_cdp_generate(
        execution_id="dispatch-ol-mission",
        model_id="cdp/opus-4.8",
        prompt_text="ping",
        purpose="mission",
        poll_interval_s=0,
        client=client,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    assert result.ok is False
    assert retain_calls == [True]


def test_stage_cdp_prompt_twice_yields_single_manifest_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Double stage on one execution_id must not leak an interior slash block."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    first = stage_cdp_prompt_with_skills(
        execution_id="exec-double-stage",
        prompt_text="TYPE: CONTINUITY_HANDOFF\narc: 6655\n",
        skills=["reasoning-posture", "consult-posture"],
    )
    on_disk = (
        tmp_path
        / "notes/system/ephemeral/cdp-endpoint/exec-double-stage/prompt.md"
    )
    once_text = on_disk.read_text(encoding="utf-8")
    # Simulate worker re-entry that still rewrites (defense-in-depth peel).
    second = stage_cdp_prompt_with_skills(
        execution_id="exec-double-stage-rewrite",
        prompt_text=once_text,
        skills=None,
    )
    twice_path = (
        tmp_path
        / "notes/system/ephemeral/cdp-endpoint/exec-double-stage-rewrite/prompt.md"
    )
    twice_text = twice_path.read_text(encoding="utf-8")
    assert twice_text.count("/reasoning-posture\n") == 1
    assert twice_text.count("<!--cdp-required-skills:") == 1
    assert "TYPE: CONTINUITY_HANDOFF" in twice_text
    # Re-entry reseals from the effective set, so a caller-only slug from the
    # first stage is not carried forward.
    assert "/consult-posture\n" not in twice_text
    # Ownership guard: re-stage via same-exec prompt_uri must pass through.
    guarded = stage_cdp_prompt_with_skills(
        execution_id="exec-double-stage",
        prompt_uri=first.prompt_uri,
        skills=None,
    )
    assert guarded.prompt_uri == first.prompt_uri
    assert on_disk.read_text(encoding="utf-8") == once_text
    assert second.staged is True


def test_admit_then_worker_uri_does_not_double_prepend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Admit stages; worker passes the staged URI — single owner, no interior slash."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    admitted = stage_cdp_prompt_with_skills(
        execution_id="exec-admit-worker",
        prompt_text="## ask\nhello\n",
        skills=["reasoning-posture", "consult-posture"],
    )
    # Worker shape: skills omitted, prompt_uri = admit's ephemeral URI.
    worker = stage_cdp_prompt_with_skills(
        execution_id="exec-admit-worker",
        prompt_uri=admitted.prompt_uri,
        skills=None,
    )
    assert worker.prompt_uri == admitted.prompt_uri
    text = (
        tmp_path / "notes/system/ephemeral/cdp-endpoint/exec-admit-worker/prompt.md"
    ).read_text(encoding="utf-8")
    assert text.count("/reasoning-posture\n") == 1
    assert text.count("/consult-posture\n") == 1
    assert text.count("<!--cdp-required-skills:") == 1
    # After stripping the leading manifest, body must not start with another slash block.
    from claude_bundles.cowork_skill_delivery import split_leading_slash_skills

    _tokens, rest = split_leading_slash_skills(text)
    assert not rest.lstrip("\n").startswith("/")
