"""Test item 9 — loader keys reach llama-server argv."""

from __future__ import annotations

from inference_djinn.engines.gguf.native.config import ServerConfig


def test_loader_f16_kv_and_ubatch_reach_argv() -> None:
    cfg = ServerConfig(
        model_path="/tmp/model.gguf",
        ctx_size=8192,
        n_gpu_layers=-1,
        cache_type_k="f16",
        cache_type_v="f16",
        ubatch_size=2048,
    )
    args = cfg.to_cli_args()
    assert "-ub" in args and "2048" in args

    f32_cfg = ServerConfig(
        model_path="/tmp/model.gguf",
        ctx_size=8192,
        cache_type_k="f32",
        cache_type_v="f32",
    )
    f32_args = f32_cfg.to_cli_args()
    assert "-ctk" in f32_args and "f32" in f32_args
    assert "-ctv" in f32_args and "f32" in f32_args


def test_cpu_profile_uses_zero_gpu_layers() -> None:
    cfg = ServerConfig(model_path="/tmp/model.gguf", ctx_size=8192, n_gpu_layers=0)
    args = cfg.to_cli_args()
    assert "-ngl" in args and "0" in args
