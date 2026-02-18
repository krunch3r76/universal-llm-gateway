#!/usr/bin/env python3
"""
llama-server-based model measurement.

Replaces simple_gpu_layer_test.py and simple_cpu_only_memory_test.py.
Spawns a temporary llama-server instance, measures RAM/VRAM, and reports
results as JSON on stdout.

Usage:
    # GPU measurement
    python llama_server_measurement.py --model /path/to/model.gguf --layers 32 --ctx 4096

    # CPU measurement
    python llama_server_measurement.py --model /path/to/model.gguf --mode cpu --ctx 4096
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

# --- PID-based memory measurement ---


def get_process_rss_mb(pid: int) -> int:
    """
    Return RSS (Resident Set Size) of an external process in MB.

    Args:
        pid: Process ID to measure

    Raises:
        RuntimeError: If measurement fails
    """
    try:
        import psutil

        process = psutil.Process(pid)
        return int(process.memory_info().rss // (1024 * 1024))
    except ImportError:
        # Fallback: /proc/{pid}/status (Linux only)
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
    """
    Return GPU memory used by an external process in MB.

    Args:
        pid: Process ID to measure
        device_index: GPU device index

    Raises:
        RuntimeError: If measurement fails
    """
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
        return 0  # Process not using GPU yet

    except ImportError:
        raise ImportError("nvidia-ml-py3 not installed") from None
    except Exception as e:
        raise RuntimeError(f"pynvml failed: {e}") from e


# --- Server lifecycle ---


def find_binary() -> str:
    """Locate llama-server binary (import from shared module)."""
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
        "-np",
        "1",
        "--flash-attn",
        "on",
        "--mlock",
    ]
    if mmproj_path:
        cmd.extend(["--mmproj", mmproj_path])
    return cmd


def wait_for_health(
    socket_path: str,
    timeout_sec: int = 60,
    proc: subprocess.Popen | None = None,
) -> None:
    """
    Poll /health endpoint until server is ready.

    Args:
        socket_path: Unix socket path
        timeout_sec: Maximum wait time
        proc: Server process (checked for early exit)

    Raises:
        TimeoutError: If server doesn't become healthy
        RuntimeError: If server process dies during wait
    """
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
    """
    Exercise KV cache allocation via completion API.

    Sends a short prompt to force KV cache allocation and one
    generation pass. This captures peak memory after cache fill.
    """
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
            json={
                "prompt": prompt,
                "max_tokens": 50,
                "temperature": 0.0,
            },
        )
    except Exception:
        print("Warning: warmup request failed", file=sys.stderr)
    finally:
        client.close()


# --- Main measurement ---


def measure(
    model_path: str,
    n_gpu_layers: int,
    n_ctx: int,
    n_batch: int,
    gpu_index: int,
    mode: str,
    mmproj_path: str | None = None,
) -> dict[str, bool | int | str | None]:
    """
    Run measurement: spawn server, measure, shutdown.

    Returns:
        {"success": bool, "ram_mb": int|None, "vram_mb": int|None, "error": str|None}
    """
    result: dict[str, bool | int | str | None] = {
        "success": False,
        "ram_mb": None,
        "vram_mb": None,
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
            env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)

        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        wait_for_health(socket_path, timeout_sec=120, proc=proc)
        pid = proc.pid
        assert pid is not None

        ram_after_load = get_process_rss_mb(pid)
        vram_after_load = get_process_gpu_memory(pid, gpu_index) if mode == "gpu" else 0

        warmup_via_api(socket_path, n_ctx)
        ram_after_warmup = get_process_rss_mb(pid)
        vram_after_warmup = (
            get_process_gpu_memory(pid, gpu_index) if mode == "gpu" else 0
        )

        result["success"] = True
        result["ram_mb"] = max(ram_after_load, ram_after_warmup)
        result["vram_mb"] = (
            max(vram_after_load, vram_after_warmup) if mode == "gpu" else None
        )

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
    )

    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
