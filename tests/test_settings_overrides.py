"""Tests for editable network/security overrides in the settings UI:
schema presence, API default surfacing + accepting the new keys, and the
live (no-restart) Twilio-signature validation toggle."""

import server


class _StubBackend:
    def health_check(self):
        return {"ok": False, "model": None}


class TestSchema:
    def test_new_override_keys_present(self):
        for k in ("VALIDATE_TWILIO_SIGNATURE", "WEBHOOK_PORT", "DASHBOARD_PORT",
                  "PIN_MAX_ATTEMPTS", "PIN_LOCKOUT_WINDOW"):
            assert k in server.SETTINGS_SCHEMA


class TestApiSettings:
    def _client(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "ENV_FILE", tmp_path / ".env")
        monkeypatch.setattr(server, "DASHBOARD_TOKEN", "")  # open for the test
        monkeypatch.setattr(server, "get_agent_backend", lambda: _StubBackend())
        return server.dashboard_app.test_client()

    def test_get_surfaces_defaults(self, monkeypatch, tmp_path):
        data = self._client(monkeypatch, tmp_path).get("/api/settings").get_json()
        assert data["VALIDATE_TWILIO_SIGNATURE"] == "true"   # default surfaced when absent
        assert data["WEBHOOK_PORT"] == "5050"

    def test_update_accepts_overrides(self, monkeypatch, tmp_path):
        envf = tmp_path / ".env"
        envf.write_text("")
        monkeypatch.setattr(server, "ENV_FILE", envf)
        monkeypatch.setattr(server, "DASHBOARD_TOKEN", "")
        c = server.dashboard_app.test_client()
        r = c.post("/api/settings", json={"VALIDATE_TWILIO_SIGNATURE": "false", "WEBHOOK_PORT": "5060"})
        assert r.status_code == 200
        updated = r.get_json()["updated"]
        assert "VALIDATE_TWILIO_SIGNATURE" in updated and "WEBHOOK_PORT" in updated
        assert 'VALIDATE_TWILIO_SIGNATURE="false"' in envf.read_text()


class TestLiveToggle:
    def test_signature_validation_toggles_without_restart(self, webhook_client, monkeypatch):
        # default (enabled): unsigned webhook is rejected
        monkeypatch.setenv("VALIDATE_TWILIO_SIGNATURE", "true")
        assert webhook_client.post("/voice/status", data={"CallSid": "CA1"}).status_code == 403
        # disabled live → accepted, no restart
        monkeypatch.setenv("VALIDATE_TWILIO_SIGNATURE", "false")
        assert webhook_client.post("/voice/status", data={"CallSid": "CA1"}).status_code == 204


class TestBackendInvalidation:
    """Updating an agent-affecting setting must drop the cached backend (#72/PR review)."""

    def _client(self, monkeypatch, tmp_path):
        envf = tmp_path / ".env"
        envf.write_text("")
        monkeypatch.setattr(server, "ENV_FILE", envf)
        monkeypatch.setattr(server, "DASHBOARD_TOKEN", "")
        return server.dashboard_app.test_client()

    def test_agent_key_resets_cached_backend(self, monkeypatch, tmp_path):
        import agents
        monkeypatch.setenv("AGENT_PROVIDER", "ollama")  # registered for teardown restore
        c = self._client(monkeypatch, tmp_path)
        agents._backend = object()  # simulate a cached backend
        try:
            assert c.post("/api/settings", json={"AGENT_PROVIDER": "openai"}).status_code == 200
            assert agents._backend is None
        finally:
            agents.reset_agent_backend()

    def test_unrelated_key_keeps_cached_backend(self, monkeypatch, tmp_path):
        import agents
        monkeypatch.setenv("COMPANY_NAME", "Original")
        c = self._client(monkeypatch, tmp_path)
        sentinel = object()
        agents._backend = sentinel
        try:
            assert c.post("/api/settings", json={"COMPANY_NAME": "Acme"}).status_code == 200
            assert agents._backend is sentinel
        finally:
            agents.reset_agent_backend()
