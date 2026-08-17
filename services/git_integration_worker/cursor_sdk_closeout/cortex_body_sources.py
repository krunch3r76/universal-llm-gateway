"""Single package binding for ``cortex_files_root`` and markdown extraction from cortex:// URIs.

This module is the *only* import site of
``implement_admission.closeout_helpers.cortex_files_root``. Every in-package
caller must use module-attribute access (``cortex_body_sources.cortex_files_root()``)
so tests can patch one target and reach assembly, implement-body, OOB findings,
and ``_markdown_from_cortex_uris``. Do not re-export ``cortex_files_root`` from
package ``__init__.py``.
"""

from __future__ import annotations

from implement_admission.closeout_helpers import cortex_files_root

from services.git_integration_worker.cursor_auto.closeout_relay_cortex_uri import (
    read_cortex_text,
)


def _markdown_from_cortex_uris(uris: list[str]) -> list[str]:
    root = cortex_files_root()
    bodies: list[str] = []
    for uri in uris:
        if not uri.startswith("cortex://"):
            continue
        text = read_cortex_text(uri, cortex_root=root)
        if text:
            bodies.append(text)
    return bodies
