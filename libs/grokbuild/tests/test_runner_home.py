"""Tests for runner_home: dispatch HOME config.toml generation."""

from __future__ import annotations

from pathlib import Path

from grokbuild.runner_home import (
    _build_config_toml,
    _strip_toml_section,
    setup_dispatch_home,
)


class TestStripTomlSection:
    def test_removes_target_section(self) -> None:
        text = (
            '[model."xai/grok-4.3__effort_high"]\n'
            'model = "grok-4.3"\n'
            "[mcp_servers.user-vortex]\n"
            'url = "https://example.com"\n'
            "enabled = true\n"
        )
        result = _strip_toml_section(text, "[mcp_servers.user-vortex")
        assert "[mcp_servers.user-vortex]" not in result
        assert 'model = "grok-4.3"' in result

    def test_removes_subsection(self) -> None:
        text = (
            "[mcp_servers.user-vortex]\n"
            'url = "x"\n'
            "[mcp_servers.user-vortex.headers]\n"
            'Authorization = "Bearer t"\n'
            "[other]\n"
            "key = 1\n"
        )
        result = _strip_toml_section(text, "[mcp_servers.user-vortex")
        assert "[mcp_servers.user-vortex]" not in result
        assert "[mcp_servers.user-vortex.headers]" not in result
        assert "[other]" in result
        assert "key = 1" in result

    def test_noop_when_section_absent(self) -> None:
        text = '[model."xai/grok-4.3__effort_high"]\nmodel = "g"\n'
        assert _strip_toml_section(text, "[mcp_servers.user-vortex") == text


class TestBuildConfigToml:
    def test_no_host_config(self, tmp_path: Path) -> None:
        result = _build_config_toml(
            "tok", "d1", str(tmp_path / "nonexistent"), overlay_mcp=True
        )
        assert "[mcp_servers.user-vortex]" in result
        assert 'X-Grokbuild-Dispatch-Id = "d1"' in result
        assert 'Authorization = "Bearer tok"' in result

    def test_preserves_host_model_stanzas(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "real"
        (fake_home / ".grok").mkdir(parents=True)
        (fake_home / ".grok" / "config.toml").write_text(
            '[model."xai/grok-4.3__effort_high"]\n'
            'model = "grok-4.3__effort_high"\n'
            'base_url = "http://localhost:9999/api/v1/providers/xai"\n'
        )
        result = _build_config_toml("tok", "d1", str(fake_home), overlay_mcp=True)
        assert "xai/grok-4.3__effort_high" not in result
        assert 'base_url = "http://localhost:9999/api/v1/providers/xai"' not in result
        assert "[mcp_servers.user-vortex]" in result
        assert 'X-Grokbuild-Dispatch-Id = "d1"' in result

    def test_no_overlay_keeps_host_mcp_strips_models(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "real"
        (fake_home / ".grok").mkdir(parents=True)
        (fake_home / ".grok" / "config.toml").write_text(
            '[model."xai/grok-4.3__effort_high"]\n'
            'model = "grok-4.3"\n'
            "[mcp_servers.user-vortex]\n"
            'url = "https://mcp.k-1.me/mcp/grok"\n'
            "enabled = true\n"
        )
        result = _build_config_toml("tok", "d1", str(fake_home), overlay_mcp=False)
        assert "xai/grok-4.3__effort_high" not in result
        assert "[mcp_servers.user-vortex]" in result
        assert "https://mcp.k-1.me/mcp/grok" in result
        assert "X-Grokbuild-Dispatch-Id" not in result

    def test_host_mcp_section_replaced(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "real"
        (fake_home / ".grok").mkdir(parents=True)
        (fake_home / ".grok" / "config.toml").write_text(
            "[mcp_servers.user-vortex]\n"
            'url = "https://mcp.k-1.me/mcp/grok"\n'
            "enabled = true\n"
            "[mcp_servers.user-vortex.headers]\n"
            'Authorization = "Bearer old-token"\n'
        )
        result = _build_config_toml("new-token", "d2", str(fake_home), overlay_mcp=True)
        assert "old-token" not in result
        assert 'Authorization = "Bearer new-token"' in result
        assert result.count("[mcp_servers.user-vortex]") == 1


def test_dispatch_home_strips_host_model_stanzas(tmp_path: Path) -> None:
    fake_home = tmp_path / "real"
    (fake_home / ".grok").mkdir(parents=True)
    (fake_home / ".grok" / "config.toml").write_text(
        '[model."xai/grok-4.3__effort_high"]\n'
        'model = "grok-4.3__effort_high"\n'
        'base_url = "http://localhost:9999/api/v1/providers/xai"\n'
    )
    home = setup_dispatch_home(
        "d1", tmp_path / "sidecar", token="t", real_home=str(fake_home)
    )
    txt = (home / ".grok" / "config.toml").read_text()
    assert "xai/grok-4.3__effort_high" not in txt
    assert 'X-Grokbuild-Dispatch-Id = "d1"' in txt
