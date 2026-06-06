# 📞 Hermes Phone

**Your AI agent, on the phone.** Open-source phone system for macOS that connects Twilio to your Hermes agent — with full offline capability on Apple Silicon.

Make and receive calls with your AI assistant. Leave voicemails. Manage everything from a native macOS menu bar app. Zero cloud dependencies required.

## Why?

- **Fully offline on Mac** — mlx-whisper (STT) + Kokoro TTS run locally on Apple Silicon. No API calls, no costs, no internet required for voice processing.
- **Wraps your Hermes agent** — calls go through your actual agent session with tools, memory, and skills. Not a dumb chatbot.
- **Any provider** — Deepgram, ElevenLabs, OpenAI, Azure, Google, Cartesia, Groq, or local. Mix and match.
- **Works worldwide** — any Twilio number in 180+ countries.
- **Native macOS app** — menu bar control center with color-coded status, settings panel, voicemail manager.

## Architecture

```
Phone ←→ Twilio ←→ Server (port 5050) ←→ STT ←→ Hermes Agent ←→ TTS
                                                    ↓
                                              Tools, Memory, Skills
                                                    ↓
Dashboard (port 5051, auth protected) ←→ Settings, Voicemails, Calls
```

**Two ports for security:**
- `5050` — Public webhook server (Twilio calls, no auth)
- `5051` — Protected dashboard + API (token auth, keep behind firewall)

## Quick Start

```bash
git clone https://github.com/jaylfc/dialtone.git
cd dialtone
chmod +x install.sh
./install.sh
```

The installer walks you through Twilio, STT, TTS, and Hermes Gateway setup.

## Voice Backends

### STT (Speech-to-Text)

**Recommended for Mac:**
| Provider | Type | Cost | Notes |
|----------|------|------|-------|
| **mlx-whisper** ⭐ | Local | Free | Apple Silicon native, auto-downloads model |
| **faster-whisper** | Local | Free | CTranslate2, 4x faster than Whisper |
| **whisper.cpp** | Local | Free | C/C++, runs anywhere |

**Cloud:**
| Provider | Cost | Notes |
|----------|------|-------|
| **Deepgram Nova-3** ⭐ | $0.29/hr | Best price/performance, $200 free credit |
| Groq Whisper | $0.04/hr | Cheapest cloud, 217x realtime |
| AssemblyAI | $0.21/hr | Strong multilingual |
| Google Cloud STT | $0.96/hr | 125+ languages |
| Azure Speech | $1.00/hr | Enterprise, custom models |
| Speechmatics | $0.24/hr | 56+ languages, on-device option |
| OpenAI Whisper | $0.06/hr | Simple API |

### TTS (Text-to-Speech)

**Recommended for Mac:**
| Provider | Type | Cost | Notes |
|----------|------|------|-------|
| **Kokoro 82M** ⭐ | Local | Free | 82M params, Apache-2.0, MLX native |
| Piper | Local | Free | C++, ultra-fast, embedded devices |
| Coqui XTTS v2 | Local | Free | Voice cloning, 16 languages |
| Bark | Local | Free | Expressive, laughter/pauses |
| Sesame CSM | Local | Free | Conversational, natural prosody |
| ChatTTS | Local | Free | Fine-grained prosody control |

**Cloud:**
| Provider | Cost | Notes |
|----------|------|-------|
| **Edge TTS** | Free | Azure neural voices, no API key |
| ElevenLabs | ~$0.30/min | Best quality, voice cloning |
| Cartesia Sonic | ~$0.003/credit | Lowest latency |
| OpenAI TTS | $15/1M chars | Simple, good quality |
| AWS Polly | $16/1M chars | Reliable, 60+ languages |
| Azure Speech | $15/1M chars | 140+ languages, custom voices |
| Deepgram Aura | $0.03/1K chars | Good for telephony |
| MiMo TTS | Free | Xiaomi, 4 English voices |

## AI Agent Integration

Hermes Phone wraps your **Hermes agent session**. When a call comes in:

1. Twilio sends audio to the server
2. STT transcribes the caller's speech
3. The transcribed text goes to your Hermes agent via the Gateway API
4. Your agent responds using its full capabilities (tools, memory, skills)
5. TTS converts the response to speech
6. Audio plays back to the caller

**Setup:**
```bash
# Enable the Hermes Gateway (if not already)
hermes config set api_server.enabled true
hermes config set api_server.key your-secret-key

# Set in phone-agent .env
HERMES_GATEWAY_URL=http://127.0.0.1:8642
HERMES_GATEWAY_TOKEN=your-secret-key
# HERMES_MODEL_OVERRIDE=  # Leave empty for agent default
```

**Model override:** Set `HERMES_MODEL_OVERRIDE` to use a specific model for calls (e.g., `anthropic/claude-sonnet-4`). Leave empty to use whatever your agent is configured with. The settings panel shows all models your Hermes agent has access to.

**Legacy fallback:** If Hermes Gateway isn't running, the phone agent falls back to direct LLM calls (Xiaomi MiMo, OpenAI, OpenRouter).

