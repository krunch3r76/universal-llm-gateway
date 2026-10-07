"""Jobs satellite library: bounded JobSpecs, journal, runner, and HTTP app.

Manage starts ``jobs.server:app`` on the jobs UDS socket. Pipeline steps
reach it through ``http_v1`` and never import this package's runner. The
journal under ``JOBS_STATE_DIR`` is the run authority.
"""

from jobs.server import create_app
from jobs.spec import load_registry

__all__ = ["create_app", "load_registry"]
