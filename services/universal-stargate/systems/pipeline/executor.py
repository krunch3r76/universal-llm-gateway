"""
Pipeline executor - exports from core.

Thin compatibility shim re-exporting ``PipelineExecutor`` from
``core.executor`` so callers can import ``systems.pipeline.executor``; for
example ``pipeline_registry_bootstrap`` in the proxy component factory builds
the executor through this path. Contains no logic of its own.
"""

from .core.executor import PipelineExecutor

__all__ = ["PipelineExecutor"]
