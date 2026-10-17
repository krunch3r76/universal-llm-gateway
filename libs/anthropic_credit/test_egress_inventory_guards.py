"""Guard tests for egress_inventory derivation and scan rules."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from libs.anthropic_credit import egress_inventory
from libs.anthropic_credit.egress_inventory import (
    _API_DOMAIN_RE,
    _KEY_HEADER_RE,
    _RESOLVE_ANTHROPIC_RE,
    _SK_ANT_KEY_RE,
    derive_served_roots,
    render,
    scan,
)

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]

_FAKE_APP = """\
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .routers import wb

app = FastAPI()
wb.mount_web(app)
"""

_FAKE_ROUTER = """\
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "web"
    / "dist"
)

def mount_web(app):
    app.mount(
        "/web",
        StaticFiles(directory=str(_DIR)),
        name="web",
    )
"""

_API_SNIPPET = "https://api.anthropic.com/v1/messages"


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "guard@test.local"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "guard"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _write_fake_stargate(repo: Path, router_body: str | None = None) -> None:
    base = repo / "services/universal-stargate/systems/proxy"
    (base / "routers").mkdir(parents=True)
    (base / "routers" / "__init__.py").write_text("", encoding="utf-8")
    (base / "app.py").write_text(_FAKE_APP, encoding="utf-8")
    (base / "routers" / "wb.py").write_text(
        router_body or _FAKE_ROUTER, encoding="utf-8"
    )


def _commit_all(repo: Path, message: str = "init") -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True
    )


def _fixture_allowlist() -> dict[str, str]:
    return {"services/web/dist": "fixture allowlist for guard G3"}


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(egress_inventory, "_SERVED_ROOT_ALLOWLIST", {})
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _write_fake_stargate(repo)
    (repo / "services/web/dist").mkdir(parents=True)
    _commit_all(repo)
    return repo


def test_g2a_gitignored_outside_served_root_not_scanned(fake_repo: Path) -> None:
    x_dir = fake_repo / "services/x"
    x_dir.mkdir(parents=True)
    (x_dir / ".gitignore").write_text("ignored.js\n", encoding="utf-8")
    ignored = x_dir / "ignored.js"
    ignored.write_text(_API_SNIPPET, encoding="utf-8")
    check = subprocess.run(
        ["git", "check-ignore", "services/x/ignored.js"],
        cwd=fake_repo,
        capture_output=True,
        check=False,
    )
    assert check.returncode == 0
    report = scan(fake_repo)
    assert report.ok, render(report)


def test_g2b_tracked_outside_served_root_flagged(fake_repo: Path) -> None:
    tracked = fake_repo / "services/x/tracked.js"
    tracked.parent.mkdir(parents=True)
    tracked.write_text(_API_SNIPPET, encoding="utf-8")
    _commit_all(fake_repo, "add tracked")
    report = scan(fake_repo)
    assert not report.ok
    assert any("tracked.js" in v for v in report.tracked_whole_file_violations)


def test_g3a_keyless_served_in_allowlist_passes(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory, "_SERVED_ROOT_ALLOWLIST", _fixture_allowlist()
    )
    served = fake_repo / "services/web/dist/a.js"
    served.write_text(_API_SNIPPET, encoding="utf-8")
    report = scan(fake_repo)
    assert report.ok, render(report)
    assert report.keyless_receipts


def test_g3b_key_header_in_served_fails_even_allowlisted(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory, "_SERVED_ROOT_ALLOWLIST", _fixture_allowlist()
    )
    served = fake_repo / "services/web/dist/a.js"
    served.write_text(
        _API_SNIPPET + '\nheaders:{"x-api-key":k}',
        encoding="utf-8",
    )
    report = scan(fake_repo)
    assert not report.ok
    assert report.served_key_header_violations


def test_g3c_dangerous_direct_header_fails(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory, "_SERVED_ROOT_ALLOWLIST", _fixture_allowlist()
    )
    served = fake_repo / "services/web/dist/a.js"
    served.write_text(
        _API_SNIPPET + '\n"anthropic-dangerous-direct-browser-access":"true"',
        encoding="utf-8",
    )
    report = scan(fake_repo)
    assert not report.ok
    assert report.served_key_header_violations


def test_g3e_tracked_served_key_header_fails_even_allowlisted(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "services/web/dist/app.js"
    monkeypatch.setattr(
        egress_inventory,
        "_SERVED_ROOT_ALLOWLIST",
        _fixture_allowlist(),
    )
    monkeypatch.setitem(
        egress_inventory._ALLOWLIST,
        rel,
        "fixture tracked allowlist must not excuse served key header",
    )
    served = fake_repo / rel
    served.parent.mkdir(parents=True, exist_ok=True)
    served.write_text(
        _API_SNIPPET + '\nheaders:{"x-api-key":k}',
        encoding="utf-8",
    )
    _commit_all(fake_repo, "tracked served with key header")
    report = scan(fake_repo)
    assert not report.ok
    assert any("app.js" in v for v in report.served_key_header_violations)


def test_g3d_keyless_served_not_allowlisted_fails(fake_repo: Path) -> None:
    served = fake_repo / "services/web/dist/a.js"
    served.write_text(_API_SNIPPET, encoding="utf-8")
    report = scan(fake_repo)
    assert not report.ok
    assert report.served_unlisted_violations


def test_d1_env_directory_outside_grammar_fails(fake_repo: Path) -> None:
    bad_router = """\
