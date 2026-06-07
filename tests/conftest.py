"""Shared fixtures for the server test suite.

Env is forced before importing `server` so the module initialises deterministically
(no local-voice pip install, signature validation on, no live Deepgram).
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("USE_LOCAL_VOICE", "false")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "faketwiliotoken")
os.environ.setdefault("VALIDATE_TWILIO_SIGNATURE", "true")
os.environ.setdefault("DEEPGRAM_API_KEY", "")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture()
def webhook_client():
    server.pin_attempts.clear()
    return server.webhook_app.test_client()
