# Dialtone

**Give your AI agent a mouth, ears, and a phone number.**

Dialtone is a framework-agnostic VoIP/IVR service that connects any AI agent backend to a real phone number via Twilio. Plug in Hermes, OpenAI, Ollama, or any OpenAI-compatible endpoint — your agent handles incoming calls, makes outbound calls, takes voicemails, and more. Works on macOS, Linux, and Windows/WSL2.

## Architecture

```
                        ┌──────────────────────────────────────────┐
  Caller ←→ Twilio ←→  │  Dialtone Server                         │
                        │                                          │
                        │  ┌────────┐   ┌─────────┐   ┌────────┐  │
                        │  │  STT   │──→│  Agent   │──→│  TTS   │  │
                        │  │Provider│   │ Backend  │   │Provider│  │
                        │  └────────┘   └────┬────┘   └────────┘  │
                        │       9 providers  │  5+ providers       │
                        │               10 providers               │
                        │                    │                     │
                        │              ┌─────▼─────┐              │
                        │              │  Voicemail │──→ Telegram  │
                        │              └───────────┘               │
                        └──────────────────────────────────────────┘

  Port 5050 (public)  — Twilio webhooks, no auth required
  Port 5051 (private) — Dashboard + API, token-protected
```

**Two-port model:** The webhook server on 5050 accepts inbound Twilio requests. The dashboard on 5051 is behind auth and provides a full management UI. Keep 5051 behind a firewall or VPN.

## Supported Agent Backends

| Provider | AGENT_PROVIDER | Notes |
|---|---|---|
| **Hermes Agent** | `hermes-gateway` | Primary backend. Full tool/skill/memory support via Gateway API |
| **OpenAI API** | `openai` | GPT-4o, GPT-4o-mini, etc. |
| **OpenRouter** | `openrouter` | Access to 200+ models via single API |
| **Ollama** | `ollama` | Local LLMs, no API key needed |
| **LM Studio** | `lmstudio` | Local LLMs, no API key needed |
| **Xiaomi MiMo** | `xiaomi` | Free tier available |
| Any OpenAI-compat endpoint | — | Set `OPENAI_BASE_URL` + `OPENAI_API_KEY` + `LLM_MODEL` |

**Status:** Hermes Agent is the primary tested backend. We are actively seeking testers and feedback for other backends — open an issue or PR if you try one.

## Voice Providers

### STT (Speech-to-Text) — 9 providers

| Provider | Type | Cost | Notes |
|---|---|---|---|
| **mlx-whisper** | Local | Free | Apple Silicon native, auto-downloads model |
| **faster-whisper** | Local | Free | CTranslate2, 4x faster than base Whisper |
| **whisper.cpp** | Local | Free | C/C++, runs anywhere |
| **Deepgram Nova-3** | Cloud | $0.29/hr | $200 free credit |
| Groq Whisper | Cloud | $0.04/hr | Cheapest cloud option |
| AssemblyAI | Cloud | $0.21/hr | Strong multilingual |
| Google Cloud STT | Cloud | $0.96/hr | 125+ languages |
| Azure Speech | Cloud | $1.00/hr | Enterprise, custom models |
| OpenAI Whisper | Cloud | $0.06/hr | Simple API |

### TTS (Text-to-Speech) — 10+ providers

| Provider | Type | Cost | Notes |
|---|---|---|---|
| **Kokoro 82M** | Local | Free | MLX native, Apache-2.0 |
| Piper | Local | Free | C++, ultra-fast |
| Coqui XTTS v2 | Local | Free | Voice cloning, 16 languages |
| Bark | Local | Free | Expressive, laughter/pauses |
| **Edge TTS** | Cloud | Free | Azure neural voices, no API key |
| ElevenLabs | Cloud | ~$0.30/min | Best quality, voice cloning |
| Cartesia Sonic | Cloud | ~$0.003/credit | Lowest latency |
| OpenAI TTS | Cloud | $15/1M chars | Simple, good quality |
| AWS Polly | Cloud | $16/1M chars | 60+ languages |
| Deepgram Aura | Cloud | $0.03/1K chars | Telephony-optimized |

## Quick Start

### macOS

```bash
git clone https://github.com/jaylfc/dialtone.git
cd dialtone
chmod +x install.sh
./install.sh
```

The installer walks you through Twilio, STT, TTS, and agent backend setup. On macOS you get a native menu bar app with settings panel and voicemail manager.

### Linux

```bash
git clone https://github.com/jaylfc/dialtone.git
cd dialtone
chmod +x install-linux.sh
./install-linux.sh
```

Installs as a systemd service. Manage via the web dashboard on port 5051. Use `uninstall-linux.sh` for clean removal.

### Windows / WSL2

Install under WSL2 using the Linux instructions above. Access the web dashboard from Windows at `http://localhost:5051`. Native macOS features (menu bar, local MLX voice) are not available — use cloud STT/TTS providers.

## Configuration

All settings live in `~/.hermes/phone-agent/.env` and can be changed via the web dashboard or macOS settings panel.

### Agent Backend

