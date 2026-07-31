from systems.proxy.core.nonstreaming.executor.federated_execution import (
    _is_request_deadline_timeout,
)


def test_request_deadline_timeout_is_not_gateway_health_timeout() -> None:
    assert _is_request_deadline_timeout(
        {
            "code": "REQUEST_TIMEOUT",
            "data": {
                "gateway_id": "edge-jupiter-gateway",
                "timeout_kind": "request_deadline",
                "deadline_s": 30.0,
            },
        }
    )


def test_forward_read_timeout_remains_gateway_health_timeout() -> None:
    assert not _is_request_deadline_timeout(
        {
            "code": "REQUEST_TIMEOUT",
            "data": {
                "gateway_id": "edge-jupiter-gateway",
                "timeout_kind": "forward_read_timeout",
            },
        }
    )
