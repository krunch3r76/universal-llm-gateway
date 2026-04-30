"""SSE streaming and job status helpers for measurement commands."""

import sys

import requests
from sse import parse_sse_message


def stream_job_logs(  # noqa: PLR0912
    stargate_url: str,
    job_id: str,
    headers: dict[str, str],
    verbose: bool = False,
) -> bool:
    """Stream job logs via Stargate federation."""
    # Use Stargate /gateway/* endpoint
    full_url = f"{stargate_url}/gateway/jobs/{job_id}/logs"

    if verbose:
        print(f"[DEBUG] Connecting to: {full_url}", file=sys.stderr)

    try:
        with requests.get(
            full_url, headers=headers, stream=True, timeout=1800
        ) as stream:
            # Check if stream started successfully
            if stream.status_code != 200:
                print(
                    f"❌ Log stream error: HTTP {stream.status_code}", file=sys.stderr
                )
                try:
                    error_body = stream.text
                    print(f"   {error_body}", file=sys.stderr)
                except Exception:
                    pass
                return True

            buffer = ""
            chunk_count = 0
            # Use small chunk_size to reduce buffering latency for SSE
            for chunk in stream.iter_content(chunk_size=64, decode_unicode=True):
                if chunk is None:
                    continue
                chunk_count += 1
                if verbose:
                    print(
                        f"[DEBUG] Chunk {chunk_count}: {len(chunk)} bytes",
                        file=sys.stderr,
                    )
                buffer += chunk

                while "\n\n" in buffer:
                    message, buffer = buffer.split("\n\n", 1)
                    if not message.strip() or message.startswith(":"):
                        continue  # Skip empty/comment (keepalive)

                    try:
                        sse = parse_sse_message(message)

                        if sse.event == "log":
                            print(sse.data)
                        elif sse.event == "complete":
                            return True
                        elif sse.event == "error":
                            error_msg = (
                                sse.data.get("error", sse.data)
                                if isinstance(sse.data, dict)
                                else sse.data
                            )
                            print(f"❌ {error_msg}", file=sys.stderr)
                            return True
                        elif sse.event == "keepalive":
                            continue  # Ignore keepalive
                        elif sse.event is None:
                            # No event type - print data as log
                            print(sse.data)
                    except ValueError:
                        # Malformed SSE - skip
                        pass
        return True
    except KeyboardInterrupt:
        print("\n⚠️  Cancelling job...")
        try:
            requests.delete(
                f"{stargate_url}/gateway/jobs/{job_id}",
                headers=headers,
                timeout=5,
            )
            print("Job cancelled.")
        except Exception:
            pass
        return False
    except requests.RequestException as e:
        print(f"⚠️  Log streaming interrupted: {e}", file=sys.stderr)
        return True


def check_job_status(
    stargate_url: str, job_id: str, headers: dict[str, str], request_timeout: float
) -> int:
    """Check final job status via Stargate. Returns exit code."""
    try:
        response = requests.get(
            f"{stargate_url}/gateway/jobs/{job_id}",
            headers=headers,
            timeout=max(request_timeout, 5),
        )
        final_status = response.json()

        if final_status.get("status") == "completed":
            print("\n✅ Measurement complete")
            return 0

        error = final_status.get("error", "unknown error")
        print(f"\n❌ Measurement {final_status.get('status', 'failed')}: {error}")
        return 1
    except Exception as e:
        print(f"\n⚠️  Could not verify final status: {e}", file=sys.stderr)
        return 1
