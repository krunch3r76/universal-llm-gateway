from systems.proxy.core.nonstreaming.preparer import _request_timeout_cap


def test_external_request_timeout_cap_remains_public_ceiling() -> None:
    assert _request_timeout_cap(is_pipeline_internal=False) == 300.0


def test_pipeline_internal_request_timeout_cap_allows_batch_budget() -> None:
    assert _request_timeout_cap(is_pipeline_internal=True) == 900.0
