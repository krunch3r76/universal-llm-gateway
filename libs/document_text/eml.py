"""MIME .eml parsing — headers, body, PDF attachments → markdown text."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EmlParseResult:
    """Structured parse output for pipeline + bridge callers."""

    text: str
    message_id: str
    email_date: str
    subject: str
    sender: str


def _extract_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                return str(part.get_content()).strip()
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/html" and "attachment" not in disposition:
                import html2text

                return html2text.html2text(part.get_content()).strip()
        return ""
    return str(msg.get_content()).strip()


def _attachment_sections(msg: Message) -> list[str]:
    sections: list[str] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        sections.append("")
        sections.append("---")
        sections.append(f"## Attachment: {filename}")
        sections.append("")
        content_type = part.get_content_type()
        if content_type == "application/pdf":
            import pymupdf  # type: ignore[import-untyped]
            import pymupdf4llm  # type: ignore[import-untyped]

            data = part.get_payload(decode=True)
            doc = None
            try:
                doc = pymupdf.open(stream=data, filetype="pdf")
                text = pymupdf4llm.to_markdown(doc)
            finally:
                if doc is not None:
                    doc.close()
            sections.append(text.strip())
        else:
            sections.append(f"[{content_type} — not extracted]")
    return sections


def render_eml_markdown(msg: Message) -> str:
    """Render an parsed message to the legacy markdown shape (MCP fs read)."""
    lines: list[str] = ["---"]
    for header in ("from", "to", "cc", "subject", "date", "message-id"):
        value = msg.get(header, "")
        if value:
            lines.append(f"{header}: {value}")
    lines.extend(["---", ""])
    lines.append(_extract_body(msg))
    lines.extend(_attachment_sections(msg))
    return "\n".join(lines)


def parse_eml_file(path: Path) -> EmlParseResult:
    """Parse a .eml file from disk."""
    with path.open("rb") as handle:
        msg = BytesParser(policy=policy.default).parse(handle)
    text = render_eml_markdown(msg)
    return EmlParseResult(
        text=text,
        message_id=str(msg.get("message-id", "") or ""),
        email_date=str(msg.get("date", "") or ""),
        subject=str(msg.get("subject", "") or ""),
        sender=str(msg.get("from", "") or ""),
    )
