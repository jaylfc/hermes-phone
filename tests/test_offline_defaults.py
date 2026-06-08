"""Offline-by-default: a fresh install (no .env keys) should default to local
providers so nothing but Twilio needs configuring."""

import server


def test_llm_defaults_to_local_ollama():
    assert server.AGENT_PROVIDER == "ollama"
    assert server.LLM_PROVIDER == "ollama"
    assert server.LLM_MODEL.startswith("qwen3")  # installer tiers the exact tag by RAM


def test_voice_defaults_to_local():
    assert server.STT_PROVIDER == "whisper"
    assert server.TTS_PROVIDER == "kokoro"
    # USE_LOCAL_VOICE default is "auto" (local-first); conftest forces it off for
    # test isolation, so we don't assert on it here.
