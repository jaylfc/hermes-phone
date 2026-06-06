# 📞 Hermes Phone

**AI-powered phone agent for macOS — voicemail, AI conversations, and call management from your menu bar.**

Turn your Mac into an intelligent phone system. Incoming calls get an AI assistant or voicemail. Outgoing calls are powered by any OpenAI-compatible LLM. Everything managed from a clean macOS menu bar app.

![macOS](https://img.shields.io/badge/macOS-13%2B-blue)
![Python](https://img.shields.io/badge/Python-3.11%2B-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ Features

- **📞 AI Phone Agent** — Incoming calls connect to any OpenAI-compatible LLM (GPT-4, Claude, MiMo, local models)
- **🔒 PIN-Gated Access** — Hidden PIN bypass lets you reach the AI while others get voicemail
- **🎙️ Voicemail Manager** — Menu bar app shows voicemails with transcripts, playback, delete, and callback
- **📤 Outbound Calls** — Make AI-powered calls from the menu bar or API
- **🌍 Works Worldwide** — Any Twilio number (US, UK, EU, AU, and 180+ countries)
- **🚀 One-Click Install** — `curl -sSL ... | bash` sets up everything

## 📋 Requirements

- macOS 13+ (Ventura or later)
- Python 3.11+
- [Twilio](https://twilio.com) account + phone number
- [Deepgram](https://console.deepgram.com) API key (free $200 credit)
- Any OpenAI-compatible LLM API key

## 🚀 Quick Start

```bash
# Install
git clone https://github.com/jaylfc/hermes-phone.git
cd hermes-phone
./install.sh

# Or one-liner:
curl -sSL https://raw.githubusercontent.com/jaylfc/hermes-phone/main/install.sh | bash
```

The installer will:
1. Install Python dependencies
2. Ask for your API keys (Twilio, Deepgram, LLM)
3. Configure your phone number webhook
4. Install macOS LaunchAgents (auto-start on login)
5. Add a 📞 menu bar app to your system

## ⚙️ Configuration

Edit `~/.hermes-phone/.env`:

```bash
# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+443xxxxxxxxx

# Deepgram (STT)
DEEPGRAM_API_KEY=your_deepgram_key

# LLM (any OpenAI-compatible API)
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o

# Phone Agent
VOICEMAIL_PIN=1234
COMPANY_NAME=Your Company
VOICEMAIL_EMAIL=hello@yourcompany.com
```

## 📞 How It Works

### Incoming Calls
```
Caller → Twilio → Your Mac
  │
  ├─ Enters PIN (1234#) → AI conversation (STT → LLM → TTS)
  │
  └─ No PIN → Greeting → Beep → Voicemail recording
       → Transcribed → Sent to menu bar + Telegram (optional)
```

### Outgoing Calls
```
Menu Bar / API → Twilio → Recipient
  │
  └─ Connected → AI conversation (STT → LLM → TTS)
```

## 🖥️ Menu Bar App

The 📞 menu bar icon gives you:

- **Status indicator** — 🟢 running / 🔴 stopped
- **Start/Stop/Restart** — Control the phone agent server
- **Voicemail Manager** — View, play, delete voicemails with transcripts
- **Make Call** — Start an outbound AI call
- **Logs** — View server logs

## 🔌 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/call` | POST | Make outbound call `{"to": "+447xxx", "goal": "..."}` |
| `/voicemails` | GET | List all voicemails |
| `/health` | GET | Server health check |

## 🌐 Network Setup

### Option A: Port Forwarding (Recommended)
Forward port `5050` to your Mac's local IP. Point your Twilio webhook to:
```
http://YOUR_STATIC_IP:5050/voice/incoming
```

### Option B: ngrok (Quick Setup)
```bash
brew install ngrok
ngrok config add-authtoken YOUR_TOKEN
ngrok http 5050
```
Point Twilio webhook to the ngrok URL.

## 🤖 AI Provider Support

Works with any OpenAI-compatible API:

| Provider | Base URL | Notes |
|----------|----------|-------|
| OpenAI | `https://api.openai.com/v1` | GPT-4o, GPT-4-mini |
| Anthropic (via proxy) | `https://api.anthropic.com/v1` | Claude |
| Xiaomi MiMo | `https://token-plan-ams.xiaomimimo.com/v1` | Free tier available |
| Ollama | `http://localhost:11434/v1` | Local models |
| vLLM | `http://localhost:8000/v1` | Self-hosted |
| OpenRouter | `https://openrouter.ai/api/v1` | 100+ models |

## 📁 File Locations

```
~/.hermes-phone/
├── .env              # API keys & config
├── server.py         # VoIP server
├── menubar.py        # Menu bar app
├── server.log        # Server logs
└── voicemails/       # Recorded voicemails
    ├── audio/        # WAV files
    └── metadata.json # Transcripts & metadata
```

## 🛠️ Development

```bash
# Clone and setup
git clone https://github.com/jaylfc/hermes-phone.git
cd hermes-phone
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run server
python3 server.py

# Run menu bar app
python3 menubar.py
```

## 📄 License

MIT — see [LICENSE](LICENSE)

## 🙏 Built With

- [Twilio](https://twilio.com) — Voice infrastructure
- [Deepgram](https://deepgram.com) — Speech-to-text
- [Flask](https://flask.palletsprojects.com) — Web server
- [rumps](https://github.com/jaredks/rumps) — macOS menu bar framework

---

**Made by [JAN Labs](https://janlabs.co.uk)**

## 🍎 Local Mode (Apple Silicon)

Hermes Phone can run **100% offline** on Apple Silicon Macs using MLX-optimized models:

| Component | Model | Size | Speed |
|-----------|-------|------|-------|
| **STT** | mlx-whisper (large-v3-turbo) | ~1.6GB | Real-time |
| **TTS** | mlx-audio (Kokoro-82M 4-bit) | ~50MB | 2-3x real-time |
| **LLM** | Any OpenAI-compatible (Ollama, vLLM) | varies | varies |

### Enable local mode

```bash
# In .env:
USE_LOCAL_VOICE=auto  # auto-detect, fall back to cloud

# Or install manually:
pip install mlx-whisper mlx-audio

# Models download automatically on first use
```

### Fully offline setup

```bash
# STT + TTS: local MLX
USE_LOCAL_VOICE=true

# LLM: local via Ollama
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3
```

Zero API costs. Zero cloud dependency. Works on a plane.
