"""Materialize + dispatch admitted windows (Phase 3 — materializers absorbed)."""

from __future__ import annotations

from .dispatch import (
    admit_consult_window,
    admit_worker_window,
    count_admissions,
    latest_checkpoint,
    parse_tip_checkpoint,
)
from .materializer import (
    _work_summary,
    handoff_subject,
    materialize_resume_packet,
)
from .materializer_autonomous import (
    autonomous_subject,
    materialize_autonomous_packet,
    select_packet,
)
from .materializer_autonomous_arc import autonomous_arc_guidance
from .materializer_closed_detent import materialize_closed_detent_packet
from .materializer_consult import consult_subject, materialize_consult_packet
from .materializer_layer import layer_subject, materialize_layer_packet
from .materializer_operator_proxy import (
    materialize_operator_proxy_packet,
    operator_proxy_subject,
)

__all__ = [
    "admit_consult_window",
    "admit_worker_window",
    "autonomous_arc_guidance",
    "autonomous_subject",
    "consult_subject",
    "count_admissions",
    "_work_summary",
    "handoff_subject",
    "layer_subject",
    "latest_checkpoint",
    "materialize_autonomous_packet",
    "materialize_closed_detent_packet",
    "materialize_consult_packet",
    "materialize_layer_packet",
    "materialize_operator_proxy_packet",
    "materialize_resume_packet",
    "operator_proxy_subject",
    "parse_tip_checkpoint",
    "select_packet",
]
