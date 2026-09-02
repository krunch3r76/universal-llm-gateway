"""Pin-bump guard for the cursor-sdk bridge-env overlay binding.

The dispatch HOME/venv overlay reaches the bridge subprocess through a
monkeypatch on the SDK's module-global ``_bridge_subprocess_env``. That is a
binding to cursor-sdk internals, currently pinned at ``cursor-sdk==1.0.30``
(``requirements.host.txt``). A pin bump can break it silently: the dispatch
would then run against the operator's real HOME with no error anywhere.

These tests drive the real, pinned launch path down to the ``Popen`` boundary
and assert the overlay's values are in the env the bridge would receive. They
fail on every drift shape that matters — the SDK building env some other way,
renaming or aliasing ``_bridge_subprocess_env``, or ``Client.launch_bridge``
ceasing to route through ``Bridge.launch``.
"""

from __future__ import annotations

import services.git_integration_worker.cursor_sdk_bridge_launch as bridge_launch


def test_overlay_home_reaches_bridge_popen_env(tmp_path, monkeypatch) -> None:
    """Overlay HOME/dispatch-id/git identity must land in the bridge Popen env.

    Load-bearing assertion of this file. Nothing is stubbed except ``Popen``
    itself, so the whole pinned ``Client.launch_bridge`` -> ``Bridge.launch`` ->
    ``_bridge_subprocess_env`` chain is exercised for real.
    """
    from cursor_sdk import _bridge as _sdk_bridge

    captured: dict[str, object] = {}

    class _StopAtPopen(Exception):  # noqa: N818
        """Raised from the fake Popen — the env is all this test needs."""

    def _popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        raise _StopAtPopen()

    monkeypatch.setattr(_sdk_bridge.subprocess, "Popen", _popen)

    home = tmp_path / "dispatch-home"
    home.mkdir()
    with bridge_launch._dispatch_home_overlay(home, dispatch_id="disp-guard"):
        try:
            bridge_launch.Client.launch_bridge(
                bridge_launch._SDK_BRIDGE_BIN,
                workspace=str(tmp_path),
                state_root=str(tmp_path / "state"),
                timeout=5.0,
                local=None,
            )
        except BaseException:  # noqa: BLE001 — see assertion below
            # The exception type is not the contract; reaching Popen is.
            # Anything raised before Popen leaves `captured` empty and the
            # assertion below reports it with the right diagnosis.
            pass

    assert "env" in captured, (
        "Client.launch_bridge never reached subprocess.Popen — the cursor-sdk "
        "launch path changed shape under the pin. The dispatch HOME overlay "
        "binding is unverified; inspect cursor_sdk._bridge.Bridge.launch and "
        "cursor_sdk._client.Client.launch_bridge before shipping this pin."
    )
    env = captured["env"]
    assert env is not None, (
        "Bridge.launch no longer passes env= to Popen — the overlay is a no-op "
        "and every dispatch would run against the operator's real HOME."
    )
    assert env["HOME"] == str(home)
    assert env["CURSOR_SDK_DISPATCH_ID"] == "disp-guard"
    assert env.get("GIT_AUTHOR_NAME")


def test_bridge_env_patch_bound_on_both_modules() -> None:
    """Both the sync and async SDK bridge modules must carry the patched env fn.

    ``_async_bridge`` imports the name at module load, so patching only the
    sync module global leaves the async binding pointing at the original.
    """
    from cursor_sdk import _async_bridge, _bridge

    assert (
        _bridge._bridge_subprocess_env.__name__
        == "_bridge_subprocess_env_with_overlay"
    ), "cursor_sdk._bridge._bridge_subprocess_env is not the overlay wrapper"
    assert _async_bridge._bridge_subprocess_env is _bridge._bridge_subprocess_env
