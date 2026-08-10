"""Build the ``fs`` MCP tool description from PERMISSIONS-derived blurbs."""

from __future__ import annotations

from endpoint_surface import Surface
from fs_roots import derive_fs_sandbox_intro
from tools.filesystem._fs_dispatch import md_section_op_doc, sandbox_op_doc


def build_fs_tool_description(surface: Surface) -> str:
    sandbox_intro, find_blurb, search_blurb = derive_fs_sandbox_intro(surface)
    return (
        f"{sandbox_intro}"
        "`read` is unified across sandboxes: source files plus text-oriented\n"
        "document formats such as PDF, DOCX, ODT, EML, and HTML can be read in\n"
        "text mode from `cortex` or `workspaces`. Optional `offset` (0-based lines\n"
        "to skip) and `limit` (max lines) return a bounded line window on decoded\n"
        "text — response adds `line_range`, `total_lines`, and `truncated` when\n"
        "either param is non-zero; binary reads ignore the range and set\n"
        "`line_range_applied: false`. Image files, archives, and other\n"
        "binary formats auto-route to base64 even without `binary=True` — reading a\n"
        "`.png`, `.jpg`, or archive returns {content_base64, auto_binary: true}\n"
        "rather than corrupted text. Pass `binary=True` explicitly when you need base64\n"
        "for an arbitrary file type or to make the intent clear. Use `write_binary`\n"
        "(cortex sandbox only) to stage base64-encoded binary files (PDFs, images)\n"
        "— pass the base64 string as `content`. Use `move` to rename or relocate\n"
        "a file within the selected sandbox. Prefer the markdown ops for large\n"
        "structured docs when you need sections/TOC; for PDFs, ``md_list`` / ``md_read``\n"
        "/ ``md_to_dict`` use the embedded outline (TOC) with coordinate-clipped\n"
        "page regions — not ATX markdown from ``pymupdf4llm``.\n\n"
        "**PDF extraction**: Default uses pymupdf4llm (prose-oriented markdown).\n"
        "For tabular or columnar PDFs (statements, invoices, ledger exports),\n"
        'prefer ``finance(op="inspect", path=...)`` which uses pdfplumber and\n'
        'preserves table structure, or ``dispatch(tool="extract_document", ...)``\n'
        "for scanned documents needing OCR sidecars. PDF reads include an\n"
        "``extraction`` field with method info and alternative suggestions.\n\n"
        "Responses dual-carry `path` (sandbox-relative) and `uri` (canonical Share URI).\n"
        "Host mount paths are accepted at ingress and normalized with an advisory;\n"
        "egress never returns absolute mount paths.\n\n"
        'Use op="list" for directories; op="read" on a directory path returns an error.\n\n'
        f"{find_blurb}"
        f"{search_blurb}"
        "Write responses (``write``, ``replace``, ``append``, ``prepend``,\n"
        "``insert_at_line``, ``write_binary``) include ``written_sha256``: bare\n"
        "lowercase hex of the resulting file bytes (``write_binary`` hashes the\n"
        "decoded bytes written). Callers compose ``sha256:`` / ``spec_sha256:``\n"
        "prefixes when citing hashes on assertions — the field itself has no prefix.\n"
        "Read-only ops do not return ``written_sha256``. Read responses (``read``)\n"
        "include ``read_sha256``: bare lowercase hex of the on-disk source file\n"
        "bytes, computed before text decode, format conversion, or offset/limit\n"
        "windowing. When ``offset``/``limit`` slice the returned ``content``,\n"
        "``read_sha256`` still covers the full source file. For\n"
        "``cortex://notes/system/operational/what-is-running.json`` reads that\n"
        "apply ``serve_view``, also expect ``served_sha256`` (hash of returned\n"
        "UTF-8 ``content``), ``serve_view_applied: true``, and ``snapshot_uri``;\n"
        "quote ``served_sha256`` for observation provenance, not ``read_sha256``.\n"
        "``binary=true`` on that URI returns the served JSON as text ``content``,\n"
        "not base64 of the unserved on-disk file. Callers compose\n"
        "``sha256:`` / ``spec_sha256:`` prefixes when citing — the field itself\n"
        "has no prefix.\n\n"
        f"{sandbox_op_doc()}\n\n"
        f"{md_section_op_doc()}"
    )
