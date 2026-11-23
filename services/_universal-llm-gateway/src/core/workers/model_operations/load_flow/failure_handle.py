"""Exception handling during model load with classified errors and cleanup."""

from typing import TYPE_CHECKING

from ....errors import GatewayError
from .cleanup import cleanup_failed_worker
from .deps import get_resource_tracker, logger
from .events import emit_loading_event
from .failure_classify import classify_load_failure

if TYPE_CHECKING:
    from ...controller import WorkerController


def load_failure_process_text(exc: Exception) -> str:
    """Choose forensic process text for a failed load.

    Prefer ``GatewayError.details["internal_error"]`` when it is a non-empty
    string, otherwise ``GatewayError.message``. Other exceptions use
    ``str(exc)``. Context, stack traces, and other detail keys are not
    forensic fields.
    """
    if isinstance(exc, GatewayError):
        internal = exc.details.get("internal_error")
        if isinstance(internal, str) and internal.strip():
            return internal
        if isinstance(exc.message, str) and exc.message:
            return exc.message
    return str(exc)


async def handle_load_exception(
    controller: "WorkerController", model_id: str, e: Exception
):
    """Handle exception during model loading with error classification."""
    process_text = load_failure_process_text(e)
    error_msg, failure_reason = classify_load_failure(process_text)
    if failure_reason == "oom":
        logger.error(f"❌ OOM error loading {model_id}: {e}")
    elif failure_reason == "insufficient_resources":
        logger.error(f"❌ Resource error loading {model_id}: {e}")
    elif failure_reason == "timeout":
        logger.error(f"❌ Timeout loading {model_id}: {e}")
    elif failure_reason == "missing_file":
        logger.error(f"❌ File not found loading {model_id}: {e}")
    elif failure_reason == "config_error":
        logger.error(f"❌ Configuration error loading {model_id}: {e}")
    else:
        logger.error(f"❌ Error loading {model_id}: {e}")

    get_resource_tracker().set_model_error(model_id, error_msg)

    await emit_loading_event(controller, model_id, "failed", process_text)
    await cleanup_failed_worker(controller, model_id, "Load exception")
