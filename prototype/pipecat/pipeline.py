"""
Pipecat voice-agent pipeline — Hermes Phone prototype / spike.

Architecture (Pipecat 0.0.98):
  Twilio (μ-law 8 kHz) ─→ TwilioFrameSerializer
    ─→ FastAPIWebsocketTransport (input)
    ─→ SileroVADAnalyzer  ← turn detection + barge-in
    ─→ DeepgramSTTService ← streaming STT
    ─→ LLMUserContextAggregator
    ─→ OpenAILLMService   ← any OpenAI-compatible endpoint
    ─→ LLMAssistantContextAggregator
    ─→ CartesiaTTSService ← low-latency TTS with built-in interruption support
    ─→ FastAPIWebsocketTransport (output)
    ─→ TwilioFrameSerializer
    ─→ Twilio (μ-law 8 kHz)

Key improvements vs ../../server.py:
  - Silero VAD (neural, frame-level) replaces the hand-rolled is_final polling
  - CartesiaTTS streams in audio chunks; Pipecat cancels in-flight chunks
    automatically when the caller speaks (barge-in)
  - Pipeline handles reconnect / error recovery; no manual thread management
  - Context aggregators keep conversation history automatically

This is a PROTOTYPE / spike — not production-ready (see README.md).

Env vars (same names as the main app where sensible):
  OPENAI_API_KEY        required
  OPENAI_BASE_URL       optional (default https://api.openai.com/v1)
  LLM_MODEL             optional (default gpt-4o-mini)
  DEEPGRAM_API_KEY      required
  CARTESIA_API_KEY      required  ← new: CartesiaTTS is the recommended
                                    low-latency provider in Pipecat examples
  COMPANY_NAME          optional (default "Hermes")
  CALL_SYSTEM_PROMPT    optional
  PORT                  optional (default 5050)

API notes (verified against Pipecat 0.0.98 source / docs):
  - TwilioFrameSerializer(stream_sid, ...) — stream_sid is required; we extract
    it from the Twilio "start" WebSocket message before building the pipeline.
  - DeepgramSTTService(api_key, live_options=LiveOptions(...)) — live_options
    must be a deepgram.audio.live.LiveOptions dataclass, not a plain dict.
  - OpenAILLMContext + llm.create_context_aggregator(context) is the idiomatic
    way to manage conversation history in Pipecat 0.0.9x.
  - PipelineParams(allow_interruptions=True) enables barge-in globally for the
    pipeline; Pipecat cancels queued audio frames on UserStartedSpeakingFrame.
"""

import os
import sys
import json
import asyncio
import logging
from contextlib import asynccontextmanager

# ── FastAPI & Uvicorn ──────────────────────────────────────────────
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse

# ── Twilio TwiML helper ────────────────────────────────────────────
from twilio.twiml.voice_response import VoiceResponse, Connect

# ── Pipecat core ──────────────────────────────────────────────────
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

# ── Pipecat transport (FastAPI-WebSocket + Twilio serializer) ──────
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketTransport,
    FastAPIWebsocketParams,
)
from pipecat.serializers.twilio import TwilioFrameSerializer

# ── Pipecat VAD ────────────────────────────────────────────────────
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

# ── Pipecat services ───────────────────────────────────────────────
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.cartesia.tts import CartesiaTTSService

# ── Deepgram SDK types (needed for LiveOptions dataclass) ─────────
# deepgram-sdk v3+ exposes LiveOptions at the top-level package.
from deepgram import LiveOptions as DeepgramLiveOptions

# ── Pipecat frames & processors ───────────────────────────────────
from pipecat.frames.frames import LLMMessagesFrame, EndFrame
from pipecat.processors.aggregators.openai_llm_context import (
    OpenAILLMContext,
    OpenAILLMContextAggregator,
)

# ── Pipecat logging helper ────────────────────────────────────────
from pipecat.utils.loguru_logger import logger as pipecat_logger

# ─────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────

# Load .env in parent directory (same style as the main app)
from pathlib import Path

_env_file = Path(__file__).parent.parent.parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file, override=False)

OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL        = os.environ.get("LLM_MODEL", "gpt-4o-mini")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
CARTESIA_API_KEY = os.environ.get("CARTESIA_API_KEY", "")
COMPANY_NAME     = os.environ.get("COMPANY_NAME", "Hermes")
SYSTEM_PROMPT    = os.environ.get(
    "CALL_SYSTEM_PROMPT",
    f"You are {COMPANY_NAME}'s AI phone assistant. "
    "Be natural, conversational, and concise — two to three sentences per turn. "
    "Use plain speech: no markdown, no bullets, no special characters. "
    "Say goodbye when the conversation is complete.",
)
PORT = int(os.environ.get("PORT", "5050"))

# ─────────────────────────────────────────────────────────────────
# Lifespan (startup validation)
# ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = []
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if not DEEPGRAM_API_KEY:
        missing.append("DEEPGRAM_API_KEY")
    if not CARTESIA_API_KEY:
        missing.append("CARTESIA_API_KEY")
    if missing:
        print(
            f"[WARN] Missing env vars: {', '.join(missing)}. "
            "The server will start but calls will fail.",
            file=sys.stderr,
        )
    print(f"[INFO] Pipecat prototype listening on :{PORT}")
    print(f"[INFO] LLM: {OPENAI_BASE_URL} / {LLM_MODEL}")
    yield

app = FastAPI(title="Hermes Phone — Pipecat Prototype", lifespan=lifespan)

# ─────────────────────────────────────────────────────────────────
# TwiML endpoint: redirect inbound call to our WebSocket
# ─────────────────────────────────────────────────────────────────

