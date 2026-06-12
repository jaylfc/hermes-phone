"""
Hermes Phone — Local Voice Engine

Provides fully offline STT/TTS using MLX-optimized models on Apple Silicon.
Zero API costs, zero cloud dependency, <800ms latency.

STT: mlx-whisper (whisper-large-v3-turbo, ~1.6GB)
TTS: mlx-audio with Kokoro-82M 4-bit (~50MB)
Fallback: edge-tts (cloud), AVSpeechSynthesizer (native macOS)
"""

import os
import sys
import audioop
import subprocess
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# Model management
# ═══════════════════════════════════════════════════════════════════

MODELS_DIR = Path(__file__).parent / "models"
STT_MODEL = "mlx-community/whisper-large-v3-turbo"
TTS_MODEL = "prince-canuma/Kokoro-82M-4bit"  # 4-bit quantized, ~50MB


def ensure_models():
    """Check that the MLX voice packages are importable. Returns True if ready.

    Never installs from here: running pip inside the long-lived server process
    blocks boot and can crash on macOS (fork-after-framework-init, issue #51).
    Install via install.sh or `python local_voice.py --install`.
    """
    try:
        import mlx_whisper  # noqa: F401
        import mlx_audio  # noqa: F401
        return True
    except ImportError:
        print("  ℹ️  MLX voice not installed — run `python local_voice.py --install` "
              "(or pip install mlx-whisper mlx-audio) to enable local voice")
        return False


def install_local_voice():
    """Install mlx-whisper and mlx-audio with dependencies."""
    python = sys.executable
    try:
        subprocess.run(
            [python, "-m", "pip", "install", "--quiet", "mlx-whisper", "mlx-audio"],
            check=True, capture_output=True, timeout=300,
        )
        print("  ✅ MLX voice models installed")
        return True
    except Exception as e:
        print(f"  ❌ Install failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════
# STT — mlx-whisper
# ═══════════════════════════════════════════════════════════════════

class LocalSTT:
    """Local speech-to-text using mlx-whisper."""

    def __init__(self, model=STT_MODEL):
        self.model = model
        self._client = None

    def _load(self):
        if self._client is None:
            import mlx_whisper
            self._client = mlx_whisper
            print(f"  ✅ STT loaded: {self.model}")

    def transcribe_file(self, audio_path: str) -> str:
        """Transcribe an audio file to text."""
        self._load()
        try:
            result = self._client.transcribe(
                audio_path,
                path_or_hf_repo=self.model,
                language="en",
                word_timestamps=False,
            )
            return result.get("text", "").strip()
        except Exception as e:
            print(f"  ❌ STT error: {e}")
            return ""

    def transcribe_bytes(self, audio_bytes: bytes, sample_rate=8000) -> str:
        """Transcribe raw audio bytes (PCM16)."""
        import tempfile
        # Save to temp WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            # Write WAV header + data
            import struct
            data_size = len(audio_bytes)
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + data_size))
            f.write(b"WAVE")
            f.write(b"fmt ")
            f.write(struct.pack("<I", 16))  # chunk size
            f.write(struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16))
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            return self.transcribe_file(tmp_path)
        finally:
            os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════
# TTS — mlx-audio (Kokoro)
# ═══════════════════════════════════════════════════════════════════

class LocalTTS:
    """Local text-to-speech using mlx-audio with Kokoro."""

    def __init__(self, model=TTS_MODEL, voice="af_heart"):
        self.model = model
        self.voice = voice
        self._client = None

    def _load(self):
        if self._client is None:
            from mlx_audio.tts import TTS
            self._client = TTS(model=self.model)
            print(f"  ✅ TTS loaded: {self.model} ({self.voice})")

    def synthesize(self, text: str) -> bytes:
        """Convert text to PCM16 audio at 24kHz. Returns raw bytes."""
        self._load()
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name

            self._client.generate(
                text=text,
                voice=self.voice,
                output_path=tmp_path,
            )

            # Read the WAV and extract PCM
            with open(tmp_path, "rb") as f:
                wav_data = f.read()
            os.unlink(tmp_path)

            # Skip WAV header (44 bytes) to get raw PCM
            if wav_data[:4] == b"RIFF":
                return wav_data[44:]
            return wav_data

        except Exception as e:
            print(f"  ❌ TTS error: {e}")
            return b""

    def synthesize_for_twilio(self, text: str) -> bytes:
        """Synthesize and resample to 8kHz for Twilio."""
        pcm_24k = self.synthesize(text)
        if pcm_24k:
            pcm_8k, _ = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, None)
            return pcm_8k
        return b""


