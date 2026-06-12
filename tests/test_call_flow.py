"""Tests for the core call path: /voice/* TwiML branches, the one-time
stream tokens that authenticate /ws/call (#66), and the live-STT turn
assembly helper (#69 interim duplication, #25 speech_final turn boundary).
"""

import base64
import json
import time

import pytest

import server


@pytest.fixture(autouse=True)
def _unsigned_webhooks(monkeypatch):
    """These tests assert TwiML content, not signatures (covered elsewhere)."""
    monkeypatch.setenv("VALIDATE_TWILIO_SIGNATURE", "false")


@pytest.fixture(autouse=True)
def _clean_tokens():
    server.stream_tokens.clear()
    yield
    server.stream_tokens.clear()


# ─── TwiML branches ──────────────────────────────────────────────────────────

class TestIncoming:
    def test_greeting_gather_and_record(self, webhook_client):
        r = webhook_client.post("/voice/incoming", data={"CallSid": "CA1", "From": "+15550001111"})
        assert r.status_code == 200
        xml = r.get_data(as_text=True)
        assert '<Gather' in xml and 'action="/voice/check-pin"' in xml
        assert '<Record' in xml and 'action="/voice/voicemail-complete"' in xml
        assert '<Stream' not in xml  # nobody reaches the AI without the PIN


class TestCheckPin:
    def setup_method(self):
        server.pin_attempts.clear()

    def _post(self, client, digits, caller="+15550001111"):
        return client.post("/voice/check-pin",
                           data={"CallSid": "CA1", "Digits": digits, "From": caller})

    def test_correct_pin_connects_stream_with_token(self, webhook_client, monkeypatch):
        monkeypatch.setattr(server, "VOICEMAIL_PIN", "1234")
        xml = self._post(webhook_client, "1234").get_data(as_text=True)
        assert '<Connect>' in xml and '<Stream' in xml
        assert 'name="token"' in xml
        assert len(server.stream_tokens) == 1
        (rec,) = server.stream_tokens.values()
        assert rec["call_sid"] == "CA1"

    def test_wrong_pin_records_voicemail_no_stream(self, webhook_client, monkeypatch):
        monkeypatch.setattr(server, "VOICEMAIL_PIN", "1234")
        xml = self._post(webhook_client, "9999").get_data(as_text=True)
        assert '<Record' in xml
        assert '<Stream' not in xml
        assert not server.stream_tokens

    def test_locked_out_caller_gets_voicemail_even_with_correct_pin(self, webhook_client, monkeypatch):
        monkeypatch.setattr(server, "VOICEMAIL_PIN", "1234")
        caller = "+15550002222"
        for _ in range(server.PIN_MAX_ATTEMPTS):
            server._record_pin_fail(caller)
        xml = self._post(webhook_client, "1234", caller=caller).get_data(as_text=True)
        assert '<Stream' not in xml
        assert '<Record' in xml


class TestOutgoing:
    def test_outgoing_stream_carries_token(self, webhook_client):
        r = webhook_client.post("/voice/outgoing", data={"CallSid": "CA9"})
        xml = r.get_data(as_text=True)
        assert '<Stream' in xml and 'name="token"' in xml
        assert len(server.stream_tokens) == 1
        (rec,) = server.stream_tokens.values()
        assert rec["call_sid"] == "CA9"


class TestVoicemailComplete:
    def test_metadata_written(self, webhook_client, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "METADATA_FILE", tmp_path / "metadata.json")
        r = webhook_client.post("/voice/voicemail-complete", data={
            "CallSid": "CA1", "RecordingSid": "RE1", "RecordingUrl": "https://x/y",
            "RecordingDuration": "7", "From": "+15550001111",
        })
        assert r.status_code == 200
        vms = server.load_voicemails()
        assert len(vms) == 1 and vms[0]["sid"] == "RE1" and vms[0]["duration"] == 7


# ─── Stream tokens (#66) ─────────────────────────────────────────────────────

class TestStreamTokens:
    def test_issue_and_redeem(self):
        t = server._issue_stream_token("CA1")
        assert server._redeem_stream_token(t, "CA1") is True

    def test_single_use(self):
        t = server._issue_stream_token("CA1")
        assert server._redeem_stream_token(t, "CA1") is True
        assert server._redeem_stream_token(t, "CA1") is False

    def test_wrong_call_sid_rejected(self):
        t = server._issue_stream_token("CA1")
        assert server._redeem_stream_token(t, "CA2") is False

    def test_missing_or_unknown_token_rejected(self):
        assert server._redeem_stream_token(None, "CA1") is False
        assert server._redeem_stream_token("forged", "CA1") is False

    def test_expired_token_rejected(self):
        t = server._issue_stream_token("CA1")
        server.stream_tokens[t]["ts"] = time.time() - server.STREAM_TOKEN_TTL - 1
        assert server._redeem_stream_token(t, "CA1") is False

    def test_issue_prunes_expired(self):
        t_old = server._issue_stream_token("CA1")
        server.stream_tokens[t_old]["ts"] = time.time() - server.STREAM_TOKEN_TTL - 1
        server._issue_stream_token("CA2")
        assert t_old not in server.stream_tokens


