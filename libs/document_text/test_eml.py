"""Unit tests for document_text.eml."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from document_text.eml import parse_eml_file


def test_parse_eml_file_returns_subject_and_message_id(tmp_path: Path) -> None:
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Subject"] = "Probate petition update"
    msg["Message-ID"] = "<test-msg-1169@example.com>"
    msg["Date"] = "Mon, 1 Jun 2026 12:00:00 +0000"
    msg.set_content("Body text for extraction.")

    eml_path = tmp_path / "fixture.eml"
    eml_path.write_bytes(msg.as_bytes())

    result = parse_eml_file(eml_path)

    assert result.subject == "Probate petition update"
    assert result.message_id == "<test-msg-1169@example.com>"
    assert "Body text for extraction." in result.text
    assert result.sender == "sender@example.com"
