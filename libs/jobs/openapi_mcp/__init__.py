"""OpenAPI stamp checks for the jobs satellite dispatch ops.

The codegen script calls ``unbound_dispatch_ops``. There is no generated
adapter module: the served document is the binding source.
"""

from jobs.openapi_mcp._ops import JOBS_DISPATCH_OPS
from jobs.openapi_mcp._route_map import unbound_dispatch_ops

__all__ = ["JOBS_DISPATCH_OPS", "unbound_dispatch_ops"]
