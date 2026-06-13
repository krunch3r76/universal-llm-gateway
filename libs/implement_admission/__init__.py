"""Unified implement admission — source_ref normalizer and packet materializer."""

from implement_admission.admission_read import PacketRead, read_packet
from implement_admission.closeout import (
    ADAPTERS,
    ImplementCloseout,
    run_adapters,
)
from implement_admission.dense_spec_schema import (
    DenseSpecVerdict,
    dense_spec_hash_uri,
    dense_spec_sha256,
    validate_dense_spec,
)
from implement_admission.implement_ready import (
    ImplementReadyVerdict,
    evaluate_implement_ready,
)
from implement_admission.materialize import MaterializedPacket, materialize
from implement_admission.normalize import normalize
from implement_admission.preflight import (
    DecisionNotAssertedError,
    require_decision_asserted,
)
from implement_admission.routing import derive_routing
from implement_admission.source_ref import SourceRef, SourceRefError, parse_source_ref
from implement_admission.spec import ImplementSpec, implement_spec_hash

__all__ = [
    "ADAPTERS",
    "DecisionNotAssertedError",
    "DenseSpecVerdict",
    "ImplementCloseout",
    "ImplementReadyVerdict",
    "ImplementSpec",
    "MaterializedPacket",
    "PacketRead",
    "SourceRef",
    "SourceRefError",
    "dense_spec_hash_uri",
    "dense_spec_sha256",
    "derive_routing",
    "evaluate_implement_ready",
    "implement_spec_hash",
    "materialize",
    "normalize",
    "parse_source_ref",
    "read_packet",
    "require_decision_asserted",
    "run_adapters",
    "validate_dense_spec",
]
