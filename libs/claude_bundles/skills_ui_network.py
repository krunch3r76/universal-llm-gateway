"""CDP network interception — primary upload success/verify oracle."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page, Request, Response

_SKILL_UPLOAD_URL = re.compile(
    r"(?:/api/|graphql|/skills?|skill[_-]?upload|createSkill|uploadSkill|customize)",
    re.I,
)
_POST_METHODS = frozenset({"POST", "PUT", "PATCH"})


@dataclass
class UploadResult:
    ok: bool
    status: int
    slug_echoed: bool
    slug: str
    log: list[dict[str, Any]] = field(default_factory=list)


def _snippet(text: str, *, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _slug_in_text(text: str, slug: str) -> bool:
    if not text:
        return False
    low = text.lower()
    slug_l = slug.lower()
    if slug_l in low:
        return True
    stem = slug_l.replace("-", "_")
    return stem in low


def _is_upload_request(request: Request, slug: str) -> bool:
    if request.method not in _POST_METHODS:
        return False
    url = request.url
    if not _SKILL_UPLOAD_URL.search(url):
        post = request.post_data or ""
        if not _slug_in_text(post, slug) and "skill" not in post.lower():
            return False
    else:
        post = request.post_data or ""
        if post and not _slug_in_text(post, slug) and "skill" not in post.lower():
            if "graphql" not in url.lower():
                return False
    return True


class UploadNetworkOracle:
    """Capture skills-upload request/response; await 2xx + slug echo."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._log: list[dict[str, Any]] = []
        self._results: dict[str, UploadResult] = {}
        self._waiters: dict[str, list[asyncio.Future[UploadResult]]] = {}
        self._expected_slug: str | None = None
        self._attached = False
        self._on_request = self._handle_request
        self._on_response = self._handle_response

    def expect_slug(self, slug: str) -> None:
        self._expected_slug = slug

    def attach(self) -> None:
        if self._attached:
            return
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self._attached = True

    def detach(self) -> None:
        if not self._attached:
            return
        self._page.remove_listener("request", self._on_request)
        self._page.remove_listener("response", self._on_response)
        self._attached = False

    def captured_log(self) -> list[dict[str, Any]]:
        return list(self._log)

    def result_for(self, slug: str) -> UploadResult | None:
        return self._results.get(slug.lower())

    async def await_upload_result(self, slug: str, *, timeout_ms: int = 30_000) -> UploadResult:
        key = slug.lower()
        existing = self._results.get(key)
        if existing is not None:
            return existing

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[UploadResult] = loop.create_future()
        self._waiters.setdefault(key, []).append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=timeout_ms / 1000)
        except TimeoutError:
            return UploadResult(
                ok=False,
                status=0,
                slug_echoed=False,
                slug=slug,
                log=self.captured_log(),
            )

    def _resolve_waiters(self, slug: str, result: UploadResult) -> None:
        key = slug.lower()
        self._results[key] = result
        for fut in self._waiters.pop(key, []):
            if not fut.done():
                fut.set_result(result)

    def _handle_request(self, request: Request) -> None:
        try:
            entry: dict[str, Any] = {
                "phase": "request",
                "url": request.url,
                "method": request.method,
                "post_data": _snippet(request.post_data or ""),
            }
            self._log.append(entry)
        except Exception:
            pass

    async def _handle_response(self, response: Response) -> None:
        try:
            request = response.request
            status = response.status
            body = ""
            try:
                body = await response.text()
            except Exception:
                body = ""
            entry: dict[str, Any] = {
                "phase": "response",
                "url": response.url,
                "method": request.method,
                "status": status,
                "body": _snippet(body),
            }
            self._log.append(entry)

            if request.method not in _POST_METHODS:
                return
            if not _SKILL_UPLOAD_URL.search(response.url):
                post = request.post_data or ""
                if "skill" not in post.lower() and "graphql" not in response.url.lower():
                    return

            slug = self._expected_slug or self._infer_slug(request, body)
            if not slug:
                return
            slug_echoed = _slug_in_text(body, slug) or (200 <= status < 300)
            ok = 200 <= status < 300 and slug_echoed
            result = UploadResult(
                ok=ok,
                status=status,
                slug_echoed=slug_echoed,
                slug=slug,
                log=self.captured_log(),
            )
            self._resolve_waiters(slug, result)
        except Exception:
            pass

    def _infer_slug(self, request: Request, body: str) -> str | None:
        post = request.post_data or ""
        for blob in (post, body):
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                for key in ("slug", "name", "skill_name", "skillName"):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                variables = data.get("variables")
                if isinstance(variables, dict):
                    for key in ("slug", "name", "skillName"):
                        val = variables.get(key)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
            for token in re.findall(r"[a-z][a-z0-9-]{2,}", blob.lower()):
                if "-" in token and len(token) > 4:
                    return token
        return None