# ─── Live-STT turn assembly (#69, #25) ───────────────────────────────────────

def _msg(text, is_final=False, speech_final=None):
    """Build a fake Deepgram live message; speech_final=None omits the attr."""
    alt = type("Alt", (), {"transcript": text})()
    chan = type("Chan", (), {"alternatives": [alt]})()
    attrs = {"channel": chan, "is_final": is_final}
    if speech_final is not None:
        attrs["speech_final"] = speech_final
    return type("Msg", (), attrs)()


class TestTurnAssembly:
    def test_interim_results_are_not_accumulated(self):
        buf = []
        assert server._accumulate_stt_message(_msg("book", speech_final=False), buf) is False
        assert server._accumulate_stt_message(_msg("book a", speech_final=False), buf) is False
        assert server._accumulate_stt_message(
            _msg("book a table", is_final=True, speech_final=True), buf) is True
        assert " ".join(buf) == "book a table"

    def test_multi_segment_utterance(self):
        buf = []
        server._accumulate_stt_message(_msg("book a table", is_final=True, speech_final=False), buf)
        done = server._accumulate_stt_message(_msg("for four people", is_final=True, speech_final=True), buf)
        assert done is True
        assert " ".join(buf) == "book a table for four people"

    def test_segment_final_does_not_end_turn(self):
        # is_final fires per segment mid-sentence; only speech_final ends the turn
        buf = []
        assert server._accumulate_stt_message(
            _msg("book a table", is_final=True, speech_final=False), buf) is False

    def test_fallback_to_is_final_when_no_speech_final_attr(self):
        buf = []
        assert server._accumulate_stt_message(_msg("hello", is_final=True), buf) is True
        assert buf == ["hello"]

    def test_empty_transcript_ignored(self):
        buf = []
        assert server._accumulate_stt_message(_msg("", is_final=True, speech_final=False), buf) is False
        assert buf == []

    def test_message_with_no_alternatives_is_ignored(self):
        # Deepgram edge-case payloads must not raise on the callback thread
        chan = type("Chan", (), {"alternatives": []})()
        msg = type("Msg", (), {"channel": chan, "is_final": True, "speech_final": False})()
        buf = []
        assert server._accumulate_stt_message(msg, buf) is False
        assert buf == []


# ─── WS handler auth gating (pre-auth media must not reach STT) ──────────────

class _FakeWS:
    """Feeds a scripted message sequence to handle_ws, then closes."""

    def __init__(self, messages):
        self._messages = [json.dumps(m) for m in messages]
        self.sent = []

    def receive(self, timeout=None):
        if self._messages:
            return self._messages.pop(0)
        raise ConnectionError("closed")

    def send(self, data):
        self.sent.append(data)


class _FakeDGConn:
    def __init__(self):
        self.media_frames = []
        self.closed = False

    def on(self, *args, **kwargs):
        pass

    def send_media(self, audio):
        self.media_frames.append(audio)

    def close(self):
        self.closed = True


class _FakeDGClient:
    def __init__(self, conn):
        class _V1:
            def connect(self, **kwargs):
                return conn
        class _Listen:
            v1 = _V1()
        self.listen = _Listen()


def _media(payload=b"audio"):
    return {"event": "media", "media": {"payload": base64.b64encode(payload).decode()}}


class TestWsAuthGating:
    def _run(self, monkeypatch, messages):
        conn = _FakeDGConn()
        monkeypatch.setattr(server, "dg_client", _FakeDGClient(conn))
        server.handle_ws(_FakeWS(messages))
        return conn

    def test_media_before_start_never_reaches_stt(self, monkeypatch):
        conn = self._run(monkeypatch, [_media(), _media(), {"event": "stop"}])
        assert conn.media_frames == []
        assert conn.closed is True

    def test_media_after_invalid_start_never_reaches_stt(self, monkeypatch):
        start = {"event": "start",
                 "start": {"streamSid": "MZ1", "callSid": "CA1",
                           "customParameters": {"token": "forged"}}}
        conn = self._run(monkeypatch, [start, _media()])
        assert conn.media_frames == []

    def test_media_after_valid_start_reaches_stt(self, monkeypatch):
        token = server._issue_stream_token("CA1")
        start = {"event": "start",
                 "start": {"streamSid": "MZ1", "callSid": "CA1",
                           "customParameters": {"token": token}}}
        conn = self._run(monkeypatch, [start, _media(b"hello"), {"event": "stop"}])
        assert conn.media_frames == [b"hello"]
