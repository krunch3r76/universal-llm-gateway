"""Pin-bump guard for the cursor-sdk ``command=`` argv-shim launch contract.

The dispatch HOME/venv/PATH/stamp/git identity reach the bridge as ``env(1)``
assignments in the ``command`` list GIW passes to ``Client.launch_bridge``.
That binds two SDK facts pinned at ``cursor-sdk==1.0.30``
(``requirements.host.txt``): the list is forwarded verbatim as ``argv[0..n]``
with the SDK's own flags appended after it, and the subprocess env is built
from ``os.environ`` untouched. A pin bump can break either silently — the
dispatch would then run against the operator's real HOME with no error.

Tests here drive the real pinned launch path to the ``Popen`` boundary
(argv + env captured), prove the SDK env builder is pristine, and prove with
a real ``Popen`` that ``/usr/bin/env`` execs in place — the pid the SDK holds
is the pid that sees the overlay.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import services.git_integration_worker.cursor_sdk_bridge_launch as bridge_launch
from services.git_integration_worker.cursor_sdk_bridge_launch import (
    build_bridge_command,
    resolve_bridge_bin,
)


def _fake_repo_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "repo-venv"
    (venv / "bin").mkdir(parents=True)
    return venv


def _first_non_assignment(argv: list[str]) -> int:
    """Index of the first element after ``argv[0]`` that is not ``K=V``."""
    for i, arg in enumerate(argv[1:], start=1):
        if "=" not in arg:
            return i
    raise AssertionError(f"no program element in argv: {argv!r}")


def test_command_argv_reaches_bridge_popen(tmp_path, monkeypatch) -> None:
    """The shim list must be argv[0..n] at Popen, with SDK flags after it.

    Load-bearing assertion of this file. Nothing is stubbed except ``Popen``
    itself, so the pinned ``Client.launch_bridge`` -> ``Bridge.launch`` ->
    ``Popen`` chain is exercised for real, and the env the SDK passes must be
    GIW's own environment — the overlay is argv, never Popen env.
    """
    from cursor_sdk import _bridge as _sdk_bridge

    captured: dict[str, object] = {}

    class _StopAtPopen(Exception):  # noqa: N818
        """Raised from the fake Popen — argv and env are all this test needs."""

    def _popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = kwargs.get("env")
        raise _StopAtPopen()

    monkeypatch.setattr(_sdk_bridge.subprocess, "Popen", _popen)

    home = tmp_path / "dispatch-home"
    home.mkdir()
    command = build_bridge_command(
        bridge_bin=resolve_bridge_bin(),
        dispatch_home=home,
        repo_venv=_fake_repo_venv(tmp_path),
        real_home=tmp_path / "operator-home",
        dispatch_id="disp-guard",
    )
    try:
        bridge_launch.Client.launch_bridge(
            command=command,
            workspace=str(tmp_path),
            state_root=str(tmp_path / "state"),
            timeout=5.0,
            local=None,
        )
    except BaseException:  # noqa: BLE001 — see assertion below
        # The exception type is not the contract; reaching Popen is.
        pass

    assert "argv" in captured, (
        "Client.launch_bridge never reached subprocess.Popen — the cursor-sdk "
        "launch path changed shape under the pin. Inspect "
        "cursor_sdk._bridge.Bridge.launch before shipping this pin."
    )
    argv = captured["argv"]
    assert argv[0] == "/usr/bin/env"
    assert f"HOME={home}" in argv
    assert "CURSOR_SDK_DISPATCH_ID=disp-guard" in argv
    assert any(a.startswith("GIT_AUTHOR_NAME=") for a in argv)
    assert any(a.startswith("CURSOR_SDK_DISPATCH_LEDGER=") for a in argv)
    bin_idx = _first_non_assignment(argv)
    assert os.path.isabs(argv[bin_idx]), argv[bin_idx]
    assert argv[bin_idx] == command[-1]
    assert argv[: bin_idx + 1] == command, "SDK did not forward command= verbatim"
    assert "--workspace" in argv[bin_idx + 1 :], "SDK flags must follow the bridge bin"
    env = captured["env"]
    assert env is not None
    assert env["HOME"] == os.environ["HOME"], "overlay leaked into Popen env"
    assert "CURSOR_SDK_DISPATCH_ID" not in env or (
        env["CURSOR_SDK_DISPATCH_ID"] == os.environ.get("CURSOR_SDK_DISPATCH_ID")
    )


def test_sdk_env_fn_is_pristine() -> None:
    """GIW must not wrap, alias, or replace the SDK's subprocess-env builder."""
    from cursor_sdk import _async_bridge, _bridge

    assert _bridge._bridge_subprocess_env.__name__ == "_bridge_subprocess_env"
    assert _async_bridge._bridge_subprocess_env is _bridge._bridge_subprocess_env
    for name in (
        "_install_bridge_env_patch",
        "_dispatch_home_overlay",
        "_dispatch_env",
        "_dispatch_env_overlay",
        "_BRIDGE_ENV_PATCH_INSTALLED",
        "_PATH_PREPEND_KEY",
    ):
        assert not hasattr(bridge_launch, name), name


