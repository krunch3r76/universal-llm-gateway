"""Hermetic tests for customize_upload_bytes recon authority."""

from __future__ import annotations

from claude_bundles.customize_upload_bytes import customize_upload_bytes
from claude_bundles.skills_api import prepare_claude_ai_upload_md


def _life_local_sot(repo: object, slug: str, *, literary_h1: str, description: str) -> None:
    path = repo / ".claude" / "skills" / slug / "SKILL.md"  # type: ignore[operator]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n"
        f"# {literary_h1}\n\nBody.\n",
        encoding="utf-8",
    )


def test_customize_upload_bytes_matches_prepare_claude_ai_upload_md(
    tmp_path, monkeypatch
) -> None:
    slug = "srm"
    _life_local_sot(
        tmp_path,
        slug,
        literary_h1="Strategic Resource Management",
        description=(
            "Strategic resource management for life-operator workflows, planning, "
            "and portfolio decisions across domains."
        ),
    )
    monkeypatch.setattr(
        "claude_bundles.customize_upload_bytes.claude_ai_bundle_dir",
        lambda repo, s: repo / ".claude" / "skills" / s,
    )
    skill_md = tmp_path / ".claude" / "skills" / slug / "SKILL.md"
    staging = tmp_path / "staging"
    upload_path, _, _ = prepare_claude_ai_upload_md(skill_md, staging, slug=slug)
    assert customize_upload_bytes(tmp_path, slug) == upload_path.read_bytes()


def test_life_local_literary_h1_is_adapted_not_raw_sot(tmp_path, monkeypatch) -> None:
    slug = "srm"
    _life_local_sot(
        tmp_path,
        slug,
        literary_h1="Strategic Resource Management",
        description=(
            "Strategic resource management for life-operator workflows, planning, "
            "and portfolio decisions across domains."
        ),
    )
    monkeypatch.setattr(
        "claude_bundles.customize_upload_bytes.claude_ai_bundle_dir",
        lambda repo, s: repo / ".claude" / "skills" / s,
    )
    raw = (tmp_path / ".claude" / "skills" / slug / "SKILL.md").read_bytes()
    adapted = customize_upload_bytes(tmp_path, slug)
    assert adapted != raw
    assert "Srm — Strategic Resource Management".encode() in adapted
