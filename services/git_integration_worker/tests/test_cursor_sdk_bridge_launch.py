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
    launch_sdk_bridge,
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
    assert not any(a.startswith("PLAYWRIGHT_BROWSERS_PATH=") for a in bare)

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


def test_build_bridge_command_sets_playwright_browsers_path_when_cache_exists(
    tmp_path: Path,
) -> None:
    """Operator ms-playwright cache ⇒ PLAYWRIGHT_BROWSERS_PATH on the env shim (a:37291)."""
    real = tmp_path / "operator-home"
    browsers = real / ".cache" / "ms-playwright"
    browsers.mkdir(parents=True)
    command = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=tmp_path / "dispatch-home",
        repo_venv=None,
        real_home=real,
        dispatch_id=None,
    )
    assert f"PLAYWRIGHT_BROWSERS_PATH={browsers}" in command
    assert command.index(f"HOME={tmp_path / 'dispatch-home'}") < command.index(
        f"PLAYWRIGHT_BROWSERS_PATH={browsers}"
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


def test_shell_cwd_preload_chdirs_node_process(tmp_path: Path) -> None:
    """Armed preload chdirs the node process to the fallback directory."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    preload = (
        Path(bridge_launch.__file__).resolve().parent
        / "cursor_sdk_shell_cwd_preload.cjs"
    )
    lane = tmp_path / "lane"
    lane.mkdir()
    armed = os.environ.copy()
    armed["NODE_OPTIONS"] = f"--require {preload}"
    armed["CURSOR_SDK_SHELL_FALLBACK_CWD"] = str(lane)
    armed["CURSOR_SDK_SHELL_ALIGN_CWD"] = "1"
    proc = subprocess.run(
        [str(node), "-e", "console.log(process.cwd())"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=armed,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(lane)


def test_shell_cwd_preload_bare_bash_pwd_is_fallback(tmp_path: Path) -> None:
    """Bash spawn with no cwd option uses the fallback directory."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    preload = (
        Path(bridge_launch.__file__).resolve().parent
        / "cursor_sdk_shell_cwd_preload.cjs"
    )
    lane = tmp_path / "lane"
    lane.mkdir()
    probe = tmp_path / "bare_pwd.cjs"
    probe.write_text(
        "\n".join(
            [
                "const { spawn } = require('child_process');",
                "process.chdir(require('os').tmpdir());",
                "const child = spawn('/bin/bash', ['-c', 'pwd']);",
                "child.stdout.on('data', (b) => process.stdout.write(b));",
                "child.on('close', (code) => process.exit(code == null ? 1 : code));",
                "",
            ]
        ),
        encoding="utf-8",
    )
    armed = os.environ.copy()
    armed["NODE_OPTIONS"] = f"--require {preload}"
    armed["CURSOR_SDK_SHELL_FALLBACK_CWD"] = str(lane)
    armed["CURSOR_SDK_SHELL_ALIGN_CWD"] = "1"
    proc = subprocess.run(
        [str(node), str(probe)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=armed,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(lane)


def test_command_argv_includes_preload_when_lane_path_set(
    tmp_path, monkeypatch
) -> None:
    """Lane launch arms the preload env and leaves Popen cwd unset.

    SDK-appended filesystem paths and callback URLs must be absolute. The
    cwd fix is the preload chdir, not subprocess.Popen(cwd=).
    """
    from cursor_sdk import _bridge as _sdk_bridge

    captured: dict[str, object] = {}

    def _popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["cwd"] = kwargs.get("cwd")
        raise RuntimeError("stop-at-popen")

    monkeypatch.setattr(_sdk_bridge.subprocess, "Popen", _popen)

    lane = tmp_path / "lane"
    lane.mkdir()
    home = tmp_path / "dispatch-home"
    home.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    command = build_bridge_command(
        bridge_bin=resolve_bridge_bin(),
        dispatch_home=home,
        repo_venv=_fake_repo_venv(tmp_path),
        real_home=tmp_path / "operator-home",
        dispatch_id="disp-lane-cwd",
        lane_path=lane,
        align_shell_cwd=True,
    )
    try:
        bridge_launch.Client.launch_bridge(
            command=command,
            workspace=str(lane),
            state_root=str(state),
            timeout=5.0,
            local=None,
        )
    except BaseException:  # noqa: BLE001
        pass

    assert "argv" in captured
    argv = captured["argv"]
    assert isinstance(argv, list)
    preload = (
        Path(bridge_launch.__file__).resolve().parent
        / "cursor_sdk_shell_cwd_preload.cjs"
    )
    assert f"CURSOR_SDK_SHELL_FALLBACK_CWD={lane}" in argv
    assert "CURSOR_SDK_SHELL_ALIGN_CWD=1" in argv
    assert any(
        a.startswith("NODE_OPTIONS=") and f"--require {preload}" in a for a in argv
    )
    bin_idx = _first_non_assignment(argv)
    tail = argv[bin_idx + 1 :]
    for flag in ("--workspace", "--state-root", "--tool-callback-url"):
        assert flag in tail, flag
        value = tail[tail.index(flag) + 1]
        assert (
            os.path.isabs(value)
            or value.startswith("http://")
            or value.startswith("https://")
        ), value
    assert captured["cwd"] is None


def _preload_path() -> Path:
    return (
        Path(bridge_launch.__file__).resolve().parent
        / "cursor_sdk_shell_cwd_preload.cjs"
    )


def _armed_preload_env(lane: Path, *, align: bool) -> dict[str, str]:
    armed = os.environ.copy()
    armed["NODE_OPTIONS"] = f"--require {_preload_path()}"
    armed["CURSOR_SDK_SHELL_FALLBACK_CWD"] = str(lane)
    armed.pop("CURSOR_SDK_SHELL_ALIGN_CWD", None)
    if align:
        armed["CURSOR_SDK_SHELL_ALIGN_CWD"] = "1"
    return armed


def _run_node(
    node: Path, args: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(node), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
        cwd=cwd,
    )


def test_build_bridge_command_lane_b_has_shell_align_flag(tmp_path: Path) -> None:
    """Lane B argv carries the alignment assignment."""
    lane = tmp_path / "lane"
    command = build_bridge_command(
        bridge_bin="/usr/bin/true",
        dispatch_home=tmp_path / "home",
        repo_venv=None,
        real_home=None,
        dispatch_id=None,
        lane_path=lane,
        align_shell_cwd=True,
    )
    assert "CURSOR_SDK_SHELL_ALIGN_CWD=1" in command


def test_build_bridge_command_lane_a_argv_matches_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alignment off matches master 7ce57ebb element for element.

    Master arms NODE_OPTIONS and CURSOR_SDK_SHELL_FALLBACK_CWD whenever
    lane_path is set, and does not append CURSOR_SDK_SHELL_ALIGN_CWD.
    """
    from services.git_integration_worker.cursor_dispatch_ledger import (
        dispatch_ledger_env_vars,
    )
    from services.git_integration_worker.cursor_home import (
        build_dispatch_path_prepend,
        dispatch_git_env_vars,
    )
    from services.git_integration_worker.cursor_sdk_context import steer_spool_dir

    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    lane = tmp_path / "lane"
    home = tmp_path / "dispatch-home"
    venv = _fake_repo_venv(tmp_path)
    real_home = tmp_path / "operator-home"
    bridge_bin = "/usr/bin/true"
    dispatch_id = "disp-lane-a-master"
    prepend = build_dispatch_path_prepend(venv, real_home=real_home)
    base = os.environ.get("PATH", "")
    path_value = f"{prepend}{os.pathsep}{base}" if base else prepend
    preload = _preload_path()
    expected = [
        "/usr/bin/env",
        f"HOME={home}",
        f"VIRTUAL_ENV={venv}",
        f"PATH={path_value}",
        f"CURSOR_SDK_DISPATCH_ID={dispatch_id}",
        *[f"{k}={v}" for k, v in dispatch_git_env_vars(dispatch_id).items()],
        *[f"{k}={v}" for k, v in dispatch_ledger_env_vars().items()],
        f"ULG_STEER_SPOOL_DIR={steer_spool_dir()}",
        f"NODE_OPTIONS=--require {preload}",
        f"CURSOR_SDK_SHELL_FALLBACK_CWD={lane}",
        bridge_bin,
    ]
    actual = build_bridge_command(
        bridge_bin=bridge_bin,
        dispatch_home=home,
        repo_venv=venv,
        real_home=real_home,
        dispatch_id=dispatch_id,
        lane_path=lane,
        align_shell_cwd=False,
    )
    assert actual == expected


def test_shell_cwd_preload_without_align_flag_matches_master(tmp_path: Path) -> None:
    """Preload armed, alignment absent: no chdir, no bare-bash fill, missing cwd still rewritten."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    lane = tmp_path / "lane"
    lane.mkdir()
    runner = tmp_path / "runner"
    runner.mkdir()
    env = _armed_preload_env(lane, align=False)
    cwd_proc = _run_node(node, ["-e", "console.log(process.cwd())"], env, runner)
    assert cwd_proc.returncode == 0, cwd_proc.stderr
    assert cwd_proc.stdout.strip() == str(runner)

    probe = tmp_path / "bare.cjs"
    probe.write_text(
        "const { spawn } = require('child_process');\n"
        "const child = spawn('/bin/bash', ['-c', 'pwd']);\n"
        "child.stdout.on('data', (b) => process.stdout.write(b));\n"
        "child.on('close', (code) => process.exit(code == null ? 1 : code));\n",
        encoding="utf-8",
    )
    bare = _run_node(node, [str(probe)], env, runner)
    assert bare.returncode == 0, bare.stderr
    assert bare.stdout.strip() == str(runner)

    missing = tmp_path / "missing-cwd"
    rewrite = tmp_path / "rewrite.cjs"
    rewrite.write_text(
        "const { spawn } = require('child_process');\n"
        "const missing = process.argv[2];\n"
        "const child = spawn('/bin/bash', ['-c', 'pwd'], { cwd: missing });\n"
        "child.stdout.on('data', (b) => process.stdout.write(b));\n"
        "child.stderr.on('data', (b) => process.stderr.write(b));\n"
        "child.on('error', (err) => { process.stderr.write(String(err.code)); process.exit(1); });\n"
        "child.on('close', (code) => process.exit(code == null ? 1 : code));\n",
        encoding="utf-8",
    )
    rewritten = _run_node(node, [str(rewrite), str(missing)], env, runner)
    assert rewritten.returncode == 0, rewritten.stderr
    assert rewritten.stdout.strip() == str(lane)


def test_shell_cwd_preload_respects_explicit_valid_cwd(tmp_path: Path) -> None:
    """An explicit valid cwd is not replaced by the fallback."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    lane = tmp_path / "lane"
    lane.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    probe = tmp_path / "explicit.cjs"
    probe.write_text(
        "const { spawn } = require('child_process');\n"
        "const other = process.argv[2];\n"
        "const child = spawn('/bin/bash', ['-c', 'pwd'], { cwd: other });\n"
        "child.stdout.on('data', (b) => process.stdout.write(b));\n"
        "child.on('close', (code) => process.exit(code == null ? 1 : code));\n",
        encoding="utf-8",
    )
    proc = _run_node(
        node, [str(probe), str(other)], _armed_preload_env(lane, align=True), tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(other)


def test_shell_cwd_preload_missing_fallback_no_chdir_no_crash(tmp_path: Path) -> None:
    """A missing fallback with alignment on leaves the runner cwd and exits 0."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    missing = tmp_path / "not-a-dir"
    env = _armed_preload_env(missing, align=True)
    proc = _run_node(node, ["-e", "console.log(process.cwd())"], env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(tmp_path)


def test_shell_cwd_preload_bare_bash_spawn_options_only(tmp_path: Path) -> None:
    """spawn(bash, options) with no argv array still fills an empty cwd."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    lane = tmp_path / "lane"
    lane.mkdir()
    probe = tmp_path / "opts.cjs"
    probe.write_text(
        "const { spawn } = require('child_process');\n"
        "process.chdir(require('os').tmpdir());\n"
        "const child = spawn('/bin/bash', {});\n"
        "child.stdin.write('pwd\\n');\n"
        "child.stdin.end();\n"
        "child.stdout.on('data', (b) => process.stdout.write(b));\n"
        "child.on('close', (code) => process.exit(code == null ? 1 : code));\n",
        encoding="utf-8",
    )
    proc = _run_node(node, [str(probe)], _armed_preload_env(lane, align=True), tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(lane)


def test_shell_cwd_preload_child_sees_pwd_env(tmp_path: Path) -> None:
    """After chdir, a bash child with no cwd option sees $PWD as the fallback."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    lane = tmp_path / "lane"
    lane.mkdir()
    probe = tmp_path / "pwd_env.cjs"
    probe.write_text(
        "const { spawn } = require('child_process');\n"
        "const child = spawn('/bin/bash', ['-c', 'printf %s \"$PWD\"']);\n"
        "child.stdout.on('data', (b) => process.stdout.write(b));\n"
        "child.on('close', (code) => process.exit(code == null ? 1 : code));\n",
        encoding="utf-8",
    )
    proc = _run_node(node, [str(probe)], _armed_preload_env(lane, align=True), tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(lane)


def test_shell_cwd_preload_spawn_url_cwd_not_replaced(tmp_path: Path) -> None:
    """A URL cwd is not treated as empty and is not replaced by the fallback."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    lane = tmp_path / "lane"
    lane.mkdir()
    probe = tmp_path / "url_cwd.cjs"
    probe.write_text(
        "const { spawn } = require('child_process');\n"
        "const child = spawn('/bin/bash', ['-c', 'pwd'], { cwd: new URL('file:///tmp') });\n"
        "let out = '';\n"
        "child.stdout.on('data', (b) => { out += b; });\n"
        "child.on('error', (err) => {\n"
        "  process.stderr.write('spawn_error ' + err.code + '\\n');\n"
        "  process.exit(2);\n"
        "});\n"
        "child.on('close', (code) => {\n"
        "  process.stdout.write(out);\n"
        "  process.exit(code == null ? 1 : code);\n"
        "});\n",
        encoding="utf-8",
    )
    proc = _run_node(node, [str(probe)], _armed_preload_env(lane, align=True), tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "/tmp"
    assert proc.stdout.strip() != str(lane)


def test_shell_cwd_preload_invalid_fallback_stderr(tmp_path: Path) -> None:
    """A non-directory fallback logs shell_cwd_fallback_invalid and exits 0."""
    node = _vendored_node()
    if node is None:
        pytest.skip("vendored cursor-sdk bin/node is absent")
    env = _armed_preload_env(Path("/nonexistent/for/shell_cwd_test"), align=True)
    proc = _run_node(node, ["-e", "console.log(process.cwd())"], env, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(tmp_path)
    assert "shell_cwd_fallback_invalid" in proc.stderr
    assert "/nonexistent/for/shell_cwd_test" in proc.stderr


def test_launch_sdk_bridge_align_flag_follows_context_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``launch_sdk_bridge`` maps lane B to CURSOR_SDK_SHELL_ALIGN_CWD and lane A omits it.

    Flag-level ``build_bridge_command(align_shell_cwd=)`` tests do not cover
    ``align_shell_cwd=ctx.lane == "B"``. A lane A dispatch must not carry the
    assignment; a lane B dispatch must.
    """
    import httpx

    from services.git_integration_worker.config import WorkerConfig
    from services.git_integration_worker.cursor_sdk_capture_binding import (
        CaptureBinding,
    )
    from services.git_integration_worker.cursor_sdk_dispatch_context import (
        SdkDispatchContext,
    )

    captured: list[list[str]] = []

    def _launch_bridge(*, command, **_kwargs):
        captured.append(list(command))
        return object()

    monkeypatch.setattr(bridge_launch.Client, "launch_bridge", _launch_bridge)

    hub = tmp_path / "source_repo"
    hub.mkdir()
    wt_root = tmp_path / "worktree_root"
    wt_root.mkdir()
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=hub,
        worktree_root=wt_root,
        dispatch_workspace=tmp_path / "dispatch_ws",
        green_gate_cmd=["true"],
    )
    home = tmp_path / "dispatch-home"
    home.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    venv = _fake_repo_venv(tmp_path)
    timeout = httpx.Timeout(5.0)
    common = dict(
        bridge_state=state,
        dispatch_home=home,
        repo_venv=venv,
        real_home=tmp_path / "operator-home",
        local=None,
        client_timeout=timeout,
    )

    ctx_a = SdkDispatchContext(
        dispatch_id="disp-lane-a",
        thread_id="thread-a",
        handoff_contract="consult",
        hub=hub,
        dispatch_workspace=hub,
        capture_binding=CaptureBinding.lane_a(cfg),
    )
    write_tree = tmp_path / "lane-b-wt"
    write_tree.mkdir()
    ctx_b = SdkDispatchContext(
        dispatch_id="disp-lane-b",
        thread_id="thread-b",
        handoff_contract="implement",
        hub=hub,
        dispatch_workspace=write_tree,
        capture_binding=CaptureBinding.lane_b(cfg, write_tree),
    )

    launch_sdk_bridge(ctx_a, **common)
    launch_sdk_bridge(ctx_b, **common)

    assert len(captured) == 2
    assert "CURSOR_SDK_SHELL_ALIGN_CWD=1" not in captured[0]
    assert "CURSOR_SDK_SHELL_ALIGN_CWD=1" in captured[1]


def test_resolve_bridge_bin_is_absolute_file() -> None:
    """The pinned wheel must resolve to an absolute, existing launcher."""
    resolved = resolve_bridge_bin()
    assert os.path.isabs(resolved), resolved
    assert os.path.isfile(resolved), resolved
    assert "=" not in resolved and not resolved.startswith("-")