```bash
# Choose your backend (default: auto-detect Hermes Gateway, then legacy LLM)
AGENT_PROVIDER=hermes-gateway   # or openai, openrouter, ollama, lmstudio, xiaomi

# Hermes Gateway settings
HERMES_GATEWAY_URL=http://127.0.0.1:8642
HERMES_GATEWAY_TOKEN=your-secret-key
HERMES_MODEL_OVERRIDE=          # empty = agent default

# OpenAI-compatible settings (used for openai, openrouter, ollama, lmstudio, xiaomi)
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini

# Provider-specific overrides
OLLAMA_BASE_URL=http://localhost:11434/v1
LMSTUDIO_BASE_URL=http://localhost:1234/v1
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

When `AGENT_PROVIDER` is empty, Dialtone auto-detects: tries Hermes Gateway first, then falls back to direct LLM calls, then no-op.

### Voice

```bash
STT_PROVIDER=deepgram      # or mlx-whisper, faster-whisper, groq, etc.
TTS_PROVIDER=polly          # or elevenlabs, kokoro, edge, etc.
TTS_VOICE=Polly.Brian
TTS_LANGUAGE=en-GB
USE_LOCAL_VOICE=auto        # auto, true, false
```

### Network

```bash
WEBHOOK_PORT=5050
DASHBOARD_PORT=5051
WEBHOOK_URL_OVERRIDE=       # set if behind a proxy/ngrok
DASHBOARD_TOKEN=auto-generated
```

### Call Handling

```bash
COMPANY_NAME=My Company
VOICEMAIL_EMAIL=hello@company.com
VOICEMAIL_PIN=1234
CALL_GOAL="Have a friendly conversation."
```

## Local / Offline Mode

Full offline capability on Apple Silicon — zero cloud API calls:

```bash
# 1. Install local voice engines
pip install mlx-whisper mlx-audio

# 2. Set in .env
STT_PROVIDER=mlx-whisper
TTS_PROVIDER=kokoro
USE_LOCAL_VOICE=true
AGENT_PROVIDER=ollama        # or lmstudio

# 3. Models auto-download on first use
# STT: whisper-large-v3-turbo (~1.6GB)
# TTS: Kokoro-82M-4bit (~50MB)
# LLM: whatever Ollama/LM Studio model you choose
```

**Cost: $0.00/min** after Twilio per-minute charges.

## Features

**Inbound calls:** Configurable greeting, PIN bypass to AI agent, voicemail with auto-transcription and Telegram notifications.

**Outbound calls:** Initiate from dashboard, menu bar, or API. AI agent handles the conversation. Per-call goal configuration.

**Voicemail:** Local recordings, auto-transcription, Telegram voice message + transcript, ZIP/text export, dashboard playback.

**Dashboard (port 5051):** Dark theme, mobile-friendly. Make calls, manage voicemails, configure all providers, discover models from Hermes/Ollama/LM Studio.

**macOS menu bar:** Color-coded status icon, start/stop/restart, phone number dialog for calls, voicemail manager, native settings panel (pywebview).

## API

```bash
# Health check (no auth)
curl http://localhost:5050/health

# Make a call
curl -X POST http://localhost:5051/call \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"to": "+1234567890", "goal": "Book a table for 4"}'

# List voicemails
curl -H "Authorization: Bearer <token>" http://localhost:5051/voicemails

# Get settings
curl -H "Authorization: Bearer <token>" http://localhost:5051/api/settings

# List available models
curl -H "Authorization: Bearer <token>" http://localhost:5051/api/models

# Export voicemails
curl -H "Authorization: Bearer <token>" http://localhost:5051/export/zip -o voicemails.zip
```

## Files

```
agents/
  base.py             — Abstract agent backend interface (AgentBackend ABC)
  hermes_gateway.py   — Hermes Agent Gateway backend
  openai_compat.py    — OpenAI-compatible backend (OpenAI, OpenRouter, Ollama, LM Studio, Xiaomi)
  noop.py             — Fallback when no backend configured
server.py             — Main server (two Flask apps: webhook + dashboard)
menubar.py            — macOS menu bar app with native settings panel
native_settings.py    — macOS native settings (pywebview)
local_voice.py        — Local STT/TTS via MLX (Apple Silicon)
provider_registry.py  — STT/TTS provider discovery and configuration
install.sh            — macOS setup wizard
install-linux.sh      — Linux setup wizard
uninstall-linux.sh    — Linux clean removal
run.sh                — Launch server
setup.sh              — Install dependencies
.env                  — Configuration (gitignored)
.env.example          — Configuration template
voicemails/           — Voicemail audio and metadata
requirements.txt      — Python dependencies
```

## Contributing

**We need testers for non-Hermes backends.** If you use OpenAI, OpenRouter, Ollama, LM Studio, or any OpenAI-compatible provider, please try it and open an issue with your results.

Workflow:
1. Fork and create a feature branch
2. Make changes, test locally
3. Open a PR — CodeRabbit reviews automatically
4. Address feedback, merge

## Requirements

- Python 3.11+
- Twilio account (any country, any number)
- One agent backend (Hermes Agent recommended, or any supported provider)

## License

MIT

## Credits

Built with [Hermes Agent](https://hermes-agent.nousresearch.com) by [JAN Labs](https://janlabs.co.uk).
