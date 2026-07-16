"""Advisory Claude.ai Project chrome via Jupiter CDP session cookies.

Uses the authenticated ``claude-ai-chrome-profile`` (same lane as Skills UI).
Private org APIs — fragile; never birth-gate-bearing.

Dogfood shapes (2026-07-16):
  POST /api/organizations/{org}/projects {name, description} → 201
  PUT  /api/organizations/{org}/projects/{uuid} {prompt_template} → 202
  PUT  ... {is_archived: true} → 202
  DELETE ... → 204
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page

from claude_bundles.project_chrome_prompt import (
    ProjectChromeSpec,
    build_description,
    build_prompt_template,
)
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

DEFAULT_ORG_ID = "3a71fd02-ae6d-4d16-964b-38b2959c0940"
PROJECTS_ORIGIN = "https://claude.ai"


@dataclass(frozen=True)
class ProjectChromeResult:
    uuid: str
    name: str
    url: str
    org_id: str
    prompt_len: int
    created: bool


def project_url(uuid: str) -> str:
    return f"{PROJECTS_ORIGIN}/cowork/project/{uuid}"


async def _api_fetch(
    page: Page,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Authenticated fetch against claude.ai private APIs in the CDP page."""
    expr = """
    async ({ method, path, body }) => {
      const opts = {
        method,
        credentials: 'include',
        headers: { Accept: 'application/json' },
      };
      if (body !== null && body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(path, opts);
      const text = await r.text();
      let parsed = null;
      if (text) {
        try { parsed = JSON.parse(text); } catch (e) { parsed = text.slice(0, 2000); }
      }
      return { status: r.status, ok: r.ok, parsed };
    }
    """
    return await page.evaluate(expr, {"method": method, "path": path, "body": body})


async def ensure_claude_origin(page: Page) -> None:
    if "claude.ai" not in (page.url or ""):
        await page.goto(f"{PROJECTS_ORIGIN}/projects", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)


async def create_project(
    page: Page,
    *,
    org_id: str,
    name: str,
    description: str,
) -> dict[str, Any]:
    path = f"/api/organizations/{org_id}/projects"
    result = await _api_fetch(
        page, "POST", path, {"name": name, "description": description}
    )
    if result.get("status") != 201:
        raise RuntimeError(f"create project failed: {json.dumps(result)[:800]}")
    parsed = result.get("parsed") or {}
    if not isinstance(parsed, dict) or not parsed.get("uuid"):
        raise RuntimeError(f"create missing uuid: {json.dumps(result)[:800]}")
    return parsed


async def put_prompt_template(
    page: Page,
    *,
    org_id: str,
    uuid: str,
    prompt_template: str,
) -> dict[str, Any]:
    path = f"/api/organizations/{org_id}/projects/{uuid}"
    result = await _api_fetch(page, "PUT", path, {"prompt_template": prompt_template})
    status = result.get("status")
    if status not in (200, 202):
        raise RuntimeError(f"put prompt_template failed: {json.dumps(result)[:800]}")
    return result.get("parsed") or {}


async def get_project(page: Page, *, org_id: str, uuid: str) -> dict[str, Any]:
    path = f"/api/organizations/{org_id}/projects/{uuid}"
    result = await _api_fetch(page, "GET", path)
    if result.get("status") != 200:
        raise RuntimeError(f"get project failed: {json.dumps(result)[:800]}")
    parsed = result.get("parsed") or {}
    if not isinstance(parsed, dict):
        raise RuntimeError(f"get project non-object: {json.dumps(result)[:800]}")
    return parsed


async def archive_project(page: Page, *, org_id: str, uuid: str) -> None:
    path = f"/api/organizations/{org_id}/projects/{uuid}"
    result = await _api_fetch(page, "PUT", path, {"is_archived": True})
    if result.get("status") not in (200, 202):
        raise RuntimeError(f"archive failed: {json.dumps(result)[:800]}")


async def delete_project(page: Page, *, org_id: str, uuid: str) -> None:
    path = f"/api/organizations/{org_id}/projects/{uuid}"
    result = await _api_fetch(page, "DELETE", path)
    if result.get("status") not in (200, 204):
        raise RuntimeError(f"delete failed: {json.dumps(result)[:800]}")