# ═══════════════════════════════════════════════════════════════════
# Fallback TTS — edge-tts (cloud) and AVSpeechSynthesizer (native)
# ═══════════════════════════════════════════════════════════════════

class EdgeTTS:
    """Fallback TTS using Microsoft Edge's cloud voices."""

    def __init__(self, voice="en-GB-SoniaNeural"):
        self.voice = voice

    def synthesize_for_twilio(self, text: str) -> bytes:
        """Synthesize using edge-tts. Returns PCM16 at 8kHz."""
        try:
            import edge_tts
            import asyncio
            import tempfile

            async def _generate():
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    tmp_path = f.name
                communicate = edge_tts.Communicate(text, self.voice)
                await communicate.save(tmp_path)
                return tmp_path

            mp3_path = asyncio.run(_generate())

            # Convert MP3 to PCM using ffmpeg
            result = subprocess.run(
                ["ffmpeg", "-i", mp3_path, "-f", "s16le", "-ar", "8000", "-ac", "1", "-"],
                capture_output=True, timeout=30,
            )
            os.unlink(mp3_path)

            if result.returncode == 0:
                return result.stdout
            return b""
        except Exception as e:
            print(f"  ❌ Edge TTS error: {e}")
            return b""


class NativeTTS:
    """Zero-dependency fallback using macOS AVSpeechSynthesizer."""

    def synthesize_for_twilio(self, text: str) -> bytes:
        """Always returns silence.

        AVSpeechSynthesizer has no direct PCM-bytes output path without a
        subprocess; this engine exists only as a last-resort fallback so
        VoiceEngine initialisation doesn't fail when nothing else is
        available.
        """
        return b""


# ═══════════════════════════════════════════════════════════════════
# Unified voice engine
# ═══════════════════════════════════════════════════════════════════

class VoiceEngine:
    """
    Unified voice engine with automatic fallback:
    1. Local MLX (offline, fast, free)
    2. Edge TTS (cloud, high quality)
    3. Native macOS (zero deps, robotic)
    """

    def __init__(self, prefer_local=True):
        self.stt = None
        self.tts = None
        self.mode = "none"

        if prefer_local:
            self._init_local()

        if self.mode == "none":
            self._init_edge()

        if self.mode == "none":
            self._init_native()

        print(f"  Voice engine: {self.mode}")

    def _init_local(self):
        """Try to initialize local MLX voice."""
        try:
            if ensure_models():
                self.stt = LocalSTT()
                self.tts = LocalTTS()
                self.mode = "local-mlx"
        except Exception as e:
            print(f"  ⚠️ Local voice unavailable: {e}")

    def _init_edge(self):
        """Try edge-tts (cloud)."""
        import importlib.util
        if importlib.util.find_spec("edge_tts"):
            self.tts = EdgeTTS()
            # STT still needs something local or Deepgram
            self.mode = "edge-cloud"

    def _init_native(self):
        """Last resort: macOS native."""
        try:
            self.tts = NativeTTS()
            self.mode = "native-macos"
        except Exception:
            self.mode = "none"

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file to text."""
        if self.stt:
            return self.stt.transcribe_file(audio_path)
        return ""

    def speak(self, text: str) -> bytes:
        """Synthesize text to PCM16 audio for Twilio (8kHz)."""
        if self.tts:
            if hasattr(self.tts, "synthesize_for_twilio"):
                return self.tts.synthesize_for_twilio(text)
            elif hasattr(self.tts, "synthesize"):
                pcm_24k = self.tts.synthesize(text)
                if pcm_24k:
                    pcm_8k, _ = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, None)
                    return pcm_8k
        return b""

    @property
    def is_local(self) -> bool:
        return "local" in self.mode

    @property
    def is_available(self) -> bool:
        return self.mode != "none"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Local voice engine utilities")
    parser.add_argument("--install", action="store_true",
                        help="Install mlx-whisper + mlx-audio into the current interpreter")
    args = parser.parse_args()
    if args.install:
        ok = install_local_voice()
        raise SystemExit(0 if ok else 1)
    parser.print_help()
