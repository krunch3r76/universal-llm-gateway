"""Email surface routing — fail-closed guards for IMAP bridge vs M365 Graph."""

from email_routing.surface_guard import (
    apply_indeterminate_if_degraded,
    check_mailbox_surface,
    wrong_surface_response,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker',)

__all__ = [
    "apply_indeterminate_if_degraded",
    "check_mailbox_surface",
    "wrong_surface_response",
]