async def ensure_project_chrome(
    page: Page,
    spec: ProjectChromeSpec,
    *,
    org_id: str = DEFAULT_ORG_ID,
    prompt_template: str | None = None,
) -> ProjectChromeResult:
    """Create Project + set instructions. Always creates a new Project."""
    await ensure_claude_origin(page)
    description = build_description(spec)
    prompt = (
        prompt_template if prompt_template is not None else build_prompt_template(spec)
    )
    created = await create_project(
        page, org_id=org_id, name=spec.name, description=description
    )
    uuid = str(created["uuid"])
    await put_prompt_template(page, org_id=org_id, uuid=uuid, prompt_template=prompt)
    # Re-GET to confirm persistence (dogfood saw rare race on immediate read).
    body = await get_project(page, org_id=org_id, uuid=uuid)
    prompt_len = len(body.get("prompt_template") or "")
    if prompt_len == 0:
        # One retry after short wait — eventual consistency guard.
        await page.wait_for_timeout(1500)
        body = await get_project(page, org_id=org_id, uuid=uuid)
        prompt_len = len(body.get("prompt_template") or "")
    if prompt_len == 0:
        raise RuntimeError(
            f"prompt_template did not persist for {uuid} after PUT+retry GET"
        )
    return ProjectChromeResult(
        uuid=uuid,
        name=str(body.get("name") or spec.name),
        url=project_url(uuid),
        org_id=org_id,
        prompt_len=prompt_len,
        created=True,
    )


async def destroy_project_chrome(page: Page, *, org_id: str, uuid: str) -> None:
    await ensure_claude_origin(page)
    await archive_project(page, org_id=org_id, uuid=uuid)
    await delete_project(page, org_id=org_id, uuid=uuid)


async def run_ensure(
    spec: ProjectChromeSpec,
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    org_id: str = DEFAULT_ORG_ID,
    prompt_template: str | None = None,
) -> ProjectChromeResult:
    pw, _browser, _context, page = await connect_cdp(cdp_url)
    try:
        return await ensure_project_chrome(
            page, spec, org_id=org_id, prompt_template=prompt_template
        )
    finally:
        await pw.stop()


async def refresh_project_chrome(
    page: Page,
    spec: ProjectChromeSpec,
    *,
    uuid: str,
    org_id: str = DEFAULT_ORG_ID,
    prompt_template: str | None = None,
) -> ProjectChromeResult:
    """Re-PUT instructions on an existing Project (no create)."""
    await ensure_claude_origin(page)
    prompt = (
        prompt_template if prompt_template is not None else build_prompt_template(spec)
    )
    await put_prompt_template(page, org_id=org_id, uuid=uuid, prompt_template=prompt)
    body = await get_project(page, org_id=org_id, uuid=uuid)
    prompt_len = len(body.get("prompt_template") or "")
    if prompt_len == 0:
        await page.wait_for_timeout(1500)
        body = await get_project(page, org_id=org_id, uuid=uuid)
        prompt_len = len(body.get("prompt_template") or "")
    if prompt_len == 0:
        raise RuntimeError(
            f"prompt_template did not persist for {uuid} after refresh PUT+retry GET"
        )
    return ProjectChromeResult(
        uuid=uuid,
        name=str(body.get("name") or spec.name),
        url=project_url(uuid),
        org_id=org_id,
        prompt_len=prompt_len,
        created=False,
    )


async def run_refresh(
    spec: ProjectChromeSpec,
    *,
    uuid: str,
    cdp_url: str = DEFAULT_CDP_URL,
    org_id: str = DEFAULT_ORG_ID,
    prompt_template: str | None = None,
) -> ProjectChromeResult:
    pw, _browser, _context, page = await connect_cdp(cdp_url)
    try:
        return await refresh_project_chrome(
            page, spec, uuid=uuid, org_id=org_id, prompt_template=prompt_template
        )
    finally:
        await pw.stop()


async def run_destroy(
    *,
    uuid: str,
    cdp_url: str = DEFAULT_CDP_URL,
    org_id: str = DEFAULT_ORG_ID,
) -> None:
    pw, _browser, _context, page = await connect_cdp(cdp_url)
    try:
        await destroy_project_chrome(page, org_id=org_id, uuid=uuid)
    finally:
        await pw.stop()


async def run_get(
    *,
    uuid: str,
    cdp_url: str = DEFAULT_CDP_URL,
    org_id: str = DEFAULT_ORG_ID,
) -> dict[str, Any]:
    pw, _browser, _context, page = await connect_cdp(cdp_url)
    try:
        await ensure_claude_origin(page)
        return await get_project(page, org_id=org_id, uuid=uuid)
    finally:
        await pw.stop()
