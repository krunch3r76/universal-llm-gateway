"""Headless M365 email export — intent parse, Graph fetch, sink write, receipts."""

from email_export.imprint import (
    ImprintResult,
    correspondence_id_for_message,
    imprint_eml,
)
from email_export.intent import EmailSyncIntent, load_intent
from email_export.receipt import ExportReceipt, ReceiptEntry, ReceiptStatus

__all__ = [
    "EmailSyncIntent",
    "ExportReceipt",
    "ImprintResult",
    "ReceiptEntry",
    "ReceiptStatus",
    "correspondence_id_for_message",
    "imprint_eml",
    "load_intent",
]