def test_env_shim_execs_in_place(tmp_path, monkeypatch) -> None:
    """Kind-proof: the pid Popen returns is the pid that sees the overlay.

    No SDK involved. ``/usr/bin/env`` must exec its program in place — if it
    forked, ``_terminate_process``, ``_read_discovery``, the stderr drain and
    the orphan reaper would all act on the wrong process. Pid equality is the
    assertion; tolerating inequality here would void the launch contract.
    """
    home = tmp_path / "dispatch-home"
    home.mkdir()
    repo_venv = _fake_repo_venv(tmp_path)
    dispatch_id = "disp-exec"
    prev_environ = dict(os.environ)
    command = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=home,
        repo_venv=repo_venv,
        real_home=tmp_path / "operator-home",
        dispatch_id=dispatch_id,
    )
    script = (
        "import os, sys\n"
        "print(os.getpid())\n"
        "print(os.environ['HOME'])\n"
        "print(os.environ['VIRTUAL_ENV'])\n"
        "print(os.environ['PATH'].split(os.pathsep)[0])\n"
        "print(os.environ['GIT_AUTHOR_EMAIL'])\n"
        "print(os.environ['CURSOR_SDK_DISPATCH_ID'])\n"
    )
    proc = subprocess.Popen(
        command + ["-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    lines = out.splitlines()
    assert int(lines[0]) == proc.pid, "env(1) did not exec in place"
    assert lines[1] == str(home)
    assert lines[2] == str(repo_venv)
    assert lines[3] == str(repo_venv / "bin")
    assert lines[4] == f"{dispatch_id}@dispatch.git-integration-worker"
    assert lines[5] == dispatch_id
    assert dict(os.environ) == prev_environ


def test_build_bridge_command_rejects_unsafe_bin(tmp_path) -> None:
    """Relative, ``=``-bearing, ``-``-prefixed, or empty bins are refused."""
    for bad in ("cursor-sdk-bridge", "/opt/a=b/bridge", "-bridge", "--bridge", ""):
        with pytest.raises(ValueError):
            build_bridge_command(
                bridge_bin=bad,
                dispatch_home=tmp_path,
                repo_venv=None,
                real_home=None,
                dispatch_id=None,
            )


def test_build_bridge_command_omits_optional_pairs(tmp_path, monkeypatch) -> None:
    """None inputs omit their pairs; empty GIW PATH yields a bare prepend."""
    minimal = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=tmp_path,
        repo_venv=None,
        real_home=None,
        dispatch_id=None,
    )
    assert minimal == ["/usr/bin/env", f"HOME={tmp_path}", sys.executable]

    repo_venv = _fake_repo_venv(tmp_path)
    monkeypatch.delenv("PATH", raising=False)
    bare = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=tmp_path,
        repo_venv=repo_venv,
        real_home=tmp_path / "operator-home",
        dispatch_id=None,
    )
    assert f"VIRTUAL_ENV={repo_venv}" in bare
    assert f"PATH={repo_venv / 'bin'}" in bare
    assert not any(a.startswith("CURSOR_SDK_DISPATCH_ID=") for a in bare)
    assert not any(a.startswith("GIT_") for a in bare)

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    full = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=tmp_path,
        repo_venv=repo_venv,
        real_home=tmp_path / "operator-home",
        dispatch_id="disp-x",
    )
    assert f"PATH={repo_venv / 'bin'}{os.pathsep}/usr/bin:/bin" in full
    assert full[-1] == sys.executable
    assert full.index("CURSOR_SDK_DISPATCH_ID=disp-x") < full.index(
        "GIT_AUTHOR_NAME=cursor-sdk/disp-x"
    )


def test_build_bridge_command_arms_shell_cwd_preload(tmp_path, monkeypatch) -> None:
    """Missing-cwd fallback reaches the bridge as env(1) assignments.

    ``NODE_OPTIONS`` keeps any value already in the worker environment, with
    ``--require <preload>`` in front. Both assignments sit before the binary.
    """
    lane = tmp_path / "lane-root"
    lane.mkdir()
    monkeypatch.setenv("NODE_OPTIONS", "--trace-warnings")
    command = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=tmp_path / "home",
        repo_venv=None,
        real_home=None,
        dispatch_id=None,
        lane_path=lane,
    )
    preload = (
        Path(bridge_launch.__file__).resolve().parent
        / "cursor_sdk_shell_cwd_preload.cjs"
    )
    bin_idx = _first_non_assignment(command)
    assert command[bin_idx] == sys.executable
    head = command[:bin_idx]
    assert f"CURSOR_SDK_SHELL_FALLBACK_CWD={lane}" in head
    assert f"NODE_OPTIONS=--require {preload} --trace-warnings" in head


