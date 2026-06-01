#!/usr/bin/env python3
"""
Live Microphone Transcription for Whisper Streaming
Based on sobatkon approach - simple real-time transcription
"""

import argparse
import asyncio
import json
import os
import signal
import stat
import sys
from queue import Queue

import websockets
from websockets.legacy.client import WebSocketClientProtocol

# Local presets for --direct mode (when bypassing Stargate)
# NOTE: Keep in sync with Stargate VAD_PROFILES in:
#       services/universal-stargate/proxy/routers/v1/audio.py
LOCAL_PRESETS = {
    "sensitive": {
        "vad_method": "silero",
        "silero_threshold": 0.3,
        "silero_min_silence_ms": 300,
        "speech_pad_ms": 150,
    },
    "balanced": {
        "vad_method": "silero",
        "silero_threshold": 0.5,
        "silero_min_silence_ms": 500,
        "speech_pad_ms": 100,
    },
    "aggressive": {
        "vad_method": "silero",
        "silero_threshold": 0.7,
        "silero_min_silence_ms": 800,
        "speech_pad_ms": 50,
    },
}


class MicrophoneTranscriber:
    """Live microphone transcription client for Whisper streaming."""

    def __init__(
        self,
        stargate_url: str = "ws://io:9999",
        model: str = "whisper-large-v3",
        args: argparse.Namespace | None = None,
        unix_socket_path: str | None = None,
    ):
        self.stargate_url: str = stargate_url
        self.model: str = model
        self.args: argparse.Namespace | None = args
        self.unix_socket_path: str | None = unix_socket_path
        self.websocket: WebSocketClientProtocol | None = None
        self.audio_stream = None  # pyaudio.Stream, set in setup_audio()
        self.pyaudio_instance = None  # pyaudio.PyAudio, set in setup_audio()
        self.is_streaming: bool = False
        self._local_vad_warned: bool = False

        # Audio configuration - matching Whisper requirements
        self.target_sample_rate: int = 16000  # Required by Whisper
        self.device_sample_rate: int = 48000  # Will be detected from device
        self.sample_rate: int = self.target_sample_rate  # For backwards compatibility
        self.chunk_size: int = 1024
        self.format = None  # Set in setup_audio() after pyaudio import
        self.channels: int = 1

        # Streaming configuration
        self.stream_chunk_duration: float = 0.5  # Send 0.5s chunks
        self.stream_chunk_size: int = int(
            self.target_sample_rate * self.stream_chunk_duration
        )
        self.audio_buffer: list[int] = []

        # Resampling support
        self.needs_resampling: bool = False
        self.resample_buffer: list[int] = []

        # Thread-safe queue for audio data
        self.audio_queue: Queue[bytes] = Queue()

    def setup_audio(self):
        """Initialize PyAudio."""
        # Import audio dependencies here (allows --help to work without them)
        try:
            import numpy as np  # noqa: PLC0415 - lazy import to allow --help without deps
            import pyaudio  # noqa: PLC0415 - lazy import to allow --help without deps

            try:
                import webrtcvad  # noqa: PLC0415 - optional local validation
            except ImportError as err:
                print(f"⚠️ Local WebRTC VAD import failed: {err}")
                print(
                    "   If the module is installed, ensure this Python matches the "
                    "install (e.g., correct venv/architecture)."
                )
                webrtcvad = None
        except ImportError:
            print("ERROR: Missing audio dependencies")
            print("Install: sudo apt-get install portaudio19-dev")
            print("Then: pip install pyaudio numpy")
            return False

        # Store imports as instance attributes for use in other methods
        self.np = np
        self.pyaudio = pyaudio
        self.webrtcvad = webrtcvad
        self.format = pyaudio.paInt16  # Set format now that pyaudio is imported

        try:
            self.pyaudio_instance = pyaudio.PyAudio()

            # Determine input device
            device_index = None
            if (
                self.args
                and hasattr(self.args, "device")
                and self.args.device is not None
            ):
                device_index = self.args.device
                device_info = self.pyaudio_instance.get_device_info_by_index(
                    device_index
                )
                print(f"🎤 Using device {device_index}: {device_info['name']}")

                # Use device's default sample rate
                self.device_sample_rate = int(device_info["defaultSampleRate"])
                print(f"   Device sample rate: {self.device_sample_rate}Hz")

                if self.device_sample_rate != self.target_sample_rate:
                    self.needs_resampling = True
                    print(
                        f"   Will resample to {self.target_sample_rate}Hz for Whisper"
                    )
            else:
                # Use default device
                default_device = self.pyaudio_instance.get_default_input_device_info()
                device_index = default_device["index"]
                self.device_sample_rate = int(default_device["defaultSampleRate"])
                print(f"🎤 Using: {default_device['name']}")

                if self.device_sample_rate != self.target_sample_rate:
                    self.needs_resampling = True
                    print(
                        f"   Will resample from {self.device_sample_rate}Hz to {self.target_sample_rate}Hz"
                    )

            self.audio_stream = self.pyaudio_instance.open(
                format=self.format,
                channels=self.channels,
                rate=self.device_sample_rate,  # Use device's native rate
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self.chunk_size,
                stream_callback=self.audio_callback,
            )

            if self.webrtcvad:
                print("✅ Local WebRTC VAD available for chunk sanity checks")
            else:
                print("⚠️ Local WebRTC VAD not installed; skipping local VAD ratio logs")

            return True

        except Exception as e:
            print(f"❌ Audio setup error: {e}")
            return False

    def audio_callback(self, in_data, frame_count, time_info, status):
        """Audio input callback - buffers and sends chunks."""
        if self.is_streaming:
            audio_data = self.np.frombuffer(in_data, dtype=self.np.int16)

            # Resample if needed
            if self.needs_resampling:
                # Simple decimation: take every Nth sample
                # For 48kHz -> 16kHz, take every 3rd sample (48000/16000 = 3)
                decimation_factor = self.device_sample_rate // self.target_sample_rate
                audio_data = audio_data[::decimation_factor]

            self.audio_buffer.extend(audio_data)

            # Send 0.5s chunks to queue
            while len(self.audio_buffer) >= self.stream_chunk_size:
                audio_chunk = self.np.array(
                    self.audio_buffer[: self.stream_chunk_size], dtype=self.np.int16
                )
                self.audio_buffer = self.audio_buffer[self.stream_chunk_size :]

                # Local WebRTC sanity check (does this chunk look like silence?)
                if self.webrtcvad:
                    self._log_local_vad(audio_chunk.tobytes())
                elif not self._local_vad_warned:
                    print("⚠️ Local VAD disabled; install webrtcvad to log ratios")
                    self._local_vad_warned = True

                # Put in queue for async sending
                self.audio_queue.put(audio_chunk.tobytes())

        return (in_data, self.pyaudio.paContinue)

    def _log_local_vad(self, audio_bytes: bytes) -> None:
        """
        Run local WebRTC VAD on the outgoing chunk to verify input shape.

        Uses 30ms frames (required by WebRTC). Logs voice ratio once per chunk.
        """
        if not self.webrtcvad:
            return

        vad = self.webrtcvad.Vad(3)
        frame_size = int(self.target_sample_rate * 0.03) * 2  # 30ms, 16-bit mono
        total_frames = max(len(audio_bytes) // frame_size, 1)
        voiced = 0
        for i in range(total_frames):
            frame = audio_bytes[i * frame_size : (i + 1) * frame_size]
            if len(frame) < frame_size:
                break
            if vad.is_speech(frame, sample_rate=self.target_sample_rate):
                voiced += 1
        voiced / total_frames
        # print(
        #     f"[LOCAL VAD] frames={total_frames} voiced_ratio={ratio:.2f} "
        #     f"bytes={len(audio_bytes)}"
        # )

    async def send_audio_loop(self):
        """Continuously send audio chunks from queue."""
        while self.is_streaming:
            try:
                # Check queue without blocking
                if not self.audio_queue.empty():
                    audio_bytes = self.audio_queue.get_nowait()
                    if self.websocket:
                        await self.websocket.send(audio_bytes)
                else:
                    # Small delay to avoid busy-waiting
                    await asyncio.sleep(0.01)
            except Exception as e:
                if self.is_streaming:
                    print(f"❌ Send error: {e}")

    def build_websocket_url(self) -> str:
        """
        Build WebSocket URL with Whisper quality profile and VAD parameters.

        Returns full WebSocket URL for HTTP/WebSocket mode, or path-only for Unix socket mode.
        """
        params = [f"model={self.model}"]
        is_direct = self.args and getattr(self.args, "direct", False)

        # Timeout configuration (only add if explicitly set, None = use server default)
        if self.args:
            session_timeout = getattr(self.args, "session_timeout", None)
            if session_timeout is not None:
                params.append(f"session_timeout={session_timeout}")

            inactivity_timeout = getattr(self.args, "inactivity_timeout", None)
            if inactivity_timeout is not None:
                params.append(f"inactivity_timeout={inactivity_timeout}")

        # Whisper quality profile (quality/balanced/fast)
        if self.args and self.args.whisper_profile:
            params.append(f"whisper_profile={self.args.whisper_profile}")

        # In direct mode (Gateway), apply local preset and pass params individually
        # In Stargate mode, pass profile param and let server expand it
        if self.args and self.args.preset:
            if is_direct:
                # Direct mode: Gateway doesn't know profiles, expand locally
                preset_params = LOCAL_PRESETS.get(self.args.preset, {})
                # Apply preset params (will be overridden by explicit args below)
                if preset_params.get("vad_method"):
                    params.append(f"vad_method={preset_params['vad_method']}")
                if preset_params.get("silero_threshold"):
                    params.append(
                        f"silero_threshold={preset_params['silero_threshold']}"
                    )
                if preset_params.get("silero_min_silence_ms"):
                    params.append(
                        f"silero_min_silence_ms={preset_params['silero_min_silence_ms']}"
                    )
                if preset_params.get("speech_pad_ms"):
                    params.append(f"speech_pad_ms={preset_params['speech_pad_ms']}")
            else:
                # Stargate mode: pass profile param, server handles expansion
                params.append(f"profile={self.args.preset}")

        # VAD method (explicit override)
        if self.args and self.args.vad_method:
            params.append(f"vad_method={self.args.vad_method}")

        # Silero params (explicit override)
        if self.args and getattr(self.args, "silero_threshold", None) is not None:
            params.append(f"silero_threshold={self.args.silero_threshold}")
        if self.args and getattr(self.args, "min_silence_ms", None) is not None:
            params.append(f"silero_min_silence_ms={self.args.min_silence_ms}")

        # WebRTC params
        if self.args and getattr(self.args, "webrtc_aggressiveness", None) is not None:
            params.append(f"webrtc_aggressiveness={self.args.webrtc_aggressiveness}")
        if self.args and getattr(self.args, "webrtc_voice_threshold", None):
            params.append(f"webrtc_voice_threshold={self.args.webrtc_voice_threshold}")

        # Energy params
        if self.args and getattr(self.args, "energy_threshold", None):
            params.append(f"energy_threshold={self.args.energy_threshold}")

        # Speech padding (explicit override)
        if self.args and getattr(self.args, "speech_pad_ms", None) is not None:
            params.append(f"speech_pad_ms={self.args.speech_pad_ms}")

        # Whisper internal VAD
        if self.args and getattr(self.args, "use_whisper_vad", False):
            params.append("use_whisper_vad=true")

        # Return path-only for Unix socket mode, full URL for HTTP/WebSocket mode
        path_with_params = f"/v1/audio/live_transcribe?{'&'.join(params)}"
        if self.unix_socket_path:
            return path_with_params
        else:
            return f"{self.stargate_url}{path_with_params}"

    async def connect_and_stream(self):
        """Connect to Whisper streaming endpoint."""
        uri = self.build_websocket_url()

        try:
            # Display connection information
            if self.unix_socket_path:
                print(f"🌐 Connecting to: unix:{self.unix_socket_path}{uri}")
            else:
                print(f"🌐 Connecting to: {uri}")

            # Establish WebSocket connection
            if self.unix_socket_path:
                # Unix socket connection
                # Check if socket exists
                if not os.path.exists(self.unix_socket_path):
                    print(f"❌ Unix socket not found: {self.unix_socket_path}")
                    return

                # Check if it's a socket (correct validation using stat.S_ISSOCK)
                if not stat.S_ISSOCK(os.stat(self.unix_socket_path).st_mode):
                    print(f"❌ Path is not a Unix socket: {self.unix_socket_path}")
                    return

                try:
                    # Connect via Unix domain socket
                    self.websocket = await websockets.unix_connect(
                        path=self.unix_socket_path,
                        uri=f"ws://localhost{uri}",  # Dummy host, real path in uri
                    )
                except PermissionError:
                    print(
                        "❌ Connection error: Permission denied (check socket permissions)"
                    )
                    return
                except ConnectionRefusedError:
                    print(
                        "❌ Connection error: Connection refused (is the service running?)"
                    )
                    return
                except Exception as e:
                    print(f"❌ Connection error: {e}")
                    return
            else:
                # Standard WebSocket connection
                self.websocket = await websockets.connect(uri)

            # Wait for ready message
            response = await self.websocket.recv()
            ready_info = json.loads(response)

            if ready_info.get("type") == "ready":
                session_id = ready_info.get("session_id")
                vad_method = ready_info.get("config", {}).get("vad_method")
                limits = ready_info.get("limits", {})
                monitor_mode = ready_info.get("monitor_mode", False)

                print(f"✅ Connected! Session: {session_id}")
                print(f"   VAD: {vad_method}")

                # Show timeout configuration
                if monitor_mode:
                    print("   Mode: Monitor (no timeouts)")
                else:
                    timeout_info = []
                    session_timeout = limits.get("session_timeout_s", 0)
                    inactivity_timeout = limits.get("inactivity_timeout_s", 0)
                    if session_timeout > 0:
                        timeout_info.append(f"session: {session_timeout}s")
                    if inactivity_timeout > 0:
                        timeout_info.append(f"inactivity: {inactivity_timeout}s")
                    if timeout_info:
                        print(f"   Timeouts: {', '.join(timeout_info)}")

                print("=" * 50)
                print("🎙️  Speak into your microphone...")
                print("   Press Ctrl+C to stop")
                print("=" * 50)
            else:
                print(f"❌ Connection failed: {ready_info}")
                return

            # Enable buffering/sending now that server is ready
            self.is_streaming = True

            # Start audio sending task
            send_task = asyncio.create_task(self.send_audio_loop())

            try:
                # Listen for transcriptions
                async for message in self.websocket:
                    try:
                        result = json.loads(message)
                        await self.handle_transcription(result)

                    except json.JSONDecodeError:
                        print(f"❌ Invalid response: {message}")
            finally:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass

        except websockets.exceptions.ConnectionClosed:
            print("\n🔌 Connection closed")
        except Exception as e:
            print(f"❌ Connection error: {e}")

    async def handle_transcription(self, result: dict):
        """Handle transcription result."""
        try:
            msg_type = result.get("type")

            if msg_type == "error":
                error_code = result.get("code")
                error_msg = result.get("message")
                print(f"❌ Error [{error_code}]: {error_msg}")
                return

            if msg_type == "transcription":
                text = result.get("text", "").strip()
                if text:
                    # Show transcription with timestamp
                    start = result.get("start_time", 0)
                    print(f"[{start:.1f}s] {text}")

            # Optionally show other message types for debugging
            elif msg_type not in ["ready"]:
                print(f"ℹ️  {msg_type}: {result}")

        except Exception as e:
            print(f"❌ Handle error: {e}")

    async def start_streaming(self):
        """Start streaming."""
        if not self.setup_audio():
            return

        # Start audio device immediately, but gate buffering/sending until server ready
        self.is_streaming = False
        self.audio_stream.start_stream()

        try:
            await self.connect_and_stream()
        except Exception as e:
            print(f"❌ Streaming error: {e}")
        finally:
            await self.stop_streaming()

    async def stop_streaming(self):
        """Stop streaming."""
        self.is_streaming = False

        if self.audio_stream:
            self.audio_stream.stop_stream()
            self.audio_stream.close()

        if self.pyaudio_instance:
            self.pyaudio_instance.terminate()

        if self.websocket:
            await self.websocket.close()


def signal_handler(signum, frame):
    """Handle Ctrl+C."""
    print("\n🛑 Stopping...")
    sys.exit(0)


async def main():
    signal.signal(signal.SIGINT, signal_handler)

    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Live microphone transcription with Whisper streaming"
    )
    parser.add_argument(
        "--stargate",
        default="ws://io:9999",
        help="Stargate WebSocket URL (default: ws://io:9999)",
    )
    parser.add_argument(
        "--unix-socket",
        metavar="PATH",
        help="Connect via Unix socket instead of HTTP/WebSocket (e.g., /tmp/universal-sockets/stargate.sock)",
    )
    parser.add_argument(
        "--model",
        default="whisper-large-v3",
        help="Whisper model ID (default: whisper-large-v3)",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Connect directly to Gateway (port 9998) bypassing Stargate",
    )
    parser.add_argument(
        "--device",
        type=int,
        metavar="INDEX",
        help="Audio input device index (use --list-devices to see available devices)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio input devices and exit",
    )

    # Timeout configuration (monitor mode by default)
    parser.add_argument(
        "--session-timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Maximum session duration in seconds (default: 0 = unlimited/monitor mode)",
    )
    parser.add_argument(
        "--inactivity-timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Timeout for silence/no-data in seconds (default: 0 = unlimited/monitor mode)",
    )

    # Whisper quality profile
    parser.add_argument(
        "--whisper-profile",
        choices=["quality", "balanced", "fast", "quality-file", "balanced-file"],
        help=(
            "Whisper quality profile. "
            "Streaming (default): quality, balanced, fast. "
            "File-optimized (higher latency): quality-file, balanced-file"
        ),
    )

    # VAD configuration
    parser.add_argument(
        "--vad-method",
        choices=["silero", "webrtc", "energy"],
        help="VAD method (default: silero)",
    )
    parser.add_argument(
        "--silero-threshold",
        type=float,
        metavar="0.1-0.9",
        help="Silero threshold (lower=more sensitive, default: 0.5)",
    )
    parser.add_argument(
        "--min-silence-ms",
        type=int,
        metavar="100-3000",
        help="Min silence before cutoff (default: 500)",
    )
    parser.add_argument(
        "--webrtc-aggressiveness",
        type=int,
        choices=[0, 1, 2, 3],
        help="WebRTC aggressiveness (default: 3)",
    )
    parser.add_argument(
        "--webrtc-voice-threshold",
        type=float,
        metavar="0.3-0.9",
        help="WebRTC voice threshold (default: 0.6)",
    )
    parser.add_argument(
        "--energy-threshold",
        type=float,
        metavar="0.01-0.5",
        help="Energy threshold (default: 0.1)",
    )
    parser.add_argument(
        "--speech-pad-ms",
        type=int,
        metavar="0-300",
        help="Speech padding in ms (default: 100)",
    )
    parser.add_argument(
        "--preset",
        choices=["sensitive", "balanced", "aggressive"],
        help="Use preset VAD configuration (via Stargate profile, or local in --direct mode)",
    )
    parser.add_argument(
        "--use-whisper-vad",
        action="store_true",
        help="Enable Whisper internal VAD (experimental)",
    )

    args = parser.parse_args()

    # Handle device listing
    if args.list_devices:
        try:
            import pyaudio

            p = pyaudio.PyAudio()
            print("\n🎤 Available Audio Input Devices:\n")
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:
                    print(f"  [{i}] {info['name']}")
                    print(
                        f"      Channels: {info['maxInputChannels']}, Sample Rate: {int(info['defaultSampleRate'])}Hz"
                    )
            p.terminate()
            return 0
        except Exception as e:
            print(f"❌ Failed to list devices: {e}")
            return 1

    # Validate mutually exclusive arguments
    if args.unix_socket and args.direct:
        print("❌ Error: --unix-socket and --direct are mutually exclusive")
        print("   --unix-socket connects via Unix domain socket")
        print("   --direct connects to Gateway via HTTP/WebSocket")
        return 1

    # Determine connection mode and URL
    unix_socket_path = None
    if args.unix_socket:
        # Unix socket mode
        unix_socket_path = args.unix_socket
        stargate_url = None  # Not used in Unix socket mode
    elif args.direct:
        # Direct mode: bypass Stargate, connect to Gateway directly
        stargate_url = (
            args.stargate.replace(":9999", ":9998")
            if ":9999" in args.stargate
            else args.stargate
        )
        if ":9998" not in stargate_url:
            stargate_url = "ws://localhost:9998"
        print("⚠️  Direct mode: bypassing Stargate, connecting to Gateway directly")
    else:
        stargate_url = args.stargate

    print("🎙️  Whisper Live Microphone Transcription")
    print("   Real-time speech-to-text")
    print()

    client = MicrophoneTranscriber(
        stargate_url=stargate_url if stargate_url else "",
        model=args.model,
        args=args,
        unix_socket_path=unix_socket_path,
    )

    try:
        await client.start_streaming()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped!")
        sys.exit(0)
