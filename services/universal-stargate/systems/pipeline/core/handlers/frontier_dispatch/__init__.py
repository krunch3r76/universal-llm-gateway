"""Built-in ``frontier_dispatch_v1`` step handler — native-endpoint frontier dispatch.

Routes pipeline dispatch calls to Stargate's provider-native endpoints
via the in-process ``CloudProxyClient`` forwarder. Uses
``libs/agent_seat/native_loop`` for the bounded tool loop.

Package-private submodules (consolidated from former handler-level siblings):

- ``admission_checks`` — unknown-option rejection, remote-MCP resolution,
  agent/model consistency checks, context injection.
- ``request`` — model/agent/prompt resolution, reasoning-effort translation.
- ``tools`` — default tool tiers, xAI built-ins, 3-way tool-set resolution
  (endpoint-supplied / persona-bound / persona-free).
- ``streaming`` — cancel check, native sender, tool-event dispatcher.
- ``observability`` — post-loop anomaly detection.

This package is the package-shadow of the former ``frontier_dispatch.py`` module
(unit 14 of the pipeline modularize overhaul). ``FrontierDispatchHandler`` is the
sole public surface; importing this package triggers ``@register_handler``
registration with the DomainRouter as a side effect of importing ``handler``.

Internal layout (all submodules are package-private):

- ``handler`` — FrontierDispatchHandler class (constants + thin execute/validate)
- ``admission_gate`` — ordered admission, tool-set resolution, RemoteMcpEnabled
- ``gen_params`` — generation parameters + FrontierRequest construction
- ``native_loop`` — streaming closures, native tool loop, terminal events
- ``completion`` — post-loop observability + StepOutput assembly

YAML shape::

    steps:
      - name: respond
        type: frontier_dispatch_v1

Caller::

    # Agent surface (MCP — preferred):
    team_dispatch(op="generate", role="gatherer", model="openai/gpt-5.4",
                  dispatch_thread_id="cursor-2026-06-02-example",
                  messages=[{"role": "user", "content": "..."}])

    # Internal HTTP (pipeline composition / Stargate callers):
    POST /api/v1/frontier/dispatch  # persona-free
    POST /api/v1/team/dispatch      # role-envelope

    # Raw escape hatch (advanced — bypasses canonical admission):
    pipeline(op="async", pipeline_id="frontier-dispatch",
             pipeline_options={"model": "openai/gpt-5.4", "role": "gatherer"},
             messages=[{"role": "user", "content": "..."}])
"""

from .handler import FrontierDispatchHandler

__all__ = ["FrontierDispatchHandler"]
