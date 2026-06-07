"""Tests for the webhook hardening: Twilio signature validation, PIN lockout,
Deepgram v7 transcription helper, and the wss URL builder."""

import server
from twilio.request_validator import RequestValidator

TOKEN = "faketwiliotoken"  # matches TWILIO_AUTH_TOKEN set in conftest


class TestTwilioSignature:
    def test_unsigned_voice_webhook_is_rejected(self, webhook_client):
        r = webhook_client.post("/voice/status", data={"CallSid": "CA1", "CallStatus": "completed"})
        assert r.status_code == 403

    def test_validly_signed_webhook_is_accepted(self, webhook_client):
        params = {"CallSid": "CA1", "CallStatus": "completed"}
        url = "http://localhost/voice/status"
        sig = RequestValidator(TOKEN).compute_signature(url, params)
        r = webhook_client.post("/voice/status", data=params, headers={"X-Twilio-Signature": sig})
        assert r.status_code == 204

    def test_bad_signature_is_rejected(self, webhook_client):
        r = webhook_client.post(
            "/voice/status", data={"CallSid": "CA1"}, headers={"X-Twilio-Signature": "nope"}
        )
        assert r.status_code == 403


class TestPinLockout:
    def setup_method(self):
        server.pin_attempts.clear()

    def test_locks_after_max_attempts(self):
        caller = "caller-a"
        for _ in range(server.PIN_MAX_ATTEMPTS):
            server._record_pin_fail(caller)
        assert server._pin_locked(caller) is True

    def test_not_locked_before_max(self):
        caller = "caller-b"
        for _ in range(server.PIN_MAX_ATTEMPTS - 1):
            server._record_pin_fail(caller)
        assert server._pin_locked(caller) is False

    def test_expired_window_resets(self):
        caller = "caller-c"
        for _ in range(server.PIN_MAX_ATTEMPTS):
            server._record_pin_fail(caller)
        count, _first = server.pin_attempts[caller]
        server.pin_attempts[caller] = (count, 0)  # window start in the distant past
        assert server._pin_locked(caller) is False


class TestDeepgramHelper:
    def test_returns_empty_without_client(self, monkeypatch):
        monkeypatch.setattr(server, "dg_client", None)
        assert server._deepgram_transcribe_file(b"audio") == ""


class TestWsUrl:
    def test_honours_webhook_override(self, monkeypatch):
        monkeypatch.setattr(server, "WEBHOOK_URL_OVERRIDE", "https://pub.example.com")
        assert server._ws_url() == "wss://pub.example.com/ws/call"
