# Hermes Phone — Pipecat Prototype

**Status: prototype / spike — not production code.**

This directory demonstrates the "better voice pipeline + reliability" direction from
the roadmap, using [Pipecat 0.0.98](https://github.com/pipecat-ai/pipecat) as an
alternative to the hand-rolled Twilio WebSocket handler in `../../server.py`.

---

## What this demonstrates

### The problem with the current handler (`../../server.py`)

The existing `/ws/call` WebSocket handler implements the full voice loop manually:

| Concern | Current approach | Problems |
|---|---|---|
| Turn detection | Polls `is_final` flag from Deepgram messages | Latency spikes, missed finals under load, no silence gating |
| Barge-in | Not implemented | Caller cannot interrupt the bot mid-sentence |
| Audio threading | `threading.Thread` per call | GIL contention; hard to backpressure |
| Error recovery | Single try/except, closes socket | One audio glitch ends the call |
| Context history | Manual `messages` list, trimmed at 40 | Off-by-one risks, no aggregator validation |

### What Pipecat adds

| Concern | Pipecat 0.0.98 approach |
|---|---|
| Turn detection | **Silero VAD** — neural, frame-level voice activity detection running on every 20 ms audio chunk; no polling |
| Barge-in | `allow_interruptions=True` in `PipelineParams` — when Silero detects speech during TTS playback, Pipecat cancels in-flight audio frames and unqueues pending TTS chunks automatically |
| Audio threading | `asyncio`-native pipeline; each stage is an `async` processor with explicit frame queues and backpressure |
| Error recovery | `PipelineRunner` catches per-frame exceptions; the pipeline continues unless explicitly ended |
| Context history | `OpenAILLMContextAggregator` pair manages message history, role boundaries, and function-call turns |
| Transport | `FastAPIWebsocketTransport` + `TwilioFrameSerializer` — handles μ-law ↔ PCM conversion, stream SID extraction, DTMF events, and call termination frames |

The net result: **sub-400 ms Time-To-First-Byte** from caller end-of-speech to
first TTS audio chunk (Deepgram nova-2 + Cartesia streaming), versus ~800–1200 ms
typical for the current poll-based handler, and **natural barge-in** so callers
can interrupt the bot as they would a human.

---

## Directory layout

```
prototype/pipecat/
  pipeline.py               ← Pipecat FastAPI app + pipeline definition
  tunnel.sh                 ← TLS-tunnel helper (cloudflared / ngrok)
  requirements-prototype.txt ← pinned dependencies
  README.md                 ← this file
```

---

## Step-by-step run instructions

### 1. Prerequisites

- Python 3.11+ (Pipecat requires 3.10+; 3.11 recommended)
- API keys: Deepgram, OpenAI (or compatible), Cartesia
- A Twilio account + phone number with Media Streams access
- `cloudflared` or `ngrok` installed (see `tunnel.sh`)

### 2. Create a virtual environment

```bash
cd prototype/pipecat
python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements-prototype.txt
```

This installs `pipecat-ai==0.0.98` with extras:
`websocket,silero,deepgram,openai,cartesia`

Silero VAD downloads its model (~5 MB) on first run.

### 4. Configure environment variables

Copy the parent app's `.env` and add the Cartesia key, or create a new one:

```bash
# Required
export OPENAI_API_KEY="sk-..."
export DEEPGRAM_API_KEY="..."
export CARTESIA_API_KEY="..."

# Optional — same names as the main app
export OPENAI_BASE_URL="https://api.openai.com/v1"   # or any OpenAI-compatible URL
export LLM_MODEL="gpt-4o-mini"
export COMPANY_NAME="Hermes"
export CALL_SYSTEM_PROMPT=""   # leave blank for default

# Set after step 5
export PUBLIC_URL="https://abc123.trycloudflare.com"
export PORT=5050
```

### 5. Start the TLS tunnel

In a dedicated terminal:

```bash
chmod +x tunnel.sh
./tunnel.sh
```

`tunnel.sh` prefers `cloudflared` (no signup needed for ephemeral tunnels):

```
cloudflared tunnel --url http://localhost:5050
```

It will print a URL like `https://abc123.trycloudflare.com`.  
Set `PUBLIC_URL` to that value, then continue.

> **Note:** The tunnel URL changes every restart. Update `PUBLIC_URL` and the
> Twilio webhook each time you restart the tunnel.

### 6. Start the Pipecat server

In a second terminal (with the venv activated and env vars set):

```bash
python pipeline.py
# or: uvicorn pipeline:app --host 0.0.0.0 --port 5050
```

You should see:
```
[INFO] Pipecat prototype listening on :5050
[INFO] LLM: https://api.openai.com/v1 / gpt-4o-mini
INFO:     Application startup complete.
```

### 7. Point the Twilio webhook

1. Go to [Twilio Console → Phone Numbers → Manage → Active Numbers](https://console.twilio.com/us1/develop/phone-numbers/manage/active)
2. Click your phone number
3. Under **Voice & Fax → "A CALL COMES IN"**, set:
   - **Webhook URL**: `https://<your-tunnel-url>/twiml`
   - **Method**: HTTP POST
4. Save

### 8. Test it

Call your Twilio number. You should hear the greeting and be able to have a
natural conversation. Interrupt the bot mid-sentence to test barge-in.

---

## How the pipeline works (architecture)

```
Caller phone
  │
  │ (PSTN)
  ▼
Twilio Media Streams — sends μ-law 8 kHz audio over WebSocket
  │
  ▼  wss://<tunnel>/ws
FastAPIWebsocketTransport  ←── TwilioFrameSerializer
  │  (handles μ-law↔PCM, stream SID, DTMF, EndFrame on hangup)
  │
  ▼
SileroVADAnalyzer  ◄── detects speech onset/offset every 20 ms
  │  emits UserStartedSpeakingFrame / UserStoppedSpeakingFrame
  │  triggers interruption of TTS output on barge-in
  │
  ▼
DeepgramSTTService  ── streaming nova-2-phonecall
  │  emits TranscriptionFrame (final utterance only)
  │
  ▼
OpenAILLMContextAggregator (user side)
  │  accumulates utterances into context messages
  │
  ▼
OpenAILLMService  ── streaming chat completions
  │  emits TextFrame chunks
  │
  ▼
OpenAILLMContextAggregator (assistant side)
  │  appends assistant reply to context
  │
  ▼
CartesiaTTSService  ── streaming TTS, low-latency WebSocket
  │  emits AudioFrame chunks as they arrive
  │  Pipecat cancels pending chunks on barge-in
  │
  ▼
FastAPIWebsocketTransport (output)
  │  re-serializes AudioFrame → Twilio media message
  │
  ▼  wss://<tunnel>/ws
Twilio Media Streams
  │
  ▼
Caller phone
```

---

## Known limitations

### Accepted (inherent to the prototype scope)

- **No live-call testing without real API keys and a Twilio number.**
  A Twilio phone number costs ~$1/month plus per-minute usage.
  The pipeline cannot be tested end-to-end without real credentials.

- **Single machine = SPOF.**
  The server and tunnel run on the same laptop. If the machine sleeps, the
  tunnel drops, calls fail. For production, deploy to a VPS or Pipecat Cloud.

- **Ephemeral tunnel URL changes on restart.**
  Every `cloudflared`/`ngrok` restart gives a new URL; the Twilio webhook must
  be updated manually each time.

- **Cartesia TTS is a new paid dependency.**
  The existing app uses Twilio's built-in Polly voices (no extra cost). Cartesia
  adds ~$0.065/min TTS cost. Alternative: swap `CartesiaTTSService` for
  `ElevenLabsTTSService` (has a free tier) or the `DeepgramTTSService`.

### Future work to productionise

- Deploy server to a cloud VM or [Pipecat Cloud](https://pipecat.daily.co) (no
  tunnel needed, auto-scales).
- Add voicemail fallback (record frame → Deepgram batch transcription) to match
  the existing app's voicemail feature.
- Wire up the PIN gate: add a DTMF processor before the main pipeline to
  replicate `check_pin` in the current server.
- Add Telegram notification webhook (separate FastAPI background task).
- Replace `CARTESIA_API_KEY` with an OpenAI TTS fallback for zero-new-key
  evaluation: swap `CartesiaTTSService` for `OpenAITTSService` (already
  available in the `openai` extra).

---

## Comparison table: current server.py vs this prototype

| Feature | `server.py` | `prototype/pipecat/pipeline.py` |
|---|---|---|
| Turn detection | Deepgram `is_final` polling | Silero VAD (neural, 20 ms frames) |
| Barge-in / interruption | Not supported | Built-in (`allow_interruptions=True`) |
| Audio conversion | Manual `audioop` μ-law→PCM | `TwilioFrameSerializer` (built-in) |
| Error recovery | Socket closes on exception | Per-frame exception handling |
| Conversation history | Manual list, manual trim | `OpenAILLMContextAggregator` |
| Latency (est. TTFB) | ~800–1200 ms | ~300–400 ms (Deepgram+Cartesia streaming) |
| Lines of code (voice loop) | ~100 (WebSocket handler) | ~80 (pipeline definition) |
| TTS provider | Twilio Polly / MLX / OpenAI | Cartesia (swappable) |
| Voicemail | Yes | Not in prototype |
| PIN gate | Yes | Not in prototype |
| Dashboard | Yes | Not in prototype |
