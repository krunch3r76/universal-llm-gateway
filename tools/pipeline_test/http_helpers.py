"""Shared HTTP helpers for pipeline test tools."""

PIPELINE_TEST_HEADERS: dict[str, str] = {
    "X-Pipeline-Internal": "true",
    "X-Pipeline-Execution-Id": "pipeline-test-tooling",
    "X-Pipeline-Step-Id": "tooling",
    "X-Skip-Token-Counting": "true",
}

PIPELINE_TEST_PARAMS: dict[str, str] = {}
