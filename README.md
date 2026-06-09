# Dialtone

**Give your AI agent a mouth, ears, and a phone number.**

Dialtone is a framework-agnostic VoIP/IVR service that connects any AI agent to a real phone number via Twilio. Your agent handles incoming calls, makes outbound calls, manages voicemails, and more — with pluggable STT, TTS, and LLM backends. Works on macOS, Linux, and Windows/WSL2.

---

## Built on Hermes Agent

> **Hermes Agent** by [Nous Research](https://nousresearch.com) is Dialtone's primary platform and testing target. Dialtone was originally built as a phone interface for Hermes and ships with deep Hermes integration out of the box.

**What Hermes brings to Dialtone:**

- **Full tool/skill support** — your phone agent can use any Hermes skill, browse the web, manage files, run code, and more during a call
- **Persistent memory** — conversations and caller context survive across sessions
- **Named conversations** — each caller gets their own conversation thread, tracked by caller ID
- **Gateway API** — single endpoint (`http://127.0.0.1:8642/v1/responses`) with streaming, tool use, and structured output
- **Model routing** — Hermes can route to any provider (OpenAI, Anthropic, local Ollama, etc.) behind the scenes
- **Agent orchestration** — spawn sub-agents, delegate tasks, chain calls — all accessible from a phone conversation

**Quick Hermes setup:**

```bash
# In .env
AGENT_PROVIDER=hermes-gateway
HERMES_GATEWAY_URL=http://127.0.0.1:8642
HERMES_GATEWAY_TOKEN=your-hermes-key
HERMES_MODEL_OVERRIDE=               # empty = use agent's default model
```

When `AGENT_PROVIDER` is set to `hermes-gateway` or left on `auto`, Dialtone automatically detects and connects to a running Hermes Gateway instance.

**Not a Hermes user?** No problem — Dialtone works with any framework. Read on.

---

## Architecture

```
                        ┌──────────────────────────────────────────┐
  Caller ←→ Twilio ←→   │  Dialtone Server                         │
                        │                                          │
                        │  ┌────────┐   ┌─────────┐   ┌────────┐   │
                        │  │  STT   │──→│  Agent  │──→│  TTS   │   │
                        │  │Provider│   │ Backend │   │Provider│   │
                        │  └────────┘   └────┬────┘   └────────┘   │
                        │       9 providers  │  5+ providers       │
                        │               10 providers               │
                        │                    │                     │
                        │              ┌─────▼─────┐               │
                        │              │ Voicemail │──→ Telegram   │
                        │              └───────────┘               │
                        └──────────────────────────────────────────┘

  Port 5050 (public)  — Twilio webhooks, no auth required
  Port 5051 (private) — Dashboard + API, token-protected
```

**Two-port model:** The webhook server on 5050 accepts inbound Twilio requests with no auth (Twilio needs open access). The dashboard on 5051 is behind token auth and provides the full management UI. Keep 5051 behind a firewall or VPN.

---

## Agent Backends

Dialtone is framework-agnostic. Plug in any AI agent or LLM:

| Backend | AGENT_PROVIDER | What you get |
|---|---|---|
| **Hermes Agent** | `hermes-gateway` | Full agent with tools, skills, memory, orchestration |
| **OpenAI API** | `openai` | GPT-4o, GPT-4o-mini, etc. Direct API calls |
| **OpenRouter** | `openrouter` | 200+ models via single API key |
| **Ollama** | `ollama` | Run any local LLM (Llama, Mistral, Phi, Qwen, etc.) |
| **LM Studio** | `lmstudio` | Local LLMs with GUI, OpenAI-compatible API |
| **Xiaomi MiMo** | `xiaomi` | MiMo 2.5 reasoning model, free tier available |
| **Any OpenAI-compat** | `openai-compat` | Set base URL + API key + model name |

**Auto-detection:** When `AGENT_PROVIDER=auto` (default), Dialtone tries Hermes Gateway first, then falls back to direct LLM calls, then a no-op fallback.

**How it works:** Each backend implements the same `AgentBackend` interface:
- `health_check()` — is the backend reachable?
- `chat(message, history)` — send a message, get a response
- `get_models()` — list available models
- `on_call_start()` / `on_call_end()` — lifecycle hooks

Adding a new backend? Create a file in `agents/` and register it in the factory.

---

## Voice Providers

### STT (Speech-to-Text) — 9 providers

| Provider | Type | Cost | Notes |
|---|---|---|---|
| **mlx-whisper** | Local | Free | Apple Silicon native, auto-downloads model (~1.6GB) |
| **faster-whisper** | Local | Free | CTranslate2, 4x faster than base Whisper |
| **whisper.cpp** | Local | Free | C/C++, runs anywhere |
| **Deepgram Nova-3** | Cloud | $0.29/hr | $200 free credit, real-time streaming |
| Groq Whisper | Cloud | $0.04/hr | Cheapest cloud option |
| AssemblyAI | Cloud | $0.21/hr | Strong multilingual |
| Google Cloud STT | Cloud | $0.96/hr | 125+ languages |
| Azure Speech | Cloud | $1.00/hr | Enterprise, custom models |
| OpenAI Whisper | Cloud | $0.06/hr | Simple API |

### TTS (Text-to-Speech) — 10+ providers

| Provider | Type | Cost | Notes |
|---|---|---|---|
| **Kokoro 82M** | Local | Free | MLX native, Apache-2.0, ~50MB 4-bit quant |
| Piper | Local | Free | C++, ultra-fast |
| Coqui XTTS v2 | Local | Free | Voice cloning, 16 languages |
| Bark | Local | Free | Expressive, laughter/pauses |
| **Edge TTS** | Cloud | Free | Azure neural voices, no API key needed |
| ElevenLabs | Cloud | ~$0.30/min | Best quality, voice cloning |
| Cartesia Sonic | Cloud | ~$0.003/credit | Lowest latency |
| OpenAI TTS | Cloud | $15/1M chars | Simple, good quality |
| AWS Polly | Cloud | $16/1M chars | 60+ languages, multiple voice styles |
| Deepgram Aura | Cloud | $0.03/1K chars | Telephony-optimized |
| MiMo V2.5 TTS | Cloud | Free | Xiaomi TTS, 4 English voices (Mia, Chloe, Dean, Milo) |

**Auto-fallback:** When `USE_LOCAL_VOICE=auto`, Dialtone tries MLX → edge-tts → native macOS speech. Models download automatically on first use.

---

## Features

**Inbound calls**
- Configurable greeting (plays via TTS, customizable via `VOICEMAIL_GREETING`)
- Hidden PIN bypass — only callers who know the PIN reach the AI agent
- Everyone else hears the greeting, gets a beep, and can leave a voicemail
- Caller ID detection (handles forwarded calls, blocked numbers)

**Outbound calls**
- Initiate from dashboard, menu bar app, or API
- AI agent handles the full conversation
- Per-call goal configuration (e.g. "Book a table for 4")

**Voicemail**
- Local audio recordings with metadata
- Auto-transcription via STT provider
- Telegram notifications: voice message + text transcript
- Dashboard playback with inline audio player
- Export: ZIP (audio + metadata + transcripts) or plain text

**Web Dashboard (port 5051)**
- Dark theme, mobile-friendly design
- Stats overview (total voicemails, recent activity)
- Voicemail manager with playback and transcription
- Outbound call form
- Full settings panel — all `.env` options configurable via UI
- Provider discovery: browse and install STT/TTS/LLM providers
- Model discovery: fetch available models from Hermes, Ollama, LM Studio, OpenRouter
- Token-protected with auto-auth from menu bar

**macOS Menu Bar App**
- Single colored phone icon (green = running, red = stopped)
- Start / Stop / Restart server
- Quick outbound call dialog
- Voicemail manager window
- Native AppKit settings panel (5 tabs, all config options)
- One-click dashboard open (auto-authenticated)
- Auto-starts on login via LaunchAgent

**Security**
- Two-port model: public webhooks (5050) vs private dashboard (5051)
- `DASHBOARD_TOKEN` for API/dashboard auth (auto-generated during install)
- PIN-protected AI access (not mentioned in greeting — callers don't know it exists)
- Webhook routes are validated by Twilio request signature (`X-Twilio-Signature`), not token auth (`VALIDATE_TWILIO_SIGNATURE=true`; set false only for local dev)
- PIN gate is constant-time and rate-limited — a caller is locked out after repeated wrong attempts (`PIN_MAX_ATTEMPTS`, `PIN_LOCKOUT_WINDOW`)
- Dashboard auth uses an opaque, revocable 30-day session cookie (HttpOnly, SameSite=Strict, Secure behind TLS) — the cookie never stores the raw token
- No secrets in public repo — all credentials in `.env` (gitignored)

---

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

Installs as a systemd user service. Manage via the web dashboard on port 5051. Use `uninstall-linux.sh` for clean removal.

### Windows / WSL2

Install under WSL2 using the Linux instructions above. Access the web dashboard from Windows at `http://localhost:5051`. Native macOS features (menu bar, local MLX voice) are not available — use cloud STT/TTS providers.

---

## Configuration

All settings live in `~/.hermes/phone-agent/.env` and can be changed via the web dashboard (`/settings.html`) or the macOS native settings panel.

### Agent Backend

```bash
# Choose your backend (default: auto-detect)
AGENT_PROVIDER=auto              # auto | hermes-gateway | openai | openrouter | ollama | lmstudio | xiaomi | openai-compat

# Hermes Gateway
HERMES_GATEWAY_URL=http://127.0.0.1:8642
HERMES_GATEWAY_TOKEN=your-hermes-key
HERMES_MODEL_OVERRIDE=           # empty = use agent's default model

# OpenAI-compatible (for openai, openrouter, ollama, lmstudio, xiaomi)
LLM_BASE_URL_OVERRIDE=https://api.openai.com/v1
LLM_API_KEY_OVERRIDE=sk-...
LLM_MODEL_OVERRIDE=gpt-4o-mini
```

### Voice

```bash
STT_PROVIDER=deepgram            # mlx-whisper | faster-whisper | whispercpp | deepgram | groq | assemblyai | google | azure | openai
TTS_PROVIDER=polly               # kokoro | piper | coqui | bark | edge | elevenlabs | cartesia | openai | polly | aura | mimo
TTS_VOICE=Polly.Brian
TTS_LANGUAGE=en-GB
USE_LOCAL_VOICE=auto             # auto | true | false
```

### Twilio

```bash
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-auth-token
TWILIO_PHONE_NUMBER=+123****7890
MY_PHONE_NUMBER=+098****4321
VALIDATE_TWILIO_SIGNATURE=true    # verify X-Twilio-Signature on /voice/* webhooks
```

### Call Handling

```bash
COMPANY_NAME="My Company"
VOICEMAIL_EMAIL=hello@company.com
VOICEMAIL_PIN=1234
PIN_MAX_ATTEMPTS=5               # lock out a caller after this many wrong PINs
PIN_LOCKOUT_WINDOW=600           # lockout window, in seconds
VOICEMAIL_GREETING="Hi, you've reached our AI assistant..."
CALL_GOAL="Have a friendly conversation."
```

### Network

```bash
WEBHOOK_PORT=5050
DASHBOARD_PORT=5051
WEBHOOK_URL_OVERRIDE=            # set if behind a proxy/ngrok
DASHBOARD_TOKEN=auto-generated   # change to set a custom token
```

### Notifications

```bash
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

---

## Local / Offline Mode

Full offline capability on Apple Silicon — zero cloud API costs after Twilio per-minute charges:

```bash
# 1. Install local voice engines
pip install mlx-whisper mlx-audio

# 2. Set in .env
STT_PROVIDER=mlx-whisper
TTS_PROVIDER=kokoro
USE_LOCAL_VOICE=true
AGENT_PROVIDER=ollama        # or lmstudio for fully local LLM

# 3. Models auto-download on first use
# STT: whisper-large-v3-turbo (~1.6GB)
# TTS: Kokoro-82M-4bit (~50MB)
# LLM: whatever Ollama/LM Studio model you choose
```

**Cost: $0.00/min** after Twilio per-minute charges.

For non-Apple-Silicon systems, use `faster-whisper` (STT) + `piper` (TTS) + `ollama` (LLM) for a fully local stack on Linux/Windows.

---

## API

```bash
# Health check (no auth required)
curl http://localhost:5050/health

# Make a call
curl -X POST http://localhost:5051/call \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "+123****7890", "goal": "Book a table for 4"}'

# List voicemails
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5051/voicemails

# Get settings (secrets masked)
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5051/api/settings

# Update settings
curl -X POST http://localhost:5051/api/settings \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"COMPANY_NAME": "New Name"}'

# List available models from active backend
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5051/api/models

# List STT/TTS providers
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5051/api/providers

# Install a provider
curl -X POST http://localhost:5051/api/providers/install \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider": "kokoro", "type": "tts"}'

# Export voicemails as ZIP
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5051/export/zip -o voicemails.zip

# Export transcripts as plain text
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:5051/export/transcripts -o transcripts.txt
```

---

## Files

```
agents/
  __init__.py           — Backend factory + auto-detection
  base.py               — Abstract agent interface (AgentBackend ABC)
  hermes_gateway.py     — Hermes Agent Gateway adapter
  openai_compat.py      — OpenAI-compatible adapter (OpenAI, OpenRouter, Ollama, LM Studio, Xiaomi)
  noop.py               — Fallback when no backend configured
server.py               — Main server (Flask: webhook on 5050, dashboard on 5051)
menubar.py              — macOS menu bar app (AppKit, colored phone icon, native settings)
native_settings.py      — macOS native settings window (pyobjc AppKit)
local_voice.py          — Local STT/TTS via MLX (Apple Silicon)
provider_registry.py    — STT/TTS provider discovery, install commands, model lists
install.sh              — macOS setup wizard with interactive configuration
install-linux.sh        — Linux/WSL2 setup wizard with systemd service
uninstall.sh            — macOS clean removal
uninstall-linux.sh      — Linux clean removal
run.sh                  — Launch server
setup.sh                — Install Python dependencies
requirements.txt        — Dependencies with platform markers
.coderabbit.yaml        — CodeRabbit PR review configuration
.env                    — Configuration (gitignored)
.env.example            — Configuration template with all options
icons/                  — Colored phone icons (green, red, 22×22)
voicemails/             — Voicemail audio files and metadata
```

---

## Roadmap

Features planned or under consideration for future releases:

### Near-term

- **Built-in agent harness** — Run an embedded agent (e.g. [OpenCode](https://github.com/opencode-ai/opencode)) directly inside Dialtone without needing an external gateway. Choose from offline chat models via Ollama.
- **Persona & training interface** — Configure your phone agent's personality, tone, and knowledge via the dashboard. Upload documents for RAG (retrieval-augmented generation) context.
- **Skills & permissions** — Define what your phone agent can and cannot do per-call. Grant/revoke tool access (web search, file access, API calls) via the dashboard.
- **Multi-language support** — Auto-detect caller language and respond accordingly. Configure per-language greetings and voices.
- **Call recording & playback** — Record full AI conversations (with consent), not just voicemails. Dashboard playback with transcription.

### Medium-term

- **SMS/WhatsApp integration** — Extend beyond voice to text-based channels via Twilio Messaging.
- **Caller profiles** — Persistent per-caller memory. Recognize returning callers, remember preferences, continue previous conversations.
- **Call analytics** — Dashboard with call duration, frequency, sentiment analysis, common topics.
- **Webhook forwarding** — Forward voicemail transcripts and call summaries to external services (Slack, email, Notion, custom webhooks).
- **Multi-number support** — Manage multiple Twilio numbers from one Dialtone instance, each with its own agent and greeting.
- **RAG document pipeline** — Upload PDFs, docs, or URLs → chunk → embed → vector search. Agent answers questions from your knowledge base during calls.

### Long-term

- **Alternative voice pipelines** — Adapter support for [Pipecat](https://github.com/pipecat-ai/pipecat) (12.7k★), [LiveKit Agents](https://github.com/livekit-agents) (10.9k★), or [Patter](https://github.com/patter-ai/patter) (504★) for users who want different real-time voice architectures.
- **Multi-agent orchestration** — Route calls to specialized sub-agents (sales, support, scheduling) based on caller intent.
- **SIP trunking** — Direct SIP support beyond Twilio for enterprise PBX integration.
- **Voice cloning** — Clone a specific voice for your agent using Coqui XTTS v2 or ElevenLabs.
- **Conference calling** — Add the AI agent to multi-party calls as a participant.
- **Plugin system** — Community-contributed agent backends, voice providers, and integrations.

### Completed

- [x] Framework-agnostic agent backend architecture
- [x] Hermes Gateway deep integration (tools, skills, memory, named conversations)
- [x] 9 STT providers, 10+ TTS providers
- [x] Fully offline mode on Apple Silicon (mlx-whisper + Kokoro + Ollama)
- [x] Web dashboard with full settings management
- [x] macOS menu bar app with native settings panel
- [x] Linux/WSL2 installer with systemd service
- [x] Voicemail with transcription and Telegram notifications
- [x] Outbound calling from dashboard/API/menu bar
- [x] PIN-gated AI access (hidden from callers)
- [x] Provider auto-install and model discovery
- [x] Two-port security model
- [x] Voicemail export (ZIP and plain text)

---

## Contributing

**We need testers for non-Hermes backends.** If you use OpenAI, OpenRouter, Ollama, LM Studio, or any OpenAI-compatible provider, please try it and open an issue with your results. Every backend that works expands the project's reach.

**Voice provider testing** is also valuable — especially local providers on Linux (faster-whisper, piper) and non-Apple platforms.

Workflow:
1. Fork and create a feature branch
2. Make changes, test locally
3. Open a PR — CodeRabbit reviews automatically
4. Address feedback, merge

---

## Requirements

- Python 3.11+
- Twilio account (any country, any number type)
- One agent backend (Hermes Agent, OpenAI, Ollama, or any supported provider)

---

## License

**MIT License + [Commons Clause](https://commonsclause.com/)** — see [LICENSE](LICENSE).

Free to use, modify, and self-host. The Commons Clause adds a single restriction on top of MIT: you may **not Sell** the software — you can't charge for the software itself or offer it as a paid hosted/managed service whose value derives substantially from it. Everything else MIT permits is allowed. (Source-available; not an OSI-approved open-source license.)

---

## Credits

Built with [Hermes Agent](https://hermes-agent.nousresearch.com) by [JAN Labs](https://janlabs.co.uk).
