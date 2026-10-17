"""Guard tests for egress_inventory derivation and scan rules."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from libs.anthropic_credit import egress_inventory
from libs.anthropic_credit.egress_inventory import derive_served_roots, render, scan

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
    ignored = fake_repo / "services/x/ignored.js"
    ignored.parent.mkdir(parents=True)
    ignored.write_text(_API_SNIPPET, encoding="utf-8")
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
