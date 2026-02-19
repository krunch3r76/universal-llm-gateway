#!/usr/bin/env python3
"""Measure model RAM and VRAM by spawning a temporary llama-server.

VRAM is measured in-container via pynvml per-process accounting.
When --hold is used, the caller (gpu.py) can also take a host-side
VRAM snapshot via Stargate federation for cross-validation.

With --hold: load model → measure VRAM → print READY → wait for stdin
             close → warmup → measure RAM → JSON.
Without --hold: load model → measure VRAM → warmup → measure RAM → JSON.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


def get_process_rss_mb(pid: int) -> int:
    """Return RSS (MB) for an external PID."""
    try:
        import psutil

        return int(psutil.Process(pid).memory_info().rss // (1024 * 1024))
    except ImportError:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) // 1024
            raise RuntimeError(f"VmRSS not found in /proc/{pid}/status")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to read /proc/{pid}/status: {e}") from e


def get_process_gpu_memory(pid: int, device_index: int = 0) -> int:
    """Return GPU memory used by a process in MB via pynvml per-process accounting."""
    try:
        import pynvml

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
        processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)

        for proc in processes:
            if proc.pid == pid:
                vram_mb = proc.usedGpuMemory // (1024 * 1024)
                pynvml.nvmlShutdown()
                return int(vram_mb)

        pynvml.nvmlShutdown()
        return 0

    except ImportError:
        raise ImportError("nvidia-ml-py3 not installed") from None
    except Exception as e:
        raise RuntimeError(f"pynvml failed: {e}") from e


def _select_visible_gpu_device(gpu_index: int, cuda_visible_devices: str | None) -> str:
    """Resolve --gpu-index to a concrete CUDA_VISIBLE_DEVICES value."""
    if not cuda_visible_devices:
        return str(gpu_index)

    tokens = [t.strip() for t in cuda_visible_devices.split(",") if t.strip()]
    if not tokens:
        return str(gpu_index)

    if gpu_index < 0 or gpu_index >= len(tokens):
        raise ValueError(
            f"--gpu-index {gpu_index} out of range for "
            f"CUDA_VISIBLE_DEVICES={cuda_visible_devices!r}"
        )
    return tokens[gpu_index]


# --- Server lifecycle ---


def find_binary() -> str:
    """Locate llama-server binary."""
    from inference_djinn.engines.gguf.native.binary import find_llama_server

    return find_llama_server()


def build_server_command(
    binary: str,
    model_path: str,
    socket_path: str,
    n_gpu_layers: int,
    n_ctx: int,
    n_batch: int,
    mmproj_path: str | None = None,
    *,
    use_mmap: bool = True,
    f16_kv: bool = True,
) -> list[str]:
    """Build llama-server CLI command for measurement."""
    cmd = [
        binary,
        "-m",
        model_path,
        "--host",
        socket_path,
        "-ngl",
        str(n_gpu_layers),
        "-c",
        str(n_ctx),
        "-b",
        str(n_batch),
        "-np",
        "1",
        "-cb",
        "--flash-attn",
        "on",
        "--mlock",
    ]
    if not use_mmap:
        cmd.append("--no-mmap")
    if not f16_kv:
        cmd.extend(["-ctk", "f32", "-ctv", "f32"])
    if mmproj_path:
        cmd.extend(["--mmproj", mmproj_path])
    return cmd


def wait_for_health(
    socket_path: str,
    timeout_sec: int = 60,
    proc: subprocess.Popen | None = None,
) -> None:
    """Poll /health endpoint until server is ready."""
    import httpx

    transport = httpx.HTTPTransport(uds=socket_path)
    client = httpx.Client(transport=transport, base_url="http://localhost", timeout=2.0)

    try:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if proc and proc.poll() is not None:
                stderr_out = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError(
                    f"Server died during startup (exit {proc.returncode}): "
                    f"{stderr_out[:500]}"
                )
            try:
                resp = client.get("/health")
                if resp.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            time.sleep(1.0)
    finally:
        client.close()

    raise TimeoutError(f"Server failed to become healthy within {timeout_sec}s")


def warmup_via_api(socket_path: str, n_ctx: int) -> None:
    """Send a short completion request to fault in pages and caches."""
    import httpx

    transport = httpx.HTTPTransport(uds=socket_path)
    client = httpx.Client(
        transport=transport, base_url="http://localhost", timeout=60.0
    )

    prompt_tokens = max(1, min(100, n_ctx // 100))
    prompt = "Explain the concept of " * prompt_tokens

    try:
        client.post(
            "/v1/completions",
            json={"prompt": prompt, "max_tokens": 50, "temperature": 0.0},
        )
    except Exception:
        print("Warning: warmup request failed", file=sys.stderr)
    finally:
        client.close()


def _drain_stderr(pipe: object, lines: list[str]) -> None:
    """Drain stderr pipe into a list in a background thread."""
    for line in pipe:
        lines.append(line)
    pipe.close()


def _parse_offloaded_layers(stderr_lines: list[str]) -> int | None:
    """Parse actual offloaded layer count from llama-server stderr."""
    for line in stderr_lines:
        m = re.search(r"offloaded\s+(\d+)/(\d+)\s+layers", line)
        if m:
            return int(m.group(1))
    return None


# --- Main measurement ---


def measure(
    model_path: str,
    n_gpu_layers: int,
    n_ctx: int,
    n_batch: int,
    gpu_index: int,
    mode: str,
    mmproj_path: str | None = None,
    hold: bool = False,
) -> dict[str, bool | int | str | None]:
    """Spawn server, measure RAM and VRAM, shutdown; return JSON-shaped dict.

    VRAM is measured after model load via pynvml per-process accounting.
    With hold=True: prints READY after load (caller can take host-side
    snapshot for cross-validation), blocks until stdin is closed.
    """
    result: dict[str, bool | int | str | None] = {
        "success": False,
        "ram_mb": None,
        "vram_mb": None,
        "offloaded_layers": None,
        "error": None,
    }

    socket_path = f"/tmp/measurement-{uuid.uuid4().hex[:8]}.sock"
    proc: subprocess.Popen | None = None

    try:
        binary = find_binary()
        cmd = build_server_command(
            binary,
            model_path,
            socket_path,
            n_gpu_layers,
            n_ctx,
            n_batch,
            mmproj_path,
        )

        env = os.environ.copy()
        if mode == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = ""
        else:
            selected = _select_visible_gpu_device(
                gpu_index=gpu_index,
                cuda_visible_devices=env.get("CUDA_VISIBLE_DEVICES"),
            )
            env["CUDA_VISIBLE_DEVICES"] = selected

        stderr_lines: list[str] = []
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(proc.stderr, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()

        wait_for_health(socket_path, timeout_sec=120, proc=proc)

        pid = proc.pid
        assert pid is not None

        # VRAM after model load (before warmup — captures layer offload cost)
        vram_after_load = get_process_gpu_memory(pid, gpu_index) if mode == "gpu" else 0

        if hold:
            print("READY", flush=True)
            sys.stdin.readline()

        warmup_via_api(socket_path, n_ctx)
        ram_mb = get_process_rss_mb(pid)

        offloaded = _parse_offloaded_layers(stderr_lines)
        if offloaded is not None and offloaded != n_gpu_layers and n_gpu_layers != -1:
            print(
                f"WARNING: requested {n_gpu_layers} GPU layers but server "
                f"offloaded {offloaded}",
                file=sys.stderr,
            )

        result["success"] = True
        result["ram_mb"] = ram_mb
        result["vram_mb"] = vram_after_load if mode == "gpu" else None
        result["offloaded_layers"] = offloaded

    except FileNotFoundError as e:
        result["error"] = f"Binary not found: {e}"
    except TimeoutError as e:
        result["error"] = f"Server startup timeout: {e}"
    except RuntimeError as e:
        result["error"] = f"Runtime error: {e}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"

    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        socket_file = Path(socket_path)
        if socket_file.exists():
            socket_file.unlink()

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure model resource usage via llama-server"
    )
    parser.add_argument("--model", required=True, help="Path to GGUF model")
    parser.add_argument(
        "--layers",
        type=int,
        default=-1,
        help="Number of GPU layers (-1 = all, 0 = CPU only)",
    )
    parser.add_argument("--ctx", type=int, default=4096, help="Context length")
    parser.add_argument("--batch", type=int, default=512, help="Batch size")
    parser.add_argument("--gpu-index", type=int, default=0, help="GPU device index")
    parser.add_argument("--mmproj", help="Path to mmproj/CLIP file (vision models)")
    parser.add_argument(
        "--mode",
        choices=["gpu", "cpu"],
        default="gpu",
        help="Measurement mode",
    )
    parser.add_argument(
        "--hold",
        action="store_true",
        help="Hold after model load for external VRAM measurement",
    )

    args = parser.parse_args()

    if not Path(args.model).exists():
        print(
            json.dumps(
                {
                    "success": False,
                    "ram_mb": None,
                    "vram_mb": None,
                    "error": f"Model not found: {args.model}",
                }
            )
        )
        return 1

    n_gpu_layers = 0 if args.mode == "cpu" else args.layers

    result = measure(
        model_path=args.model,
        n_gpu_layers=n_gpu_layers,
        n_ctx=args.ctx,
        n_batch=args.batch,
        gpu_index=args.gpu_index,
        mode=args.mode,
        mmproj_path=args.mmproj,
        hold=args.hold,
    )

    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