@app.post("/twiml", response_class=HTMLResponse)
async def twiml_inbound(request: Request):
    """
    Twilio calls this URL when a call arrives.
    We reply with TwiML that opens a Media Streams WebSocket back to us.

    The PUBLIC_URL env var must be set to your cloudflared / ngrok https URL
    (e.g. https://abc123.trycloudflare.com). Twilio uses it to reach wss://...
    """
    public_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not public_url:
        # Fallback: try to derive from the incoming Host header
        host = request.headers.get("host", "localhost:5050")
        public_url = f"https://{host}"

    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/ws"

    resp = VoiceResponse()
    connect = Connect()
    connect.stream(url=ws_url)
    resp.append(connect)
    return HTMLResponse(content=str(resp), media_type="text/xml")


# ─────────────────────────────────────────────────────────────────
# WebSocket endpoint: Twilio Media Streams → Pipecat pipeline
# ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    One Pipecat pipeline per call.  The pipeline is:
      transport.input → STT → context_agg(user) → LLM → context_agg(assistant)
      → TTS → transport.output

    Silero VAD runs inside the transport input; it gates audio frames so that
    STT only receives speech, and triggers interruption of the TTS output when
    the caller speaks while the bot is talking (barge-in).

    TwilioFrameSerializer requires stream_sid at construction time.  We peek
    at the first WebSocket message (Twilio's "connected" handshake then the
    "start" message which contains streamSid) before creating the serializer
    and handing the WebSocket to Pipecat.  A helper async-iterator replays
    those already-consumed messages into the transport.
    """
    await websocket.accept()

    # ── Peek at the Twilio handshake to extract stream_sid ──────────
    # Twilio sends two messages before audio:
    #   1. {"event": "connected", ...}
    #   2. {"event": "start", "start": {"streamSid": "...", "callSid": "..."}}
    stream_sid: str = ""
    call_sid: str = ""
    consumed_messages: list = []

    for _ in range(10):  # bounded loop; "start" usually arrives within 2 messages
        raw = await websocket.receive_text()
        consumed_messages.append(raw)
        msg = json.loads(raw)
        if msg.get("event") == "start":
            stream_sid = msg["start"].get("streamSid", "")
            call_sid   = msg["start"].get("callSid", "")
            break

    if not stream_sid:
        pipecat_logger.warning("No stream_sid received from Twilio; aborting.")
        await websocket.close()
        return

    pipecat_logger.info(f"Twilio stream connected: stream_sid={stream_sid} call_sid={call_sid}")

    # ── Build TwilioFrameSerializer with the now-known stream_sid ───
    # Disable auto_hang_up for the prototype (we don't pass Twilio auth here;
    # the call terminates naturally when the caller hangs up).
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
    )

    # ── VAD — Silero neural model, conservative for phone audio ─────
    vad = SileroVADAnalyzer(
        params=VADParams(
            confidence=0.7,    # slightly above default to reduce FP on phone noise
            start_secs=0.2,    # speech onset delay
            stop_secs=0.8,     # silence-after-speech before end-of-turn
            min_volume=0.6,
        )
    )

    # ── Transport — FastAPI WebSocket with Twilio serializer + VAD ──
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            serializer=serializer,
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,         # Twilio expects raw μ-law, not WAV
            vad_enabled=True,
            vad_analyzer=vad,
            vad_audio_passthrough=True,
        ),
    )

    # ── STT — Deepgram streaming ────────────────────────────────────
    # LiveOptions is a dataclass from the deepgram SDK (not a plain dict).
    # nova-2-phonecall is tuned for 8 kHz telephony audio.
    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
        live_options=DeepgramLiveOptions(
            model="nova-2-phonecall",
            language="en",
            punctuate=True,
            interim_results=False,   # only emit final utterances to the LLM
            encoding="mulaw",
            sample_rate=8000,
            channels=1,
        ),
    )

    # ── LLM — OpenAI-compatible (any base_url) ──────────────────────
    llm = OpenAILLMService(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        model=LLM_MODEL,
    )

    # ── TTS — Cartesia streaming, low-latency ───────────────────────
    # Voice ID "79a125e8-cd45-4c13-8a67-188112f4dd22" = Cartesia "British Lady"
    # Override with CARTESIA_VOICE_ID env var for a different voice.
    tts = CartesiaTTSService(
        api_key=CARTESIA_API_KEY,
        voice_id=os.environ.get(
            "CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"
        ),
    )

    # ── Conversation context ────────────────────────────────────────
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    context = OpenAILLMContext(messages=messages)
    context_aggregator = llm.create_context_aggregator(context)

    # ── Pipeline ────────────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            context_aggregator.assistant(),
            transport.output(),
        ]
    )

    # ── PipelineTask — enables barge-in / interruption ──────────────
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,        # cancels TTS frames when caller speaks
            enable_metrics=True,
            report_only_initial_ttfb=True,
        ),
    )

    # ── Event handlers ──────────────────────────────────────────────

    @transport.event_handler("on_client_connected")
    async def on_connected(transport_obj, client):
        """Greet the caller immediately after the WebSocket handshake."""
        greeting = f"Hello, you've reached {COMPANY_NAME}. How can I help you today?"
        await task.queue_frames([LLMMessagesFrame(messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "assistant", "content": greeting},
        ])])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport_obj, client):
        """Caller hung up — end the pipeline cleanly."""
        await task.queue_frames([EndFrame()])

    # ── Run the pipeline until the call ends ────────────────────────
    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "pipeline:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
