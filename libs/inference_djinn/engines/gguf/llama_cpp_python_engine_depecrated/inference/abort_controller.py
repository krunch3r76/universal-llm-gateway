"""
Abort callback controller for llama-cpp cancellation support.

Provides AbortController class for managing llama-cpp's abort callback lifecycle,
enabling true C-level cancellation of token generation.

Thread-safe implementation with per-context locking to prevent race conditions
when callbacks are armed/disarmed during concurrent inference operations.
"""

import ctypes
import threading

import llama_cpp

__all__ = ["AbortController"]

# Module-level registry to prevent garbage collection of active callbacks
# and serialize access per context
_active_controllers: dict[int, "AbortController"] = {}
_context_locks: dict[int, threading.Lock] = {}
_global_lock = threading.Lock()


def _get_context_lock(ctx_ptr: int) -> threading.Lock:
    """Get or create a lock for the given llama context pointer.

    Args:
        ctx_ptr: Integer pointer to llama context

    Returns:
        Lock for serializing operations on this context
    """
    with _global_lock:
        if ctx_ptr not in _context_locks:
            _context_locks[ctx_ptr] = threading.Lock()
        return _context_locks[ctx_ptr]


class AbortController:
    """Manages llama-cpp abort callback for cancellable inference.

    This class handles the lifecycle of llama-cpp's abort callback:
    - arm(): Sets up callback before generation
    - trigger(): Signals abort (generation stops at next token boundary)
    - disarm(): Clears callback after generation

    Thread-safe: Uses per-context locks to serialize arm/disarm operations
    and prevent race conditions where callbacks are freed while still in use.

    Invariants (FOL):
        ∀ model_ctx: ¬∃ concurrent_abort_ops(model_ctx)
        ∀ callback_ref: in_use(callback_ref) ⟹ ¬gc(callback_ref)
    """

    def __init__(self, llama_model):
        self._llama_model = llama_model
        self._abort_flag = False
        self._armed = False
        # MUST keep reference to prevent garbage collection
        self._callback = llama_cpp.ggml_abort_callback(self._check_abort)

    def _check_abort(self, data) -> bool:
        """Called by llama.cpp before each computation. Return True to abort."""
        return self._abort_flag

    def _get_ctx_ptr(self) -> int:
        """Get integer pointer to llama context for use as dict key.

        Returns:
            Integer pointer value (stable for lifetime of Llama object)
        """
        ctx = self._llama_model.ctx

        # Handle different ctx types from llama-cpp-python
        if isinstance(ctx, int):
            # ctx is already an integer
            return ctx
        elif hasattr(ctx, "contents"):
            # ctx is a POINTER - get address of the pointed-to structure
            return ctypes.addressof(ctx.contents)
        elif hasattr(ctx, "value"):
            # ctx is c_void_p or similar - use its value
            return ctx.value if ctx.value is not None else id(ctx)
        else:
            # Fallback: use Python object id (stable for object lifetime)
            return id(ctx)

    def arm(self) -> None:
        """Set up abort callback before generation.

        Thread-safe: acquires per-context lock and waits for previous
        controller to complete before arming.
        """
        ctx_ptr = self._get_ctx_ptr()
        lock = _get_context_lock(ctx_ptr)

        with lock:
            # Wait for and clean up any previous controller
            if ctx_ptr in _active_controllers:
                prev = _active_controllers[ctx_ptr]
                if prev is not self:
                    # Previous controller should have disarmed, but force cleanup
                    prev._force_disarm_unsafe()

            self._abort_flag = False
            llama_cpp.llama_set_abort_callback(
                self._llama_model.ctx, self._callback, None
            )
            _active_controllers[ctx_ptr] = self
            self._armed = True

    def trigger(self) -> None:
        """Signal abort - generation stops at next token boundary.

        Note: After calling trigger(), the caller should wait for
        the generate_stream() to complete before starting new inference.
        The lock in arm() provides serialization.
        """
        self._abort_flag = True

    def disarm(self) -> None:
        """Clear abort callback after generation.

        Thread-safe: acquires per-context lock before clearing callback.
        Ensures callback is not cleared while still in use.
        """
        if not getattr(self, "_armed", False):
            return  # Not armed, nothing to do

        ctx_ptr = self._get_ctx_ptr()
        lock = _get_context_lock(ctx_ptr)

        with lock:
            self._disarm_unsafe()

    def _disarm_unsafe(self) -> None:
        """Internal disarm without lock - caller must hold lock."""
        if not self._armed:
            return

        ctx_ptr = self._get_ctx_ptr()

        # Clear callback with NULL
        llama_cpp.llama_set_abort_callback(
            self._llama_model.ctx,
            ctypes.cast(None, llama_cpp.ggml_abort_callback),
            None,
        )

        # Deregister to allow GC (but only if we're the registered one)
        if _active_controllers.get(ctx_ptr) is self:
            del _active_controllers[ctx_ptr]

        self._armed = False

    def _force_disarm_unsafe(self) -> None:
        """Force disarm from external caller - for cleanup during arm()."""
        self._disarm_unsafe()
