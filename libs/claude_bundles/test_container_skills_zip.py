"""Hermetic tests for container skills zip inventory and upload-byte compare."""

from __future__ import annotations

import zipfile

from claude_bundles.bundle_description import adapt_skill_md_for_claude_ai
from claude_bundles.container_skills_zip import (
    compare_user_zip,
    pick_download_label,
    skill_md_key,
    tree_slugs,
)


def _literary_life_local_text(slug: str, literary_h1: str) -> str:
    return (
        f"---\nname: {slug}\n"
        f"description: Test fixture for customize upload bytes recon with literary H1.\n"
        f"---\n# {literary_h1}\n\nBody.\n"
    )


def _adapted_upload_bytes(slug: str, literary_h1: str) -> bytes:
    adapted, _ = adapt_skill_md_for_claude_ai(
        _literary_life_local_text(slug, literary_h1), slug=slug
    )
    return adapted.encode()


def test_tree_slugs_skips_sidecars_and_dirs() -> None:
    names = [
        "skills/user/advisor-timing/",
        "skills/user/advisor-timing/SKILL.md",
        "skills/user/advisor-timing.skill",
        "skills/user/manifest.json",
        "skills/examples/import-memory/SKILL.md",
        "skills/public/pdf/SKILL.md",
        "readme.txt",
    ]
    assert tree_slugs(names, "user") == {"advisor-timing"}
    assert tree_slugs(names, "examples") == {"import-memory"}
    assert tree_slugs(names, "public") == {"pdf"}


def test_skill_md_key() -> None:
    assert skill_md_key("user", "fs") == "skills/user/fs/SKILL.md"


def test_compare_user_zip_mirrors_and_subtracts_stock(tmp_path) -> None:
    zpath = tmp_path / "skills.zip"
    staged = tmp_path / "fs" / "SKILL.md"
    staged.parent.mkdir()
    body = b"# fs\n"
    staged.write_bytes(body)
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("skills/user/fs/SKILL.md", body)
        zf.writestr("skills/user/model-tier-awareness-web/SKILL.md", b"extra")
        zf.writestr("skills/user/docx/SKILL.md", b"stock-copy")
        zf.writestr("skills/public/docx/SKILL.md", b"stock")
        zf.writestr("skills/user/manifest.json", b"{}")
    plan = compare_user_zip(
        zpath,
        catalog={"fs", "advisor-timing"},
        staged_bytes={"fs": body},
    )
    assert plan.extra == ["model-tier-awareness-web"]
    assert plan.missing == ["advisor-timing"]
    assert plan.match == ["fs"]
    assert not plan.stale
    assert not plan.mirrored()


def test_adapted_life_local_fixture_matches_not_stale(tmp_path) -> None:
    fixtures = {
        "srm": "Strategic Resource Management",
        "document-review-timeline-linkage-audit": (
            "Document Review Timeline Linkage Audit"
        ),
    }
    staged_bytes = {
        slug: _adapted_upload_bytes(slug, literary_h1)
        for slug, literary_h1 in fixtures.items()
    }
    zpath = tmp_path / "life_local.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for slug, upload_bytes in staged_bytes.items():
            zf.writestr(f"skills/user/{slug}/SKILL.md", upload_bytes)
    plan = compare_user_zip(
        zpath,
        catalog=set(fixtures),
        staged_bytes=staged_bytes,
    )
    assert sorted(plan.match) == sorted(fixtures)
    assert not plan.stale
    assert plan.mirrored()


def test_raw_sot_vs_adapted_zip_is_stale(tmp_path) -> None:
    slug = "srm"
    literary_h1 = "Strategic Resource Management"
    raw_sot = _literary_life_local_text(slug, literary_h1).encode()
    adapted = _adapted_upload_bytes(slug, literary_h1)
    assert raw_sot != adapted
    zpath = tmp_path / "adapted.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(f"skills/user/{slug}/SKILL.md", adapted)
    plan = compare_user_zip(
        zpath,
        catalog={slug},
        staged_bytes={slug: raw_sot},
    )
    assert slug in plan.stale
    assert slug not in plan.match


def test_pick_download_prefers_skills_card() -> None:
    assert (
        pick_download_label(["Copy", "Download", "Download Claude skills"])
        == "Download Claude skills"
    )
    assert pick_download_label(["Download skills zip"]) == "Download skills zip"
    assert pick_download_label(["Copy", "Open"]) is None
