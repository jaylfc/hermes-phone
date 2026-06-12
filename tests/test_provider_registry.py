"""Tests for the provider registry and its dashboard endpoints.

Regression coverage for #68: GET /api/providers 500'd with KeyError 'backend'
because the agent entries in PROVIDER_DEPS had no "backend" key.
"""

import pytest

import server
from provider_registry import PROVIDER_DEPS, get_provider_status


@pytest.fixture()
def dash(monkeypatch):
    monkeypatch.setattr(server, "DASHBOARD_TOKEN", "")  # open for the test
    return server.dashboard_app.test_client()


class TestProviderStatus:
    def test_get_provider_status_covers_every_entry(self):
        status = get_provider_status()
        assert set(status.keys()) == set(PROVIDER_DEPS.keys())

    def test_every_entry_has_required_fields(self):
        for pid, info in get_provider_status().items():
            for field in ("name", "type", "backend", "installed", "missing", "recommended", "models"):
                assert field in info, f"{pid} missing {field}"

    def test_agent_entries_have_backend(self):
        for pid, info in PROVIDER_DEPS.items():
            if info["type"] == "agent":
                assert info.get("backend") in ("cloud", "local"), pid


class TestProviderEndpoints:
    def test_api_providers_returns_200(self, dash):
        r = dash.get("/api/providers")
        assert r.status_code == 200
        data = r.get_json()
        assert "hermes-gateway" in data
        assert data["ollama"]["backend"] == "local"

    def test_api_provider_models_known(self, dash):
        r = dash.get("/api/providers/models?provider=mlx-whisper")
        assert r.status_code == 200
        assert "models" in r.get_json()

    def test_api_provider_models_unknown_is_400(self, dash):
        assert dash.get("/api/providers/models?provider=nope").status_code == 400

    def test_install_unknown_provider_is_400(self, dash):
        r = dash.post("/api/providers/install", json={"provider": "nope"})
        assert r.status_code == 400
