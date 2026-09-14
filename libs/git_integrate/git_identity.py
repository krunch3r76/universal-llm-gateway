"""Git author/committer identity for integration-worker subprocess commits."""

from __future__ import annotations

DISPATCH_GIT_EMAIL_DOMAIN = "dispatch.git-integration-worker"


def integrate_git_env_vars(label: str, *, seat: str = "giw") -> dict[str, str]:
    """Env overrides for arc/sweeper commits (distinct from cursor-sdk dispatch HOME)."""
    name = f"{seat}/{label}"
    email = f"{label}@{DISPATCH_GIT_EMAIL_DOMAIN}"
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
