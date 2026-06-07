"""
Test suite for Hermes Phone server.py security and correctness.

Covers:
  - Auth model (require_auth decorator)
  - Login / session cookie behaviour
  - Logout invalidation
  - Twilio signature validation (require_twilio decorator)
  - /call endpoint auth guard
  - update_setting injection prevention
  - get_system_prompt goal inclusion
  - PIN lockout helpers
  - Voicemail metadata round-trip and DELETE
  - api_get_settings secret masking
"""

import json
import time
from pathlib import Path

import pytest

import server


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

REMOTE = {"REMOTE_ADDR": "203.0.113.9"}
TOKEN = "secrettoken"  # matches HERMES_API_TOKEN set in conftest


def auth_header():
    return {"X-Hermes-Token": TOKEN}


# ═══════════════════════════════════════════════════════════════════
# Auth model — require_auth
# ═══════════════════════════════════════════════════════════════════

class TestRequireAuth:
    def test_localhost_direct_is_trusted(self, client):
        """127.0.0.1 with no proxy headers → 200 (not 401)."""
        rv = client.get("/health")
        assert rv.status_code == 200

    def test_remote_no_creds_is_rejected(self, remote_client):
        """Remote IP with no credentials → 401."""
        rv = remote_client.get("/health")
        assert rv.status_code == 401

    def test_remote_with_header_token_is_accepted(self, remote_client):
        """Remote IP + correct X-Hermes-Token header → 200."""
        rv = remote_client.get("/health", headers=auth_header())
        assert rv.status_code == 200

    def test_query_string_token_is_rejected(self, remote_client):
        """Token in query string must NOT be accepted → 401."""
        rv = remote_client.get(f"/health?token={TOKEN}")
        assert rv.status_code == 401

    def test_proxy_forwarded_for_is_not_auto_trusted(self, client):
        """
        A request that looks like localhost (REMOTE_ADDR=127.0.0.1) but has
        X-Forwarded-For is treated as going through a proxy, so it is NOT
        auto-trusted — it needs a credential.
        """
        rv = client.get(
            "/health",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert rv.status_code == 401

    def test_proxy_with_valid_token_is_accepted(self, client):
        """Request through proxy with valid token → 200."""
        rv = client.get(
            "/health",
            headers={"X-Forwarded-For": "203.0.113.9", "X-Hermes-Token": TOKEN},
        )
        assert rv.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# Login / session cookie
# ═══════════════════════════════════════════════════════════════════

class TestLogin:
    def test_wrong_token_returns_401(self, remote_client):
        """POST /login with wrong token → 401."""
        rv = remote_client.post(
            "/login",
            data={"token": "wrongtoken"},
            follow_redirects=False,
        )
        assert rv.status_code == 401

    def test_correct_token_returns_303(self, remote_client):
        """POST /login with correct token → 303 redirect."""
        rv = remote_client.post(
            "/login",
            data={"token": TOKEN},
            follow_redirects=False,
        )
        assert rv.status_code == 303

    def test_correct_login_sets_session_cookie(self, remote_client):
        """Successful login sets a hermes_session cookie."""
        rv = remote_client.post(
            "/login",
            data={"token": TOKEN},
            follow_redirects=False,
        )
        cookie = rv.headers.get("Set-Cookie", "")
        assert "hermes_session=" in cookie

    def test_cookie_is_httponly(self, remote_client):
        """Session cookie must be HttpOnly."""
        rv = remote_client.post(
            "/login",
            data={"token": TOKEN},
            follow_redirects=False,
        )
        cookie = rv.headers.get("Set-Cookie", "")
        assert "HttpOnly" in cookie

    def test_cookie_is_samesite_strict(self, remote_client):
        """Session cookie must be SameSite=Strict."""
        rv = remote_client.post(
            "/login",
            data={"token": TOKEN},
            follow_redirects=False,
        )
        cookie = rv.headers.get("Set-Cookie", "")
        assert "SameSite=Strict" in cookie

    def test_cookie_is_secure_when_behind_proxy(self, remote_client):
        """
        When X-Forwarded-Proto: https is present the Secure flag must be set
        on the cookie (the server treats this as being behind an HTTPS proxy).
        """
        rv = remote_client.post(
            "/login",
            data={"token": TOKEN},
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        cookie = rv.headers.get("Set-Cookie", "")
        assert "Secure" in cookie

    def test_cookie_value_is_opaque_not_equal_to_token(self, remote_client):
        """The cookie value must be an opaque session id, not the API token."""
        rv = remote_client.post(
            "/login",
            data={"token": TOKEN},
            follow_redirects=False,
        )
        cookie = rv.headers.get("Set-Cookie", "")
        # Extract the value of hermes_session=<value>
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("hermes_session="):
                sid = part.split("=", 1)[1]
                assert sid != TOKEN
                assert len(sid) > 20, "Session id looks too short to be opaque"
                break
        else:
            pytest.fail("hermes_session cookie not found")

    def test_session_cookie_authorises_requests(self, remote_client):
        """After login the session cookie alone should grant access."""
        # Log in to obtain the cookie (test client stores it automatically)
        remote_client.post(
            "/login",
            data={"token": TOKEN},
            follow_redirects=False,
        )
        # Subsequent request uses the stored cookie
        rv = remote_client.get("/health")
        assert rv.status_code == 200

    def test_forged_cookie_is_rejected(self, remote_client):
        """A cookie with an unknown session id → 401."""
        remote_client.set_cookie("hermes_session", "totallyforgedvalue123")
        rv = remote_client.get("/health")
        assert rv.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# Logout
# ═══════════════════════════════════════════════════════════════════

class TestLogout:
    def _login(self, c):
        c.post("/login", data={"token": TOKEN}, follow_redirects=False)

    def test_logout_get_invalidates_session(self, remote_client):
        """GET /logout should revoke the session so subsequent requests → 401."""
        self._login(remote_client)
        assert remote_client.get("/health").status_code == 200
        remote_client.get("/logout")
        rv = remote_client.get("/health")
        assert rv.status_code == 401

    def test_logout_post_invalidates_session(self, remote_client):
        """POST /logout should also revoke the session."""
        self._login(remote_client)
        assert remote_client.get("/health").status_code == 200
        remote_client.post("/logout")
        rv = remote_client.get("/health")
        assert rv.status_code == 401


# ═══════════════════════════════════════════════════════════════════
# Twilio signature validation
# ═══════════════════════════════════════════════════════════════════

class TestTwilioSignature:
    def test_missing_signature_returns_403(self, client):
        """
        With VALIDATE_TWILIO_SIGNATURE=true and TWILIO_AUTH_TOKEN set,
        a POST to /voice/incoming without a valid X-Twilio-Signature → 403.
        """
        # Ensure the module-level settings match what conftest set
        assert server.VALIDATE_TWILIO is True
        assert server.twilio_validator is not None

        rv = client.post(
            "/voice/incoming",
            data={"CallSid": "CA123", "From": "+44XXXXXXXXXX"},
        )
        assert rv.status_code == 403

    def test_invalid_signature_returns_403(self, client):
        """An obviously wrong signature is rejected."""
        rv = client.post(
            "/voice/incoming",
            data={"CallSid": "CA123", "From": "+44XXXXXXXXXX"},
            headers={"X-Twilio-Signature": "badsig"},
        )
        assert rv.status_code == 403


# ═══════════════════════════════════════════════════════════════════
# /call auth guard
# ═══════════════════════════════════════════════════════════════════

class TestCallEndpoint:
    def test_call_requires_auth_from_remote(self, remote_client):
        """POST /call from a remote IP without credentials → 401."""
        rv = remote_client.post(
            "/call",
            json={"to": "+44XXXXXXXXXX"},
        )
        assert rv.status_code == 401

    def test_call_accepts_header_token(self, remote_client):
        """
        POST /call with valid token is auth-accepted (but fails with 500
        because Twilio is not configured — that's fine, we just need ≠401/403).
        """
        rv = remote_client.post(
            "/call",
            json={"to": "+44XXXXXXXXXX"},
            headers=auth_header(),
        )
        # Should NOT be 401; server will return 400 (missing Twilio config) or
        # 500, but auth was accepted.
        assert rv.status_code not in (401, 403)


# ═══════════════════════════════════════════════════════════════════
# update_setting — injection prevention
# ═══════════════════════════════════════════════════════════════════

class TestUpdateSetting:
    def test_newline_injection_is_stripped(self, tmp_path):
        """
        A value containing CR/LF must not write extra lines into .env.
        We monkeypatch __file__'s parent so update_setting writes to tmp_path.
        """
        env_file = tmp_path / ".env"
        env_file.write_text('COMPANY_NAME="Acme"\n')

        # Temporarily redirect the path that update_setting resolves
        import server as srv
        original_file = srv.__file__
        # update_setting derives the path as: Path(__file__).parent / ".env"
        # Monkeypatch by replacing the module's __file__ attribute temporarily
        srv.__file__ = str(tmp_path / "server.py")

        try:
            malicious_value = 'Acme\nOPENAI_API_KEY="injected"'
            srv.update_setting("COMPANY_NAME", malicious_value)

            content = env_file.read_text()
            lines = [l for l in content.splitlines() if l.strip()]
            # Must be exactly one key=value line (the updated one)
            keys_found = [l.split("=")[0] for l in lines if "=" in l]
            assert "OPENAI_API_KEY" not in keys_found, (
                "Newline injection created an extra .env line!"
            )
        finally:
            srv.__file__ = original_file

    def test_double_quote_injection_is_stripped(self, tmp_path):
        """Double quotes in values are replaced with single quotes."""
        env_file = tmp_path / ".env"
        env_file.write_text('COMPANY_NAME="Old"\n')

        import server as srv
        original_file = srv.__file__
        srv.__file__ = str(tmp_path / "server.py")
        try:
            srv.update_setting("COMPANY_NAME", 'Evil"Value"Here')
            content = env_file.read_text()
            assert '"Evil"' not in content
        finally:
            srv.__file__ = original_file


# ═══════════════════════════════════════════════════════════════════
# get_system_prompt
# ═══════════════════════════════════════════════════════════════════

class TestGetSystemPrompt:
    def test_goal_is_included_in_prompt(self):
        """get_system_prompt(goal) must include the passed goal string."""
        original_sp = server.SYSTEM_PROMPT
        server.SYSTEM_PROMPT = ""  # ensure we use the default template
        try:
            goal = "Sell the premium plan to the caller"
            prompt = server.get_system_prompt(goal)
            assert goal in prompt
        finally:
            server.SYSTEM_PROMPT = original_sp

    def test_custom_system_prompt_overrides_template(self):
        """When SYSTEM_PROMPT is set it is returned verbatim."""
        original_sp = server.SYSTEM_PROMPT
        server.SYSTEM_PROMPT = "Custom override prompt."
        try:
            prompt = server.get_system_prompt("some goal")
            assert prompt == "Custom override prompt."
        finally:
            server.SYSTEM_PROMPT = original_sp


# ═══════════════════════════════════════════════════════════════════
# PIN lockout
# ═══════════════════════════════════════════════════════════════════

class TestPinLockout:
    def setup_method(self):
        server.pin_attempts.clear()

    def test_not_locked_initially(self):
        assert server._pin_locked("+44XXXXXXXXXX") is False

    def test_locked_after_max_attempts(self):
        caller = "+44XXXXXXXXXX"
        for _ in range(server.PIN_MAX_ATTEMPTS):
            server._record_pin_fail(caller)
        assert server._pin_locked(caller) is True

    def test_not_locked_before_max_attempts(self):
        caller = "+44XXXXXXXXXX"
        for _ in range(server.PIN_MAX_ATTEMPTS - 1):
            server._record_pin_fail(caller)
        assert server._pin_locked(caller) is False

    def test_lock_clears_after_window(self, monkeypatch):
        """After the lockout window expires the caller is no longer locked."""
        caller = "+44XXXXXXXXXX"
        for _ in range(server.PIN_MAX_ATTEMPTS):
            server._record_pin_fail(caller)
        assert server._pin_locked(caller) is True

        # Advance time past the lockout window
        future = time.time() + server.PIN_LOCKOUT_WINDOW + 1
        monkeypatch.setattr("server.time", type("_T", (), {"time": staticmethod(lambda: future)})())
        # Re-import so _pin_locked uses monkeypatched time
        # Simpler: directly expire the record by rewriting it
        count, _ = server.pin_attempts[caller]
        server.pin_attempts[caller] = (count, time.time() - server.PIN_LOCKOUT_WINDOW - 1)
        assert server._pin_locked(caller) is False


# ═══════════════════════════════════════════════════════════════════
# Voicemail metadata round-trip and DELETE
# ═══════════════════════════════════════════════════════════════════

class TestVoicemailMetadata:
    def test_save_and_load_roundtrip(self, tmp_path):
        """save_voicemails then load_voicemails should return the same data."""
        server.METADATA_FILE = tmp_path / "metadata.json"
        vms = [
            {"sid": "RE123", "from": "+44XXXXXXXXXX", "duration": 12,
             "transcript": "Hello", "read": False},
        ]
        server.save_voicemails(vms)
        loaded = server.load_voicemails()
        assert loaded == vms

    def test_load_returns_empty_list_when_file_absent(self, tmp_path):
        server.METADATA_FILE = tmp_path / "nonexistent.json"
        assert server.load_voicemails() == []

    def test_delete_voicemail_removes_record(self, client, tmp_path):
        """DELETE /voicemails/<sid> should remove that record."""
        server.METADATA_FILE = tmp_path / "metadata.json"
        server.AUDIO_DIR = tmp_path / "audio"
        server.AUDIO_DIR.mkdir(exist_ok=True)

        vms = [
            {"sid": "RE_keep", "from": "+44111", "duration": 5,
             "transcript": "", "read": False},
            {"sid": "RE_delete", "from": "+44222", "duration": 8,
             "transcript": "", "read": False},
        ]
        server.save_voicemails(vms)

        # The client fixture uses localhost so it's auto-trusted
        rv = client.delete("/voicemails/RE_delete")
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["sid"] == "RE_delete"

        remaining = server.load_voicemails()
        sids = [v["sid"] for v in remaining]
        assert "RE_delete" not in sids
        assert "RE_keep" in sids

    def test_delete_removes_audio_file(self, client, tmp_path):
        """DELETE /voicemails/<sid> also deletes the corresponding .wav file."""
        server.METADATA_FILE = tmp_path / "metadata.json"
        server.AUDIO_DIR = tmp_path / "audio"
        server.AUDIO_DIR.mkdir(exist_ok=True)

        sid = "RE_withfile"
        audio_file = server.AUDIO_DIR / f"{sid}.wav"
        audio_file.write_bytes(b"fake wav")
        server.save_voicemails([{"sid": sid, "from": "+44000", "duration": 3,
                                  "transcript": "", "read": False}])

        client.delete(f"/voicemails/{sid}")
        assert not audio_file.exists()


# ═══════════════════════════════════════════════════════════════════
# api_get_settings — masking
# ═══════════════════════════════════════════════════════════════════

class TestApiGetSettings:
    def _get_settings(self, client, tmp_path):
        """Write a known .env and hit /api/settings from trusted localhost."""
        env_file = tmp_path / ".env"
        # Built from a dict so this source file contains no literal KEY=secret
        # lines — keeps secret-scanning pre-commit hooks happy. The actual .env
        # (with = signs) is written to a tmp dir, never committed.
        fake_env = {
            "COMPANY_NAME": "Acme",
            "HERMES_API_TOKEN": "supersecrettoken",
            "DEEPGRAM_API_KEY": "dg_secret_key_here",
            "OPENAI_API_KEY": "sk-openai-secret",
            "TWILIO_AUTH_TOKEN": "AC12345678",
            "TTS_VOICE": "Polly.Amy",
        }
        env_file.write_text("".join(f'{k}="{v}"\n' for k, v in fake_env.items()))
        # Redirect server to read from this tmp .env
        import server as srv
        original_file = srv.__file__
        srv.__file__ = str(tmp_path / "server.py")
        try:
            rv = client.get("/api/settings")
            assert rv.status_code == 200
            return rv.get_json()
        finally:
            srv.__file__ = original_file

    def test_token_key_is_masked(self, client, tmp_path):
        data = self._get_settings(client, tmp_path)
        # Keys containing TOKEN/KEY/SECRET/AUTH should be masked
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if any(s in k.upper() for s in ["TOKEN", "KEY", "SECRET", "AUTH"]):
                assert v not in (
                    "supersecrettoken", "dg_secret_key_here",
                    "sk-openai-secret", "AC12345678",
                ), f"{k} was not masked (got {v!r})"

    def test_non_secret_keys_are_not_masked(self, client, tmp_path):
        data = self._get_settings(client, tmp_path)
        # COMPANY_NAME and TTS_VOICE have no secret-looking substrings
        assert data.get("COMPANY_NAME") == "Acme"
        assert data.get("TTS_VOICE") == "Polly.Amy"

    def test_status_and_voices_included(self, client, tmp_path):
        data = self._get_settings(client, tmp_path)
        assert "_status" in data
        assert "_available_voices" in data
        assert isinstance(data["_available_voices"], list)
