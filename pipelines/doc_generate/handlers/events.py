"""Event factories for the doc-generate pipeline."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def doc_generate_extract_success(
    *,
    execution_id: str,
    step_id: str,
    subsystem_path: str,
    file_count: int,
    class_count: int,
    function_count: int,
) -> Event:
    return Event(
        signal="doc.generate.extract.success",
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "subsystem_path": subsystem_path,
            "file_count": file_count,
            "class_count": class_count,
            "function_count": function_count,
        },
    )


@event_factory
def doc_generate_extract_failed(
    *,
    execution_id: str,
    step_id: str,
    subsystem_path: str | None,
    reason: str,
    error: str,
) -> Event:
    payload: dict[str, str] = {
        "execution_id": execution_id,
        "step_id": step_id,
        "reason": reason,
        "error": error,
    }
    if subsystem_path is not None:
        payload["subsystem_path"] = subsystem_path
    return Event(signal="doc.generate.extract.failed", payload=payload)


@event_factory
def doc_generate_architecture_found(
    *,
    execution_id: str,
    step_id: str,
    architecture_doc_path: str,
) -> Event:
    return Event(
        signal="doc.generate.architecture.found",
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "architecture_doc_path": architecture_doc_path,
        },
    )


@event_factory
def doc_generate_architecture_notfound(
    *,
    execution_id: str,
    step_id: str,
    architecture_doc_path: str,
) -> Event:
    return Event(
        signal="doc.generate.architecture.notfound",
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "architecture_doc_path": architecture_doc_path,
        },
    )


@event_factory
def doc_generate_python_empty(
    *,
    execution_id: str,
    step_id: str,
    subsystem_path: str,
) -> Event:
    return Event(
        signal="doc.generate.python.empty",
        payload={
            "execution_id": execution_id,
            "step_id": step_id,
            "subsystem_path": subsystem_path,
        },
    )