import os
from fastapi.staticfiles import StaticFiles

def mount_web(app):
    app.mount("/web", StaticFiles(directory=os.environ["X"]), name="web")
"""
    (fake_repo / "services/universal-stargate/systems/proxy/routers/wb.py").write_text(
        bad_router, encoding="utf-8"
    )
    _commit_all(fake_repo, "bad router")
    report = scan(fake_repo)
    assert not report.ok
    assert report.derivation_errors
    assert any("outside grammar" in e for e in report.derivation_errors)


def test_d2_stray_staticfiles_unresolved(fake_repo: Path) -> None:
    stray = fake_repo / "services/universal-stargate/systems/extra.py"
    stray.write_text(
        "from fastapi.staticfiles import StaticFiles\nStaticFiles(directory='x')\n",
        encoding="utf-8",
    )
    _commit_all(fake_repo, "stray static")
    report = scan(fake_repo)
    assert not report.ok
    assert report.derivation_errors
    assert any("unresolved StaticFiles mount" in e for e in report.derivation_errors)


def test_d3_stale_served_allowlist_key(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory,
        "_SERVED_ROOT_ALLOWLIST",
        {"services/no/such/root": "stale"},
    )
    report = scan(fake_repo)
    assert not report.ok
    assert report.stale_allowlist_keys


def test_d4_not_a_git_repo_fails(tmp_path: Path) -> None:
    repo = tmp_path / "notgit"
    repo.mkdir()
    _write_fake_stargate(repo)
    report = scan(repo)
    assert not report.ok
    assert report.derivation_errors
    assert any(":git-ls-files:" in e for e in report.derivation_errors)


def test_d5_zero_roots_fails(tmp_path: Path) -> None:
    repo = tmp_path / "zero"
    repo.mkdir()
    _git_init(repo)
    base = repo / "services/universal-stargate/systems/proxy"
    base.mkdir(parents=True)
    (base / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    _commit_all(repo)
    report = scan(repo)
    assert not report.ok
    assert any(
        "app.py:1" in e and "zero served roots derived" in e
        for e in report.derivation_errors
    )


def test_d6_root_outside_repo_fails(fake_repo: Path) -> None:
    outside_router = """\
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent.parent.parent
    / "outside"
)

def mount_web(app):
    app.mount(
        "/web",
        StaticFiles(directory=str(_DIR)),
        name="web",
    )
