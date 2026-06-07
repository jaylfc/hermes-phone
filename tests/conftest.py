"""
Shared fixtures for Hermes Phone test suite.

We import server exactly once at module level (after forcing env vars) so the
Flask app object and all module-level globals are initialised once per process.
"""

import os
import sys
import json
import importlib
import tempfile
from pathlib import Path

import pytest

# ── Force env before any server import ───────────────────────────────────────
os.environ.setdefault("USE_LOCAL_VOICE", "false")
os.environ.setdefault("HERMES_API_TOKEN", "secrettoken")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "faketwiliotoken")
os.environ.setdefault("VALIDATE_TWILIO_SIGNATURE", "true")
os.environ.setdefault("DEEPGRAM_API_KEY", "")

# Make sure the repo root is on sys.path so `import server` finds server.py
_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import server  # noqa: E402  (must come after env setup)


# ── Flask test client ─────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path):
    """
    Return a Flask test client with voicemail paths redirected to a temp dir.
    Clears in-memory sessions/pin_attempts before each test.
    """
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    metadata_file = tmp_path / "metadata.json"

    # Patch module-level paths so file I/O stays in tmp_path
    server.AUDIO_DIR = audio_dir
    server.METADATA_FILE = metadata_file

    # Reset stateful globals
    server.sessions.clear()
    server.pin_attempts.clear()

    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture()
def remote_client(tmp_path):
    """
    A test client that presents itself as coming from a remote IP (not localhost).
    Useful for auth tests that need a non-trusted origin.
    """
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    metadata_file = tmp_path / "metadata.json"

    server.AUDIO_DIR = audio_dir
    server.METADATA_FILE = metadata_file

    server.sessions.clear()
    server.pin_attempts.clear()

    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        # Simulate every request coming from a remote IP by default
        c.environ_base = {"REMOTE_ADDR": "203.0.113.9"}
        yield c