def _vendored_node() -> Path | None:
    import cursor_sdk

    node = (
        Path(cursor_sdk.__file__).resolve().parent
        / "_vendor"
        / "bridge"
        / "bin"
        / "node"
    )
    return node if node.is_file() else None


def test_shell_cwd_preload_rewrites_missing_bash_cwd(tmp_path: Path) -> None:
    """Vendored node: missing bash cwd is ENOENT until the preload rewrites it.

    The child bash must not inherit the preload require or the fallback path.
    Skipped when the wheel did not stage ``bin/node``.
    """
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    preload = (
        Path(bridge_launch.__file__).resolve().parent
        / "cursor_sdk_shell_cwd_preload.cjs"
    )
    missing = tmp_path / "missing-cwd"
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    probe = tmp_path / "probe.cjs"
    probe.write_text(
        "\n".join(
            [
                "const { spawn } = require('child_process');",
                "const missing = process.argv[2];",
                "const mode = process.argv[3];",
                "function run(commandArgs, cwd) {",
                "  const child = spawn('/bin/bash', commandArgs, { cwd });",
                "  if (child.stdout) {",
                "    child.stdout.on('data', (buf) => process.stdout.write(buf));",
                "  }",
                "  if (child.stderr) {",
                "    child.stderr.on('data', (buf) => process.stderr.write(buf));",
                "  }",
                "  child.on('error', (err) => {",
                "    process.stderr.write(String(err.code) + '\\n');",
                "    process.exit(1);",
                "  });",
                "  child.on('close', (code) => {",
                "    process.exit(code == null ? 1 : code);",
                "  });",
                "}",
                "if (mode === 'env') {",
                "  const expr =",
                "    'printf %s \"$NODE_OPTIONS|$CURSOR_SDK_SHELL_FALLBACK_CWD\"';",
                "  run(['-c', expr], process.cwd());",
                "} else {",
                "  run(['-c', 'pwd'], missing);",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plain_env = os.environ.copy()
    plain_env.pop("NODE_OPTIONS", None)
    plain_env.pop("CURSOR_SDK_SHELL_FALLBACK_CWD", None)
    plain = subprocess.run(
        [str(node), str(probe), str(missing), "plain"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=plain_env,
    )
    assert plain.returncode != 0
    assert "ENOENT" in plain.stderr

    armed = os.environ.copy()
    armed["NODE_OPTIONS"] = f"--require {preload}"
    armed["CURSOR_SDK_SHELL_FALLBACK_CWD"] = str(fallback)
    rewritten = subprocess.run(
        [str(node), str(probe), str(missing), "rewrite"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=armed,
    )
    assert rewritten.returncode == 0, rewritten.stderr
    assert f"shell_cwd_missing {missing}" in rewritten.stderr
    assert rewritten.stdout.strip() == str(fallback)

    inherited = subprocess.run(
        [str(node), str(probe), str(missing), "env"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=armed,
    )
    assert inherited.returncode == 0, inherited.stderr
    assert str(preload) not in inherited.stdout
    assert str(fallback) not in inherited.stdout


def test_shell_cwd_preload_keeps_dispatch_id_in_bash_child(tmp_path: Path) -> None:
    """AC2: bash via wrappedSpawn retains CURSOR_SDK_DISPATCH_ID."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    preload = (
        Path(bridge_launch.__file__).resolve().parent
        / "cursor_sdk_shell_cwd_preload.cjs"
    )
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    probe = tmp_path / "dispatch_probe.cjs"
    probe.write_text(
        "\n".join(
            [
                "const { spawn } = require('child_process');",
                "const child = spawn('/bin/bash', ['-c', 'printf %s \"$CURSOR_SDK_DISPATCH_ID|$CURSOR_SDK_SHELL_FALLBACK_CWD\"'], { cwd: process.cwd() });",
                "child.stdout.on('data', (b) => process.stdout.write(b));",
                "child.on('close', (code) => process.exit(code == null ? 1 : code));",
                "",
            ]
        ),
        encoding="utf-8",
    )
    armed = os.environ.copy()
    armed["NODE_OPTIONS"] = f"--require {preload}"
    armed["CURSOR_SDK_SHELL_FALLBACK_CWD"] = str(fallback)
    armed["CURSOR_SDK_DISPATCH_ID"] = "disp-preload-ac2"
    proc = subprocess.run(
        [str(node), str(probe)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=armed,
    )
    assert proc.returncode == 0, proc.stderr
    dispatch_id, fallback_cwd = proc.stdout.strip().split("|", 1)
    assert dispatch_id == "disp-preload-ac2"
    assert fallback_cwd == ""


def test_resolve_bridge_bin_is_absolute_file() -> None:
    """The pinned wheel must resolve to an absolute, existing launcher."""
    resolved = resolve_bridge_bin()
    assert os.path.isabs(resolved), resolved
    assert os.path.isfile(resolved), resolved
    assert "=" not in resolved and not resolved.startswith("-")