## Features

### Inbound Calls
- Greeting plays to all callers (configurable)
- PIN bypass connects to AI agent (hidden — not mentioned in greeting)
- Everyone else gets voicemail with beep
- Voicemail transcribed and sent to Telegram

### Outbound Calls
- Make calls from dashboard, menu bar, or API
- AI agent handles the conversation
- Call goal configurable per call

### Voicemail
- Recordings saved locally (`voicemails/audio/`)
- Auto-transcription via STT
- Telegram notifications with voice message + transcript
- Export as ZIP or plain text
- Playback in dashboard

### Web Dashboard (port 5051)
- Dark theme, mobile-friendly
- Make calls, manage voicemails, export data
- Full settings: company, voice, AI, providers, network
- Model discovery from Hermes Gateway, Ollama, LM Studio
- Service status indicators

### macOS Menu Bar
- Single phone icon: 🟢 running, 🔴 stopped
- Start/Stop/Restart server
- Make calls with phone number dialog
- Voicemail manager
- **Native settings panel** (pywebview, not browser redirect)
- Open dashboard in browser

## Security

**Dashboard is token-protected.** Webhook routes are open for Twilio.

```bash
# Login to dashboard
open http://localhost:5051
# Enter DASHBOARD_TOKEN from .env

# API access
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5051/api/settings
```

**Firewall advice:**
- Port 5050 must be public (Twilio webhooks)
- Port 5051 should be behind firewall (dashboard)
- Use `WEBHOOK_URL_OVERRIDE` if behind a proxy
- Consider VPN for dashboard access

## Configuration

All settings in `~/.hermes/phone-agent/.env`:

```bash
# Company
COMPANY_NAME=My Company
VOICEMAIL_EMAIL=hello@company.com
VOICEMAIL_PIN=1234

# Voice
STT_PROVIDER=deepgram        # or mlx-whisper, faster-whisper, groq, etc.
TTS_PROVIDER=polly            # or elevenlabs, kokoro, edge, etc.
TTS_VOICE=Polly.Brian
TTS_LANGUAGE=en-GB
USE_LOCAL_VOICE=auto          # auto, true, false

# AI
HERMES_GATEWAY_URL=http://127.0.0.1:8642
HERMES_GATEWAY_TOKEN=your-token
HERMES_MODEL_OVERRIDE=        # empty = agent default

# Network
WEBHOOK_PORT=5050
DASHBOARD_PORT=5051
WEBHOOK_URL_OVERRIDE=         # if behind proxy
DASHBOARD_TOKEN=auto-generated
```

All settings editable from the menu bar app or web dashboard.

## Fully Offline Setup

For zero cloud dependencies on Apple Silicon:

```bash
# 1. Install local voice engines
pip install mlx-whisper mlx-audio

# 2. Configure .env
STT_PROVIDER=mlx-whisper
TTS_PROVIDER=kokoro
USE_LOCAL_VOICE=true

# 3. Models auto-download on first use
# STT: whisper-large-v3-turbo (~1.6GB)
# TTS: Kokoro-82M-4bit (~50MB)
```

**Cost: $0.00/min** (after Twilio per-minute charges).

## API Reference

```bash
# Health check (no auth)
curl http://localhost:5050/health

# Make a call
curl -X POST http://localhost:5051/call \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "+123****7890", "goal": "Book a table for 4"}'

# List voicemails
curl -H "Authorization: Bearer TOKEN" http://localhost:5051/voicemails

# Get settings
curl -H "Authorization: Bearer TOKEN" http://localhost:5051/api/settings

# List available models
curl -H "Authorization: Bearer TOKEN" http://localhost:5051/api/models

# Export voicemails
curl -H "Authorization: Bearer TOKEN" http://localhost:5051/export/zip -o voicemails.zip
```

## Files

```
server.py        — Main server (two Flask apps: webhook + dashboard)
menubar.py       — macOS menu bar app with native settings panel
local_voice.py   — Local STT/TTS via MLX (Apple Silicon)
install.sh       — Setup wizard
uninstall.sh     — Clean removal
run.sh           — Launch server
setup.sh         — Install dependencies
.env             — Configuration (gitignored)
.env.example     — Configuration template
voicemails/      — Voicemail audio and metadata
requirements.txt — Python dependencies
```

## Requirements

- macOS (Apple Silicon recommended for local voice)
- Python 3.11+
- Twilio account (any country)
- Hermes Agent (for AI integration)

## Cost

| Setup | Per minute |
|-------|-----------|
| Fully local (mlx-whisper + Kokoro) | ~$0.014 (Twilio only) |
| Deepgram + MiMo TTS | ~$0.015 |
| Deepgram + ElevenLabs | ~$0.33 |
| Groq Whisper + Edge TTS | ~$0.05 |

## License

MIT

## Credits

Built with [Hermes Agent](https://hermes-agent.nousresearch.com) by [JAN Labs](https://janlabs.co.uk).