"""
    (fake_repo / "services/outside").mkdir(parents=True, exist_ok=True)
    (fake_repo / "services/universal-stargate/systems/proxy/routers/wb.py").write_text(
        outside_router, encoding="utf-8"
    )
    _commit_all(fake_repo, "outside root")
    report = scan(fake_repo)
    assert not report.ok
    assert any(
        "static root resolves outside repository" in e for e in report.derivation_errors
    )


def test_tracked_non_utf8_still_flags_api(fake_repo: Path) -> None:
    bad = fake_repo / "services/x/badutf.py"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"\xff\xfe\n" + b'X="https://api.anthropic.com/v1/messages"\n')
    _commit_all(fake_repo, "non-utf8 tracked")
    report = scan(fake_repo)
    assert not report.ok
    assert any("badutf.py" in v for v in report.tracked_whole_file_violations)


def test_served_symlink_inside_root_scanned(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory, "_SERVED_ROOT_ALLOWLIST", _fixture_allowlist()
    )
    inner = fake_repo / "services/web/dist/nested"
    inner.mkdir(parents=True)
    linked = inner / "a.js"
    linked.write_text(_API_SNIPPET, encoding="utf-8")
    link = fake_repo / "services/web/dist/link.js"
    link.symlink_to(linked)
    report = scan(fake_repo)
    assert report.ok, render(report)
    assert report.keyless_receipts


def test_served_symlink_cycle_fails_closed(fake_repo: Path) -> None:
    dist = fake_repo / "services/web/dist"
    a = dist / "a"
    b = dist / "b"
    a.mkdir()
    b.mkdir()
    a.rmdir()
    b.rmdir()
    a.symlink_to(b, target_is_directory=True)
    b.symlink_to(a, target_is_directory=True)
    report = scan(fake_repo)
    assert not report.ok
    assert any("symlink loop in served root" in e for e in report.scan_errors)


def test_served_dir_symlink_alias_inside_root_ok(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory, "_SERVED_ROOT_ALLOWLIST", _fixture_allowlist()
    )
    nested = fake_repo / "services/web/dist/nested"
    nested.mkdir(parents=True)
    (nested / "a.js").write_text(_API_SNIPPET, encoding="utf-8")
    alias = fake_repo / "services/web/dist/alias"
    alias.symlink_to(nested, target_is_directory=True)
    report = scan(fake_repo)
    assert report.ok, render(report)
    assert not report.scan_errors
    assert any("nested/a.js" in r for r in report.keyless_receipts)


def test_line_allowlist_miss_does_not_echo_key_literal(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rel = "services/x/adapter.py"
    monkeypatch.setitem(
        egress_inventory._LINE_ALLOWLIST,
        rel,
        (r"^SAFE_LINE$",),
    )
    key_literal = "sk-" + "ant-" + "A" * 24
    adapter = fake_repo / rel
    adapter.parent.mkdir(parents=True)
    adapter.write_text(
        "SAFE_LINE\n" + f"{_API_SNIPPET} {key_literal}\n",
        encoding="utf-8",
    )
    _commit_all(fake_repo, "adapter line allowlist miss")
    report = scan(fake_repo)
    out = render(report)
    assert not report.ok
    assert f"{rel}:2" in report.tracked_line_violations
    assert key_literal not in out
    assert all(key_literal not in v for v in report.tracked_line_violations)


def test_served_symlink_outside_root_fails(fake_repo: Path) -> None:
    outside = fake_repo / "services/outside.js"
    outside.write_text("outside\n", encoding="utf-8")
    link = fake_repo / "services/web/dist/escape.js"
    link.symlink_to(outside)
    report = scan(fake_repo)
    assert not report.ok
    assert report.scan_errors
    assert any("outside served root" in e for e in report.scan_errors)


def test_module_source_does_not_self_match() -> None:
    module_path = Path(egress_inventory.__file__)
    source = module_path.read_text(encoding="utf-8")
    assert _API_DOMAIN_RE.search(source) is None
    assert _RESOLVE_ANTHROPIC_RE.search(source) is None
    assert _KEY_HEADER_RE.search(source) is None
    assert _SK_ANT_KEY_RE.search(source) is None


def test_sk_ant_literal_hard_fail_tracked(fake_repo: Path) -> None:
    key_literal = "sk-" + "ant-" + "A" * 24
    tracked = fake_repo / "services/x/leak.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text(f'KEY = "{key_literal}"\n', encoding="utf-8")
    _commit_all(fake_repo, "key tracked")
    report = scan(fake_repo)
    assert not report.ok
    assert any("leak.py" in v for v in report.key_literal_violations)


def test_sk_ant_literal_hard_fail_served(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory, "_SERVED_ROOT_ALLOWLIST", _fixture_allowlist()
    )
    key_literal = "sk-" + "ant-" + "A" * 24
    served = fake_repo / "services/web/dist/leak.js"
    served.write_text(f'var k="{key_literal}";\n', encoding="utf-8")
    report = scan(fake_repo)
    assert not report.ok
    assert any("leak.js" in v for v in report.key_literal_violations)


def test_served_roots_derived_from_real_stargate() -> None:
    roots = derive_served_roots(_REPO_ROOT)
    by_dir = {r.rel_dir: r for r in roots}
    assert set(by_dir) == {
        "services/cortex-workbench-app/dist",
        "services/universal-stargate/static/local-models",
    }
    assert by_dir[
        "services/universal-stargate/static/local-models"
    ].mount_site.endswith("app.py:601")
    assert by_dir["services/cortex-workbench-app/dist"].mount_site.endswith(
        "app.py:602"
    )
