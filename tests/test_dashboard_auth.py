"""Tests for dashboard auth hardening (opaque sessions, Secure/SameSite),
per-call goal, .env injection sanitisation, and voicemail metadata."""

import re

import pytest

import server

DTOK = "dashtok"


@pytest.fixture()
def dash(monkeypatch):
    monkeypatch.setattr(server, "DASHBOARD_TOKEN", DTOK)
    server.dashboard_sessions.clear()
    return server.dashboard_app.test_client()


class TestDashboardAuth:
    def test_no_creds_returns_login(self, dash):
        assert dash.get("/").status_code == 401

    def test_api_no_creds_returns_401(self, dash):
        assert dash.get("/api/settings").status_code == 401

    def test_login_wrong_token_rejected(self, dash):
        assert dash.post("/login", json={"token": "nope"}).status_code == 401

    def test_login_sets_opaque_session_cookie(self, dash):
        r = dash.post("/login", json={"token": DTOK})
        assert r.status_code == 200
        sc = r.headers.get("Set-Cookie", "")
        assert "hp_auth=" in sc and "HttpOnly" in sc and "SameSite=Strict" in sc
        sid = re.search(r"hp_auth=([^;]+)", sc).group(1)
        assert sid and sid != DTOK and len(sid) > 20  # opaque id, not the token

    def test_session_authorizes_then_logout_revokes(self, dash):
        dash.post("/login", json={"token": DTOK})
        assert dash.get("/voicemails").status_code == 200
        dash.get("/logout")
        assert dash.get("/voicemails").status_code == 401

    def test_forged_cookie_rejected(self, dash):
        dash.set_cookie("hp_auth", "forged-value-123")
        assert dash.get("/voicemails").status_code == 401

    def test_bearer_token_authorizes(self, dash):
        assert dash.get("/voicemails", headers={"Authorization": f"Bearer {DTOK}"}).status_code == 200

    def test_query_token_bootstrap_issues_session(self, dash):
        r = dash.get(f"/voicemails?token={DTOK}")
        assert r.status_code == 200
        assert "hp_auth=" in r.headers.get("Set-Cookie", "")

    def test_open_when_no_token_configured(self, monkeypatch):
        monkeypatch.setattr(server, "DASHBOARD_TOKEN", "")
        client = server.dashboard_app.test_client()
        assert client.get("/voicemails").status_code == 200


class TestPerCallGoal:
    def test_goal_param_used(self):
        old = server.SYSTEM_PROMPT
        server.SYSTEM_PROMPT = ""
        try:
            assert "Book a table" in server.get_system_prompt("Book a table")
        finally:
            server.SYSTEM_PROMPT = old

    def test_default_goal_used_when_none(self):
        old = server.SYSTEM_PROMPT
        server.SYSTEM_PROMPT = ""
        try:
            assert server.CALL_GOAL in server.get_system_prompt(None)
        finally:
            server.SYSTEM_PROMPT = old


class TestEnvInjection:
    def test_newline_value_does_not_inject_a_line(self, tmp_path, monkeypatch):
        envf = tmp_path / ".env"
        envf.write_text('COMPANY_NAME="Acme"\n')
        monkeypatch.setattr(server, "ENV_FILE", envf)
        server.update_setting("COMPANY_NAME", "Evil\nOPENAI_API_KEY=stolenkey")
        keys = [ln.split("=")[0] for ln in envf.read_text().splitlines() if "=" in ln]
        assert "OPENAI_API_KEY" not in keys


class TestVoicemailMetadata:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        mf = tmp_path / "metadata.json"
        monkeypatch.setattr(server, "METADATA_FILE", mf)
        data = [{"sid": "RE1", "from": "x"}, {"sid": "RE2"}]
        server.save_voicemails(data)
        assert server.load_voicemails() == data

    def test_load_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "METADATA_FILE", tmp_path / "nope.json")
        assert server.load_voicemails() == []


class TestLiveTokenChange:
    """Changing DASHBOARD_TOKEN via the API applies live: the new token works
    immediately, the old one stops working, and existing sessions are revoked
    (the behaviour the schema hint promises)."""

    def _client(self, monkeypatch, tmp_path):
        envf = tmp_path / ".env"
        envf.write_text('DASHBOARD_TOKEN="oldtok"\n')
        monkeypatch.setattr(server, "ENV_FILE", envf)
        monkeypatch.setattr(server, "DASHBOARD_TOKEN", "oldtok")
        monkeypatch.setenv("DASHBOARD_TOKEN", "oldtok")
        server.dashboard_sessions.clear()
        return server.dashboard_app.test_client()

    def test_new_token_applies_without_restart(self, monkeypatch, tmp_path):
        c = self._client(monkeypatch, tmp_path)
        r = c.post("/api/settings", json={"DASHBOARD_TOKEN": "newtok"},
                   headers={"Authorization": "Bearer oldtok"})
        assert r.status_code == 200
        assert c.get("/voicemails", headers={"Authorization": "Bearer oldtok"}).status_code == 401
        assert c.get("/voicemails", headers={"Authorization": "Bearer newtok"}).status_code == 200

    def test_existing_sessions_revoked_on_token_change(self, monkeypatch, tmp_path):
        c = self._client(monkeypatch, tmp_path)
        c.post("/login", json={"token": "oldtok"})
        assert c.get("/voicemails").status_code == 200  # session cookie works
        c.post("/api/settings", json={"DASHBOARD_TOKEN": "newtok"},
               headers={"Authorization": "Bearer oldtok"})
        assert not server.dashboard_sessions
        assert c.get("/voicemails").status_code == 401  # old session signed out
