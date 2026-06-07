"""
Hermes Phone — AI-powered VoIP server for macOS.

Architecture:
  Twilio (audio) → WebSocket → Deepgram (STT) → LLM (OpenAI-compatible) → TTS → Twilio (playback)

Incoming calls: Greeting → PIN gate → AI conversation or voicemail
Outgoing calls: POST /call → Twilio → AI conversation
Voicemail: Record → Transcribe → Store locally + optional Telegram notification
"""

import os
import sys
import json
import base64
import time
import audioop
import secrets
import threading
import tempfile
import subprocess
from functools import wraps
from pathlib import Path
from datetime import datetime

import requests as http_requests
from flask import Flask, request, Response, jsonify
from flask_sock import Sock
from werkzeug.middleware.proxy_fix import ProxyFix
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Connect, Gather
from openai import OpenAI

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

# Load .env file
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), value)

# Twilio
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.environ.get("TWILIO_PHONE_NUMBER", "")

# Deepgram (STT)
DEEPGRAM_KEY = os.environ.get("DEEPGRAM_API_KEY", "")

# LLM Provider
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
XIAOMI_KEY = os.environ.get("XIAOMI_API_KEY", "")
XIAOMI_BASE_URL = os.environ.get("XIAOMI_BASE_URL", "https://token-plan-ams.xiaomimimo.com/v1")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Phone Agent
VOICEMAIL_PIN = os.environ.get("VOICEMAIL_PIN", "1234")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "My Company")
VOICEMAIL_EMAIL = os.environ.get("VOICEMAIL_EMAIL", "")
VOICEMAIL_MAX_LENGTH = int(os.environ.get("VOICEMAIL_MAX_LENGTH", "120"))
VOICEMAIL_GREETING = os.environ.get("VOICEMAIL_GREETING", "")
TTS_VOICE = os.environ.get("TTS_VOICE", "Polly.Amy")
TTS_LANGUAGE = os.environ.get("TTS_LANGUAGE", "en-GB")
CALL_GOAL = os.environ.get("CALL_GOAL", "Have a helpful conversation.")
SYSTEM_PROMPT = os.environ.get("CALL_SYSTEM_PROMPT", "")

# Telegram (optional)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Security / networking
API_TOKEN = os.environ.get("HERMES_API_TOKEN", "")
SESSION_COOKIE = "hermes_session"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")  # e.g. https://abc.ngrok.app
DEBUG = os.environ.get("HERMES_DEBUG", "false").lower() in ("1", "true", "yes")
VALIDATE_TWILIO = os.environ.get("VALIDATE_TWILIO_SIGNATURE", "true").lower() in ("1", "true", "yes")
HOST = os.environ.get("HERMES_HOST", "0.0.0.0")
PORT = int(os.environ.get("HERMES_PORT", "5050"))
PIN_MAX_ATTEMPTS = int(os.environ.get("PIN_MAX_ATTEMPTS", "5"))
PIN_LOCKOUT_WINDOW = int(os.environ.get("PIN_LOCKOUT_WINDOW", "600"))

# Cloud TTS (OpenAI) — fallback when local/MiMo voice is unavailable
OPENAI_TTS_BASE_URL = os.environ.get("OPENAI_TTS_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_TTS_KEY = os.environ.get("OPENAI_TTS_API_KEY", OPENAI_KEY)
OPENAI_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "tts-1")
OPENAI_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy")

# Data directory
DATA_DIR = Path(__file__).parent / "voicemails"
AUDIO_DIR = DATA_DIR / "audio"
METADATA_FILE = DATA_DIR / "metadata.json"

# ═══════════════════════════════════════════════════════════════════
# Initialize clients
# ═══════════════════════════════════════════════════════════════════

# Ensure data directories exist
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# LLM client — try providers in order: Xiaomi → OpenRouter → OpenAI
llm_client = None
llm_base_url = OPENAI_BASE_URL
llm_api_key = OPENAI_KEY

if LLM_PROVIDER == "xiaomi" and XIAOMI_KEY:
    llm_client = OpenAI(base_url=XIAOMI_BASE_URL, api_key=XIAOMI_KEY)
    llm_base_url = XIAOMI_BASE_URL
    llm_api_key = XIAOMI_KEY
elif OPENROUTER_KEY:
    llm_client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_KEY)
    llm_base_url = OPENROUTER_BASE_URL
    llm_api_key = OPENROUTER_KEY
elif OPENAI_KEY:
    llm_client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_KEY)
    llm_base_url = OPENAI_BASE_URL
    llm_api_key = OPENAI_KEY

# ═══════════════════════════════════════════════════════════════════
# Local Voice Engine (MLX on Apple Silicon)
# ═══════════════════════════════════════════════════════════════════

USE_LOCAL_VOICE = os.environ.get("USE_LOCAL_VOICE", "auto").lower()

voice_engine = None
if USE_LOCAL_VOICE in ("auto", "true", "1"):
    try:
        from local_voice import VoiceEngine
        prefer_local = USE_LOCAL_VOICE != "false"
        voice_engine = VoiceEngine(prefer_local=prefer_local)
        print(f"  Voice: {voice_engine.mode}")
    except Exception as e:
        if USE_LOCAL_VOICE == "true":
            print(f"  ❌ Local voice failed: {e}")
        else:
            print(f"  ℹ️ Local voice not available, using cloud TTS")

# Deepgram client (fallback STT if local not available)
dg_client = None
if DEEPGRAM_KEY and (not voice_engine or not voice_engine.stt):
    try:
        from deepgram import DeepgramClient
        dg_client = DeepgramClient(api_key=DEEPGRAM_KEY)
    except ImportError:
        print("⚠️ Deepgram SDK not installed — STT disabled")

# Flask app
app = Flask(__name__)
# Honour X-Forwarded-Proto/Host from a single trusted tunnel/proxy (ngrok, etc.)
# so signature validation and generated URLs reflect the real public address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
sock = Sock(app)

# Twilio webhook signature validator (uses the account Auth Token)
twilio_validator = RequestValidator(TWILIO_TOKEN) if TWILIO_TOKEN else None

# Call state (in-memory)
call_states = {}

# PIN brute-force tracking: caller -> (fail_count, window_start_ts)
pin_attempts = {}

# Active browser sessions: session_id -> created_ts (in-memory; cleared on restart).
# The cookie holds an opaque random id (not the token) so sessions are revocable.
sessions = {}
SESSION_TTL = 30 * 24 * 3600

# Serialises read-modify-write on the voicemail metadata file
_vm_lock = threading.RLock()


# ═══════════════════════════════════════════════════════════════════
# Auth & request helpers
# ═══════════════════════════════════════════════════════════════════

def _request_via_proxy():
    """True if the request arrived through a tunnel/reverse proxy (e.g. ngrok)."""
    return bool(
        request.headers.get("X-Forwarded-For")
        or request.headers.get("X-Forwarded-Host")
        or request.headers.get("X-Forwarded-Proto")
    )


def _token_ok():
    """API clients authenticate with a header token (never a query string)."""
    if not API_TOKEN:
        return False
    sent = (
        request.headers.get("X-Hermes-Token", "")
        or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    )
    return bool(sent) and secrets.compare_digest(sent, API_TOKEN)


def _cookie_ok():
    """Browsers authenticate with an HttpOnly session cookie holding an opaque id."""
    sid = request.cookies.get(SESSION_COOKIE, "")
    if not sid:
        return False
    created = sessions.get(sid)
    if created is None:
        return False
    if time.time() - created > SESSION_TTL:
        sessions.pop(sid, None)
        return False
    return True


def _is_local():
    return not _request_via_proxy() and request.remote_addr in ("127.0.0.1", "::1")


def require_auth(f):
    """Protect the control plane: trust direct localhost, else require auth.

    The menu bar and a local browser hit the server directly on 127.0.0.1 and
    are trusted. Anything arriving through a tunnel/proxy, or from a remote host,
    must present a header token (API clients) or the session cookie (browsers,
    obtained by signing in at /login with HERMES_API_TOKEN). Tokens are never
    accepted in the URL, so they don't leak into logs or browser history.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if _is_local() or _token_ok() or _cookie_ok():
            return f(*args, **kwargs)
        if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
            return Response(LOGIN_HTML, mimetype="text/html", status=401)
        return jsonify({"error": "unauthorized"}), 401
    return wrapper


def require_twilio(f):
    """Reject webhook requests that aren't signed by Twilio."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if VALIDATE_TWILIO and twilio_validator is not None:
            signature = request.headers.get("X-Twilio-Signature", "")
            url = (PUBLIC_URL + request.path) if PUBLIC_URL else request.url
            if not twilio_validator.validate(url, request.form.to_dict(), signature):
                print(f"⛔ Invalid Twilio signature for {request.path}")
                return Response("Invalid signature", status=403)
        return f(*args, **kwargs)
    return wrapper


def public_base():
    """Public origin for building callback URLs (PUBLIC_URL or the request host)."""
    return PUBLIC_URL if PUBLIC_URL else f"{request.scheme}://{request.host}"


def public_ws_url(path):
    """Twilio Media Streams require wss://; derive it from the public origin."""
    return "wss://" + public_base().split("://", 1)[-1] + path


def _pin_locked(caller):
    """True if this caller has exhausted PIN attempts within the lockout window."""
    rec = pin_attempts.get(caller)
    if not rec:
        return False
    count, first = rec
    if time.time() - first > PIN_LOCKOUT_WINDOW:
        pin_attempts.pop(caller, None)
        return False
    return count >= PIN_MAX_ATTEMPTS


def _record_pin_fail(caller):
    count, first = pin_attempts.get(caller, (0, time.time()))
    if time.time() - first > PIN_LOCKOUT_WINDOW:
        count, first = 0, time.time()
    pin_attempts[caller] = (count + 1, first)


# Voicemail metadata
def load_voicemails():
    if METADATA_FILE.exists():
        return json.loads(METADATA_FILE.read_text())
    return []

def save_voicemails(voicemails):
    METADATA_FILE.write_text(json.dumps(voicemails, indent=2))

# ═══════════════════════════════════════════════════════════════════
# System prompt
# ═══════════════════════════════════════════════════════════════════

def get_system_prompt(goal=None):
    if SYSTEM_PROMPT:
        return SYSTEM_PROMPT
    goal = goal or CALL_GOAL
    return f"""You are {COMPANY_NAME}'s AI phone assistant.

GOAL: {goal}

Rules:
- Be natural, conversational, and concise (2-3 sentences max per turn)
- Listen carefully and respond appropriately
- Work toward your goal but don't be pushy
- If the person seems busy, politely end the call
- Use natural speech (no markdown, no bullets, no special characters)
- Say goodbye when the goal is achieved"""

# ═══════════════════════════════════════════════════════════════════
# LLM
# ═══════════════════════════════════════════════════════════════════

def get_llm_response(call_sid, user_text):
    """Get response from LLM via OpenAI-compatible API."""
    if not llm_client:
        return "Sorry, I'm having technical difficulties."

    state = call_states.setdefault(call_sid, {"messages": [], "transcript": []})
    state["transcript"].append({"role": "user", "text": user_text})

    messages = [{"role": "system", "content": get_system_prompt(state.get("goal"))}]
    messages.extend(state["messages"])
    messages.append({"role": "user", "content": user_text})

    try:
        resp = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=500,
            temperature=0.7,
        )
        reply = resp.choices[0].message.content.strip()

        state["messages"].append({"role": "user", "content": user_text})
        state["messages"].append({"role": "assistant", "content": reply})
        state["transcript"].append({"role": "assistant", "text": reply})

        # Keep conversation history manageable
        if len(state["messages"]) > 40:
            state["messages"] = state["messages"][-40:]

        return reply
    except Exception as e:
        print(f"LLM error: {e}")
        return "I'm sorry, I missed that. Could you repeat?"

# ═══════════════════════════════════════════════════════════════════
# TTS
# ═══════════════════════════════════════════════════════════════════

def synthesize_speech(text):
    """Convert text to audio using the best available TTS."""
    # Try local MLX TTS first (free, fast, offline)
    if voice_engine and voice_engine.tts:
        audio = voice_engine.speak(text)
        if audio:
            return audio

    # Try MiMo TTS (if using Xiaomi)
    if LLM_PROVIDER == "xiaomi" and llm_client:
        try:
            resp = llm_client.chat.completions.create(
                model="mimo-v2.5-tts",
                messages=[{"role": "assistant", "content": text}],
                audio={"format": "pcm16", "voice": "Mia"},
            )
            audio_b64 = resp.choices[0].message.get("audio", {}).get("data")
            if audio_b64:
                pcm_24k = base64.b64decode(audio_b64)
                pcm_8k, _ = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, None)
                return pcm_8k
        except Exception as e:
            print(f"MiMo TTS error: {e}")

    # Fallback: OpenAI TTS (if a key is available).
    # Request raw PCM (24 kHz, 16-bit, mono) and resample to 8 kHz for Twilio —
    # no MP3/ffmpeg round-trip needed.
    if OPENAI_TTS_KEY:
        try:
            resp = http_requests.post(
                f"{OPENAI_TTS_BASE_URL}/audio/speech",
                headers={"Authorization": f"Bearer {OPENAI_TTS_KEY}"},
                json={
                    "model": OPENAI_TTS_MODEL,
                    "input": text,
                    "voice": OPENAI_TTS_VOICE,
                    "response_format": "pcm",
                },
                timeout=30,
            )
            if resp.status_code == 200 and resp.content:
                pcm_8k, _ = audioop.ratecv(resp.content, 2, 1, 24000, 8000, None)
                return pcm_8k
            print(f"OpenAI TTS error: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            print(f"OpenAI TTS error: {e}")

    return None

# ═══════════════════════════════════════════════════════════════════
# Audio playback
# ═══════════════════════════════════════════════════════════════════

def send_audio_to_ws(ws, stream_sid, audio_data):
    """Send synthesized audio back to Twilio via WebSocket."""
    try:
        # Skip WAV header if present
        if audio_data[:4] == b"RIFF":
            audio_data = audio_data[44:]

        mulaw = audioop.lin2ulaw(audio_data, 2)

        chunk_size = 160  # 20ms at 8kHz
        for i in range(0, len(mulaw), chunk_size):
            chunk = mulaw[i:i + chunk_size]
            if len(chunk) < chunk_size:
                chunk += b"\xff" * (chunk_size - len(chunk))
            ws.send(json.dumps({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": base64.b64encode(chunk).decode()},
            }))

        ws.send(json.dumps({
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": f"resp_{int(time.time())}"},
        }))
        print(f"🔊 Sent {len(mulaw)} bytes audio")
    except Exception as e:
        print(f"Audio send error: {e}")

# ═══════════════════════════════════════════════════════════════════
# Deepgram (prerecorded transcription)
# ═══════════════════════════════════════════════════════════════════

def _deepgram_transcribe(audio_bytes):
    """Transcribe audio bytes with Deepgram (SDK v7 prerecorded API). Returns text or ""."""
    if not dg_client:
        return ""
    response = dg_client.listen.v1.media.transcribe_file(
        request=audio_bytes,
        model="nova-2",
        language="en",
        punctuate=True,
        smart_format=True,
    )
    try:
        return response.results.channels[0].alternatives[0].transcript or ""
    except (AttributeError, IndexError, KeyError, TypeError):
        return ""


# ═══════════════════════════════════════════════════════════════════
# Telegram notifications
# ═══════════════════════════════════════════════════════════════════

def notify_telegram(recording_url, caller, duration, transcription=""):
    """Download voicemail and send to Telegram with transcript."""
    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        # Download recording from Twilio
        audio_url = f"{recording_url}.wav"
        r = http_requests.get(audio_url, auth=(TWILIO_SID, TWILIO_TOKEN), timeout=30)
        if r.status_code != 200:
            print(f"❌ Failed to download recording: {r.status_code}")
            return

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(r.content)
            wav_path = f.name

        # Transcribe with Deepgram if no Twilio transcript
        transcript_text = transcription
        if not transcript_text and dg_client:
            try:
                with open(wav_path, "rb") as audio_file:
                    buffer_data = audio_file.read()
                transcript_text = _deepgram_transcribe(buffer_data)
            except Exception as e:
                print(f"⚠️ Deepgram transcription failed: {e}")

        # Format caption
        caller_display = caller.replace("+", "")
        caption = f"📞 Voicemail from {caller_display}\n⏱️ {duration}s"
        if transcript_text:
            caption += f'\n\n📝 "{transcript_text}"'

        # Send to Telegram
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
        with open(wav_path, "rb") as audio_file:
            resp = http_requests.post(
                tg_url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"voice": ("voicemail.wav", audio_file, "audio/wav")},
                timeout=30,
            )

        if resp.status_code == 200:
            print(f"✅ Voicemail sent to Telegram")
        else:
            print(f"❌ Telegram send failed: {resp.status_code}")

        # Cleanup
        try:
            os.unlink(wav_path)
        except:
            pass

    except Exception as e:
        print(f"❌ Telegram notification error: {e}")

# ═══════════════════════════════════════════════════════════════════
# Twilio Webhooks
# ═══════════════════════════════════════════════════════════════════

@app.route("/voice/incoming", methods=["POST"])
@require_twilio
def handle_incoming():
    """Handle incoming call — greeting with PIN capture."""
    call_sid = request.form.get("CallSid", "unknown")
    caller = request.form.get("From", "unknown")
    print(f"📞 Incoming: {call_sid} from {caller}")

    resp = VoiceResponse()

    # Build greeting — use custom or default
    if VOICEMAIL_GREETING:
        greeting = VOICEMAIL_GREETING
    else:
        greeting = f"Thank you for calling {COMPANY_NAME}. "
        greeting += "Please leave a message after the tone."
        if VOICEMAIL_EMAIL:
            greeting += f" Or email us at {VOICEMAIL_EMAIL}."
        else:
            greeting += ""

    # Play greeting while listening for PIN
    gather = Gather(
        num_digits=len(VOICEMAIL_PIN),
        action="/voice/check-pin",
        method="POST",
        timeout=1,
        finish_on_key="#",
    )
    gather.say(greeting, voice=TTS_VOICE, language=TTS_LANGUAGE)
    resp.append(gather)

    # If no PIN entered → beep and record voicemail
    resp.record(
        action="/voice/voicemail-complete",
        method="POST",
        max_length=VOICEMAIL_MAX_LENGTH,
        play_beep=True,
        finish_on_key="#",
        recording_status_callback="/voice/recording-ready",
        recording_status_callback_method="POST",
    )

    resp.say("Goodbye.", voice=TTS_VOICE, language=TTS_LANGUAGE)
    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/check-pin", methods=["POST"])
@require_twilio
def check_pin():
    """Check if entered PIN matches (rate-limited per caller, constant-time)."""
    digits = request.form.get("Digits", "")
    call_sid = request.form.get("CallSid", "unknown")
    caller = request.form.get("From", "unknown")

    resp = VoiceResponse()

    locked = _pin_locked(caller)
    correct = (
        not locked
        and bool(VOICEMAIL_PIN)
        and secrets.compare_digest(digits, VOICEMAIL_PIN)
    )

    if correct:
        print(f"✅ PIN correct — connecting {caller} to AI")
        pin_attempts.pop(caller, None)
        resp.say("Connecting you now.", voice=TTS_VOICE, language=TTS_LANGUAGE)
        connect = Connect()
        connect.stream(url=public_ws_url("/ws/call"))
        resp.append(connect)
    else:
        # Wrong/locked-out PIN → voicemail (no hint that a PIN exists)
        if not locked:
            _record_pin_fail(caller)
        print(f"❌ PIN rejected for {caller}")
        resp.say(
            "Please leave a message after the tone. Press hash when finished.",
            voice=TTS_VOICE, language=TTS_LANGUAGE,
        )
        resp.record(
            action="/voice/voicemail-complete",
            method="POST",
            max_length=VOICEMAIL_MAX_LENGTH,
            play_beep=True,
            finish_on_key="#",
            recording_status_callback="/voice/recording-ready",
            recording_status_callback_method="POST",
        )

    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/voicemail-complete", methods=["POST"])
@require_twilio
def voicemail_complete():
    """After voicemail recording is done."""
    call_sid = request.form.get("CallSid", "unknown")
    recording_url = request.form.get("RecordingUrl", "")
    recording_sid = request.form.get("RecordingSid", "")
    duration = request.form.get("RecordingDuration", "0")
    caller = request.form.get("From", "unknown")

    print(f"📩 Voicemail from {caller}: {duration}s, SID={recording_sid}")

    # Store voicemail metadata (serialised against the recording-ready worker)
    with _vm_lock:
        voicemails = load_voicemails()
        voicemails.append({
            "sid": recording_sid,
            "from": caller,
            "duration": int(duration or 0),
            "url": f"{recording_url}.wav",
            "time": datetime.now().isoformat(),
            "timestamp": time.time(),
            "transcript": "",
            "read": False,
        })
        save_voicemails(voicemails)

    resp = VoiceResponse()
    resp.say("Thank you for your message. Goodbye.", voice=TTS_VOICE, language=TTS_LANGUAGE)
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/recording-ready", methods=["POST"])
@require_twilio
def recording_ready():
    """Called when a voicemail recording is fully processed."""
    recording_sid = request.form.get("RecordingSid", "")
    recording_url = request.form.get("RecordingUrl", "")
    caller = request.form.get("From", "unknown")
    duration = request.form.get("RecordingDuration", "0")
    transcription_text = request.form.get("TranscriptionText", "")

    print(f"🎙️ Recording ready: {recording_sid}")

    # Download and save locally
    thread = threading.Thread(
        target=_process_voicemail,
        args=(recording_sid, recording_url, caller, duration, transcription_text),
        daemon=True,
    )
    thread.start()

    return "", 204


def _process_voicemail(recording_sid, recording_url, caller, duration, transcription_text):
    """Download, transcribe, and store voicemail."""
    try:
        # Download from Twilio
        audio_url = f"{recording_url}.wav"
        r = http_requests.get(audio_url, auth=(TWILIO_SID, TWILIO_TOKEN), timeout=30)
        if r.status_code != 200:
            print(f"❌ Download failed: {r.status_code}")
            return

        # Save audio file
        audio_path = AUDIO_DIR / f"{recording_sid}.wav"
        audio_path.write_bytes(r.content)

        # Transcribe with local STT first, then Deepgram fallback
        transcript = transcription_text
        if not transcript and voice_engine and voice_engine.stt:
            try:
                transcript = voice_engine.transcribe(str(audio_path))
                if transcript:
                    print(f"📝 Local STT transcript: {transcript[:80]}...")
            except Exception as e:
                print(f"⚠️ Local STT failed: {e}")

        if not transcript and dg_client:
            try:
                transcript = _deepgram_transcribe(r.content)
            except Exception as e:
                print(f"⚠️ Deepgram transcription failed: {e}")

        # Update metadata with transcript (serialised against other writers)
        with _vm_lock:
            voicemails = load_voicemails()
            for vm in voicemails:
                if vm.get("sid") == recording_sid:
                    vm["transcript"] = transcript
                    vm["audio_path"] = str(audio_path)
                    break
            save_voicemails(voicemails)

        # Telegram notification
        notify_telegram(recording_url, caller, duration, transcript)

        print(f"✅ Voicemail saved: {audio_path}")

    except Exception as e:
        print(f"❌ Voicemail processing error: {e}")


# ═══════════════════════════════════════════════════════════════════
# Outgoing calls
# ═══════════════════════════════════════════════════════════════════

@app.route("/voice/outgoing", methods=["POST"])
@require_twilio
def handle_outgoing():
    """Handle outgoing call — connect to AI."""
    call_sid = request.form.get("CallSid", "unknown")
    print(f"📱 Outgoing connected: {call_sid}")

    resp = VoiceResponse()
    connect = Connect()
    connect.stream(url=public_ws_url("/ws/call"))
    resp.append(connect)
    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/status", methods=["POST"])
@require_twilio
def handle_status():
    """Call status callback — logs transcript when call ends."""
    call_sid = request.form.get("CallSid", "")
    status = request.form.get("CallStatus", "")
    print(f"📊 {call_sid}: {status}")

    if status in ("completed", "failed", "busy", "no-answer"):
        state = call_states.pop(call_sid, None)
        if state and state.get("transcript"):
            print(f"\n{'='*50}")
            print(f"📝 TRANSCRIPT:")
            for m in state["transcript"]:
                tag = "🎤" if m["role"] == "user" else "🤖"
                print(f"  {tag} {m['text']}")
            print(f"{'='*50}")
    return "", 204


# ═══════════════════════════════════════════════════════════════════
# WebSocket (Twilio Media Streams)
# ═══════════════════════════════════════════════════════════════════

@sock.route("/ws/call")
def handle_ws(ws):
    """Bi-directional audio: Twilio ↔ Deepgram STT ↔ LLM ↔ TTS."""
    print("🔌 WebSocket connected")

    stream_sid = None
    call_sid = None

    # Set up Deepgram live transcription
    dg_conn = None
    if dg_client:
        try:
            from deepgram.core.events import EventType
            dg_conn = dg_client.listen.v1.connect(
                model="nova-2-phonecall",
                encoding="linear16",
                sample_rate=8000,
                channels=1,
                punctuate=True,
                interim_results=True,
            )
            print("✅ Deepgram connected")
        except Exception as e:
            print(f"⚠️ Deepgram connection failed: {e}")

    transcript_buf = []
    speech_final = False

    if dg_conn:
        from deepgram.core.events import EventType

        def on_message(msg):
            nonlocal speech_final
            if hasattr(msg, "channel") and msg.channel:
                text = msg.channel.alternatives[0].transcript
                if text.strip():
                    transcript_buf.append(text)
                    print(f"🎤 {text}")
                    if getattr(msg, "is_final", False):
                        speech_final = True

        dg_conn.on(EventType.MESSAGE, on_message)

    try:
        while True:
            data = ws.receive(timeout=30)
            if data is None:
                break

            msg = json.loads(data)

            if msg["event"] == "start":
                stream_sid = msg["start"]["streamSid"]
                call_sid = msg["start"]["callSid"]
                print(f"🎙️ Stream: {stream_sid}")

            elif msg["event"] == "media":
                audio = base64.b64decode(msg["media"]["payload"])
                if dg_conn:
                    dg_conn.send_media(audio)

            elif msg["event"] == "stop":
                break

            # Check for complete utterance
            if speech_final and stream_sid:
                full_text = " ".join(transcript_buf).strip()
                transcript_buf.clear()
                speech_final = False

                if full_text and len(full_text) > 2:
                    print(f"💬 User: {full_text}")
                    reply = get_llm_response(call_sid or "ws", full_text)
                    print(f"🤖 AI: {reply}")

                    audio = synthesize_speech(reply)
                    if audio:
                        send_audio_to_ws(ws, stream_sid, audio)

    except Exception as e:
        print(f"❌ WS error: {e}")
    finally:
        if dg_conn:
            dg_conn.close()
        print("🔌 WebSocket closed")


# ═══════════════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.route("/call", methods=["POST"])
@require_auth
def make_call():
    """Make an outbound call."""
    data = request.json or {}
    to_number = data.get("to", "")
    goal = data.get("goal", CALL_GOAL)

    if not to_number:
        return jsonify({"error": "Missing 'to'"}), 400
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM]):
        return jsonify({"error": "Twilio not configured"}), 500

    base = public_base()
    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        call = client.calls.create(
            to=to_number,
            from_=TWILIO_FROM,
            url=f"{base}/voice/outgoing",
            status_callback=f"{base}/voice/status",
            status_callback_event=["completed", "failed", "busy", "no-answer"],
            timeout=30,
        )
        # Remember this call's goal so the WebSocket turn uses it (keyed by CallSid)
        call_states[call.sid] = {"messages": [], "transcript": [], "goal": goal}
        return jsonify({"sid": call.sid, "status": call.status, "to": to_number})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/voicemails", methods=["GET"])
@require_auth
def list_voicemails():
    """List all voicemails."""
    return jsonify(load_voicemails())


@app.route("/voicemails/<sid>", methods=["DELETE"])
@require_auth
def delete_voicemail(sid):
    """Delete a voicemail."""
    with _vm_lock:
        voicemails = [vm for vm in load_voicemails() if vm.get("sid") != sid]
        save_voicemails(voicemails)

    # Delete audio file
    audio_path = AUDIO_DIR / f"{sid}.wav"
    if audio_path.exists():
        audio_path.unlink()

    return jsonify({"status": "deleted", "sid": sid})


@app.route("/health", methods=["GET"])
@require_auth
def health():
    """Server health check."""
    return jsonify({
        "status": "ok",
        "twilio": bool(TWILIO_SID),
        "deepgram": bool(DEEPGRAM_KEY),
        "llm": bool(llm_client),
        "provider": LLM_PROVIDER,
        "model": LLM_MODEL,
        "voicemails": len(load_voicemails()),
    })


# ═══════════════════════════════════════════════════════════════════
# Settings API
# ═══════════════════════════════════════════════════════════════════

def get_settings():
    """Read current settings from .env."""
    settings = {}
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    settings[k.strip()] = v.strip().strip('"').strip("'")
    return settings


def update_setting(key, value):
    """Update a single setting in .env (value sanitised to prevent injection)."""
    # Strip CR/LF and double-quotes so a value can't inject extra .env lines.
    value = str(value).replace("\r", " ").replace("\n", " ").replace('"', "'")
    env_path = Path(__file__).parent / ".env"
    lines = []
    found = False

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    lines.append(f'{key}="{value}"\n')
                    found = True
                else:
                    lines.append(line)

    if not found:
        lines.append(f'{key}="{value}"\n')

    with open(env_path, "w") as f:
        f.writelines(lines)

    # Update in-memory
    os.environ[key] = value


@app.route("/api/settings", methods=["GET"])
@require_auth
def api_get_settings():
    """Get current settings."""
    settings = get_settings()
    # Mask sensitive values
    masked = {}
    for k, v in settings.items():
        if any(s in k.upper() for s in ["TOKEN", "KEY", "SECRET", "AUTH"]):
            if len(v) > 8:
                masked[k] = v[:4] + "..." + v[-4:]
            else:
                masked[k] = "***"
        else:
            masked[k] = v

    # Add computed info
    masked["_status"] = {
        "twilio": bool(TWILIO_SID),
        "deepgram": bool(DEEPGRAM_KEY),
        "llm": bool(llm_client),
        "voice_engine": voice_engine.mode if voice_engine else "none",
    }

    # Available TTS voices (Twilio Polly voices)
    masked["_available_voices"] = [
        {"id": "Polly.Amy", "name": "Amy", "lang": "en-GB", "gender": "Female"},
        {"id": "Polly.Brian", "name": "Brian", "lang": "en-GB", "gender": "Male"},
        {"id": "Polly.Emma", "name": "Emma", "lang": "en-GB", "gender": "Female"},
        {"id": "Polly.Joanna", "name": "Joanna", "lang": "en-US", "gender": "Female"},
        {"id": "Polly.Matthew", "name": "Matthew", "lang": "en-US", "gender": "Male"},
        {"id": "Polly.Ivy", "name": "Ivy", "lang": "en-US", "gender": "Female"},
        {"id": "Polly.Justin", "name": "Justin", "lang": "en-US", "gender": "Male"},
        {"id": "Polly.Kendra", "name": "Kendra", "lang": "en-US", "gender": "Female"},
        {"id": "Polly.Kimberly", "name": "Kimberly", "lang": "en-US", "gender": "Female"},
        {"id": "Polly.Salli", "name": "Salli", "lang": "en-US", "gender": "Female"},
        {"id": "Polly.Nicole", "name": "Nicole", "lang": "en-AU", "gender": "Female"},
        {"id": "Polly.Russell", "name": "Russell", "lang": "en-AU", "gender": "Male"},
    ]

    return jsonify(masked)


@app.route("/api/settings", methods=["POST"])
@require_auth
def api_update_settings():
    """Update settings."""
    data = request.json or {}
    allowed = [
        "COMPANY_NAME", "VOICEMAIL_EMAIL", "VOICEMAIL_PIN",
        "VOICEMAIL_GREETING", "VOICEMAIL_MAX_LENGTH",
        "TTS_VOICE", "TTS_LANGUAGE", "USE_LOCAL_VOICE",
        "CALL_GOAL", "CALL_SYSTEM_PROMPT",
    ]

    updated = []
    for key, value in data.items():
        if key in allowed:
            update_setting(key, str(value))
            updated.append(key)

    return jsonify({"status": "ok", "updated": updated})


# ═══════════════════════════════════════════════════════════════════
# Web Dashboard
# ═══════════════════════════════════════════════════════════════════

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📞 Hermes Phone — Sign in</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .box { background: #1a1a1a; border: 1px solid #333; border-radius: 12px; padding: 32px; width: 320px; }
        h1 { font-size: 20px; margin-bottom: 4px; }
        p { color: #888; font-size: 13px; margin-bottom: 20px; }
        input { width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #333; background: #111; color: #e0e0e0; font-size: 14px; }
        button { width: 100%; margin-top: 12px; padding: 10px; border-radius: 8px; border: none; background: #1d4ed8; color: #fff; font-size: 14px; cursor: pointer; }
        button:hover { background: #2563eb; }
    </style>
</head>
<body>
    <form class="box" method="POST" action="/login">
        <h1>📞 Hermes Phone</h1>
        <p>Enter your access token to continue.</p>
        <input type="password" name="token" placeholder="Access token" autofocus autocomplete="current-password">
        <button type="submit">Sign in</button>
    </form>
</body>
</html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📞 Hermes Phone</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0a0a; color: #e0e0e0; min-height: 100vh; }
        .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 24px 32px; border-bottom: 1px solid #333; }
        .header h1 { font-size: 24px; font-weight: 600; }
        .header .subtitle { color: #888; font-size: 14px; margin-top: 4px; }
        .nav { display: flex; gap: 0; padding: 0 32px; background: #111; border-bottom: 1px solid #222; }
        .nav a { padding: 12px 20px; color: #888; text-decoration: none; font-size: 14px; border-bottom: 2px solid transparent; transition: all 0.15s; }
        .nav a:hover { color: #e0e0e0; }
        .nav a.active { color: #fff; border-bottom-color: #3b82f6; }
        .status-bar { display: flex; gap: 16px; padding: 16px 32px; background: #111; border-bottom: 1px solid #222; }
        .status-item { display: flex; align-items: center; gap: 8px; font-size: 13px; }
        .dot { width: 8px; height: 8px; border-radius: 50%; }
        .dot.green { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
        .dot.red { background: #f87171; }
        .container { max-width: 960px; margin: 0 auto; padding: 32px; }
        .section { margin-bottom: 32px; }
        .section h2 { font-size: 18px; font-weight: 600; margin-bottom: 16px; color: #fff; }
        .card { background: #1a1a1a; border: 1px solid #333; border-radius: 12px; overflow: hidden; }
        .voicemail-item { padding: 16px 20px; border-bottom: 1px solid #222; display: flex; align-items: center; gap: 16px; transition: background 0.15s; }
        .voicemail-item:hover { background: #222; }
        .voicemail-item:last-child { border-bottom: none; }
        .vm-icon { font-size: 24px; }
        .vm-info { flex: 1; }
        .vm-caller { font-weight: 600; font-size: 15px; }
        .vm-time { color: #888; font-size: 12px; margin-top: 2px; }
        .vm-transcript { color: #aaa; font-size: 13px; margin-top: 6px; line-height: 1.4; }
        .vm-actions { display: flex; gap: 8px; }
        .btn { padding: 6px 14px; border-radius: 6px; border: 1px solid #444; background: #222; color: #e0e0e0; font-size: 12px; cursor: pointer; transition: all 0.15s; }
        .btn:hover { background: #333; border-color: #666; }
        .btn.danger { border-color: #991b1b; color: #f87171; }
        .btn.danger:hover { background: #991b1b; color: #fff; }
        .btn.primary { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
        .btn.primary:hover { background: #2563eb; }
        .empty { padding: 48px; text-align: center; color: #666; }
        .export-bar { display: flex; gap: 8px; margin-bottom: 16px; }
        .call-form { display: flex; gap: 8px; margin-bottom: 16px; }
        .call-form input { flex: 1; padding: 8px 12px; border-radius: 6px; border: 1px solid #444; background: #222; color: #e0e0e0; font-size: 14px; }
        audio { width: 200px; height: 32px; }
        /* Settings */
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; font-size: 13px; color: #888; margin-bottom: 6px; font-weight: 500; }
        .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #333; background: #111; color: #e0e0e0; font-size: 14px; font-family: inherit; }
        .form-group textarea { min-height: 80px; resize: vertical; }
        .form-group select { cursor: pointer; }
        .form-group .hint { font-size: 11px; color: #666; margin-top: 4px; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .save-bar { position: sticky; bottom: 0; padding: 16px 0; background: linear-gradient(transparent, #0a0a0a 20%); display: flex; justify-content: flex-end; gap: 8px; }
        .toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 20px; border-radius: 8px; font-size: 13px; z-index: 100; animation: fadeIn 0.2s; }
        .toast.success { background: #065f46; color: #6ee7b7; border: 1px solid #059669; }
        .toast.error { background: #7f1d1d; color: #fca5a5; border: 1px solid #dc2626; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .badge.green { background: #065f46; color: #6ee7b7; }
        .badge.red { background: #7f1d1d; color: #fca5a5; }
        .badge.yellow { background: #78350f; color: #fcd34d; }
        .page { display: none; }
        .page.active { display: block; }
    </style>
</head>
<body>
    <div class="header">
        <h1>📞 Hermes Phone</h1>
        <div class="subtitle">AI-powered phone agent</div>
    </div>
    <div class="nav">
        <a href="#" class="active" onclick="showPage('dashboard')">Dashboard</a>
        <a href="#" onclick="showPage('settings')">Settings</a>
    </div>
    <div class="status-bar" id="status-bar">
        <div class="status-item"><div class="dot" id="status-dot"></div><span id="status-text">Loading...</span></div>
    </div>

    <!-- Dashboard Page -->
    <div class="container page active" id="page-dashboard">
        <div class="section">
            <h2>Make a Call</h2>
            <div class="call-form">
                <input type="tel" id="call-number" placeholder="+447...">
                <button class="btn primary" onclick="makeCall()">📞 Call</button>
            </div>
        </div>
        <div class="section">
            <h2>Voicemails</h2>
            <div class="export-bar">
                <button class="btn" onclick="exportAll()">📦 Export All (ZIP)</button>
                <button class="btn" onclick="exportTranscripts()">📝 Export Transcripts</button>
            </div>
            <div class="card" id="voicemail-list">
                <div class="empty">Loading voicemails...</div>
            </div>
        </div>
    </div>

    <!-- Settings Page -->
    <div class="container page" id="page-settings">
        <div class="section">
            <h2>Company & Voicemail</h2>
            <div class="card" style="padding: 24px;">
                <div class="form-row">
                    <div class="form-group">
                        <label>Company Name</label>
                        <input type="text" id="set-COMPANY_NAME" placeholder="My Company">
                    </div>
                    <div class="form-group">
                        <label>Voicemail Email</label>
                        <input type="email" id="set-VOICEMAIL_EMAIL" placeholder="hello@company.com">
                    </div>
                </div>
                <div class="form-group">
                    <label>Voicemail Greeting</label>
                    <textarea id="set-VOICEMAIL_GREETING" placeholder="Leave empty for default: 'Thank you for calling [company]. Please leave a message after the tone.'"></textarea>
                    <div class="hint">Custom greeting played to callers. Leave empty for the default.</div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Voicemail PIN</label>
                        <input type="text" id="set-VOICEMAIL_PIN" placeholder="1234">
                        <div class="hint">Callers dial this during greeting to reach AI</div>
                    </div>
                    <div class="form-group">
                        <label>Max Recording (seconds)</label>
                        <input type="number" id="set-VOICEMAIL_MAX_LENGTH" placeholder="120">
                    </div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>Voice</h2>
            <div class="card" style="padding: 24px;">
                <div class="form-row">
                    <div class="form-group">
                        <label>TTS Voice</label>
                        <select id="set-TTS_VOICE">
                            <option value="">Loading voices...</option>
                        </select>
                        <div class="hint">Voice used for system messages (greeting, goodbye)</div>
                    </div>
                    <div class="form-group">
                        <label>Language</label>
                        <select id="set-TTS_LANGUAGE">
                            <option value="en-GB">English (UK)</option>
                            <option value="en-US">English (US)</option>
                            <option value="en-AU">English (AU)</option>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label>Voice Engine</label>
                    <select id="set-USE_LOCAL_VOICE">
                        <option value="auto">Auto (local if available, else cloud)</option>
                        <option value="true">Local Only (MLX on Apple Silicon)</option>
                        <option value="false">Cloud Only (Deepgram/Edge TTS)</option>
                    </select>
                    <div class="hint">Local mode uses mlx-whisper + mlx-audio (zero API costs)</div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>AI Agent</h2>
            <div class="card" style="padding: 24px;">
                <div class="form-group">
                    <label>Call Goal</label>
                    <input type="text" id="set-CALL_GOAL" placeholder="Have a helpful conversation.">
                    <div class="hint">What the AI should try to achieve during calls</div>
                </div>
                <div class="form-group">
                    <label>System Prompt</label>
                    <textarea id="set-CALL_SYSTEM_PROMPT" placeholder="Leave empty for default behavior"></textarea>
                    <div class="hint">Custom instructions for the AI during calls</div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>Service Status</h2>
            <div class="card" style="padding: 24px;">
                <div id="service-status">Loading...</div>
            </div>
        </div>

        <div class="save-bar">
            <button class="btn" onclick="loadSettings()">Reset</button>
            <button class="btn primary" onclick="saveSettings()">💾 Save Settings</button>
        </div>
    </div>

    <script>
        // Auth is handled by the session cookie (localhost is trusted; remote signs in
        // at /login). Same-origin requests send the cookie automatically — no token in URLs.

        // Navigation
        function showPage(name) {
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.nav a').forEach(a => a.classList.remove('active'));
            document.getElementById('page-' + name).classList.add('active');
            event.target.classList.add('active');
            if (name === 'settings') loadSettings();
        }

        function toast(msg, type='success') {
            const el = document.createElement('div');
            el.className = 'toast ' + type;
            el.textContent = msg;
            document.body.appendChild(el);
            setTimeout(() => el.remove(), 3000);
        }

        // Status
        async function loadStatus() {
            try {
                const r = await fetch('/health');
                const d = await r.json();
                document.getElementById('status-dot').className = 'dot ' + (d.status === 'ok' ? 'green' : 'red');
                document.getElementById('status-text').textContent = d.status === 'ok'
                    ? `Running — ${d.provider}/${d.model} — ${d.voicemails} voicemails`
                    : 'Offline';
            } catch { document.getElementById('status-dot').className = 'dot red'; document.getElementById('status-text').textContent = 'Offline'; }
        }

        // Voicemails
        async function loadVoicemails() {
            try {
                const r = await fetch('/voicemails');
                const vms = await r.json();
                const el = document.getElementById('voicemail-list');
                if (!vms.length) { el.innerHTML = '<div class="empty">No voicemails yet</div>'; return; }
                el.innerHTML = vms.reverse().map(vm => `
                    <div class="voicemail-item">
                        <div class="vm-icon">📞</div>
                        <div class="vm-info">
                            <div class="vm-caller">${(vm.from||'Unknown').replace('+','')}</div>
                            <div class="vm-time">${vm.duration}s — ${new Date(vm.time).toLocaleString()}</div>
                            ${vm.transcript ? `<div class="vm-transcript">"${vm.transcript}"</div>` : '<div class="vm-transcript" style="color:#666">(no transcript)</div>'}
                        </div>
                        <div class="vm-actions">
                            ${vm.audio_path ? `<audio controls src="/voicemails/${vm.sid}/audio"></audio>` : ''}
                            <button class="btn danger" onclick="deleteVM('${vm.sid}')">🗑️</button>
                        </div>
                    </div>
                `).join('');
            } catch(e) { console.error(e); }
        }

        async function deleteVM(sid) {
            if (!confirm('Delete this voicemail?')) return;
            await fetch('/voicemails/' + sid, {method:'DELETE'});
            loadVoicemails(); loadStatus();
        }

        async function makeCall() {
            const num = document.getElementById('call-number').value;
            if (!num) return alert('Enter a phone number');
            const r = await fetch('/call', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({to:num})});
            const d = await r.json();
            toast(d.sid ? 'Calling ' + num + '...' : 'Error: ' + d.error, d.sid ? 'success' : 'error');
        }

        function exportAll() { window.location = '/export/zip'; }
        function exportTranscripts() { window.location = '/export/transcripts'; }

        // Settings
        let currentSettings = {};
        let availableVoices = [];

        async function loadSettings() {
            try {
                const r = await fetch('/api/settings');
                const data = await r.json();
                currentSettings = data;
                availableVoices = data._available_voices || [];

                // Populate voice dropdown
                const voiceSelect = document.getElementById('set-TTS_VOICE');
                voiceSelect.innerHTML = availableVoices.map(v =>
                    `<option value="${v.id}" ${v.id === data.TTS_VOICE ? 'selected' : ''}>${v.name} (${v.lang}, ${v.gender})</option>`
                ).join('');

                // Populate form fields
                const fields = ['COMPANY_NAME', 'VOICEMAIL_EMAIL', 'VOICEMAIL_GREETING', 'VOICEMAIL_PIN',
                    'VOICEMAIL_MAX_LENGTH', 'TTS_LANGUAGE', 'USE_LOCAL_VOICE', 'CALL_GOAL', 'CALL_SYSTEM_PROMPT'];
                fields.forEach(f => {
                    const el = document.getElementById('set-' + f);
                    if (el && data[f] !== undefined) el.value = data[f];
                });

                // Service status
                const st = data._status || {};
                document.getElementById('service-status').innerHTML = `
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                        <div>Twilio: <span class="badge ${st.twilio?'green':'red'}">${st.twilio?'Connected':'Not configured'}</span></div>
                        <div>Deepgram: <span class="badge ${st.deepgram?'green':'red'}">${st.deepgram?'Connected':'Not configured'}</span></div>
                        <div>LLM: <span class="badge ${st.llm?'green':'red'}">${st.llm?'Connected':'Not configured'}</span></div>
                        <div>Voice Engine: <span class="badge ${st.voice_engine.includes('local')?'green':'yellow'}">${st.voice_engine}</span></div>
                    </div>
                `;
            } catch(e) { console.error(e); }
        }

        async function saveSettings() {
            const fields = ['COMPANY_NAME', 'VOICEMAIL_EMAIL', 'VOICEMAIL_GREETING', 'VOICEMAIL_PIN',
                'VOICEMAIL_MAX_LENGTH', 'TTS_VOICE', 'TTS_LANGUAGE', 'USE_LOCAL_VOICE', 'CALL_GOAL', 'CALL_SYSTEM_PROMPT'];
            const data = {};
            fields.forEach(f => {
                const el = document.getElementById('set-' + f);
                if (el) data[f] = el.value;
            });

            try {
                const r = await fetch('/api/settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                const result = await r.json();
                toast('Settings saved! Restart server to apply some changes.', 'success');
            } catch(e) {
                toast('Failed to save settings', 'error');
            }
        }

        loadStatus(); loadVoicemails();
        setInterval(() => { loadStatus(); loadVoicemails(); }, 15000);
    </script>
</body>
</html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    """Exchange the access token for an HttpOnly session cookie (remote browsers)."""
    if _is_local() or _cookie_ok():
        return Response("", status=303, headers={"Location": "/"})
    if request.method == "POST":
        token = request.form.get("token", "")
        if API_TOKEN and secrets.compare_digest(token, API_TOKEN):
            sid = secrets.token_urlsafe(32)
            sessions[sid] = time.time()
            resp = Response("", status=303, headers={"Location": "/"})
            resp.set_cookie(
                SESSION_COOKIE, sid,
                httponly=True, samesite="Strict",
                secure=request.is_secure or _request_via_proxy(),
                max_age=SESSION_TTL,
            )
            return resp
        return Response(LOGIN_HTML, mimetype="text/html", status=401)
    return Response(LOGIN_HTML, mimetype="text/html")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """Invalidate the current browser session (revokes the cookie server-side)."""
    sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    resp = Response("", status=303, headers={"Location": "/login"})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.route("/", methods=["GET"])
@require_auth
def dashboard():
    """Web dashboard."""
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/voicemails/<sid>/audio", methods=["GET"])
@require_auth
def serve_voicemail_audio(sid):
    """Serve voicemail audio file."""
    audio_path = AUDIO_DIR / f"{sid}.wav"
    if audio_path.exists():
        return Response(audio_path.read_bytes(), mimetype="audio/wav")
    return jsonify({"error": "Audio not found"}), 404


@app.route("/export/zip", methods=["GET"])
@require_auth
def export_zip():
    """Export all voicemails as a ZIP file."""
    import zipfile
    import io

    voicemails = load_voicemails()
    if not voicemails:
        return jsonify({"error": "No voicemails to export"}), 404

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add metadata
        zf.writestr("voicemails.json", json.dumps(voicemails, indent=2))

        # Add audio files
        for vm in voicemails:
            audio_path = AUDIO_DIR / f"{vm['sid']}.wav"
            if audio_path.exists():
                caller = vm.get("from", "unknown").replace("+", "")
                zf.writestr(f"audio/{caller}_{vm['sid']}.wav", audio_path.read_bytes())

        # Add transcript summary
        lines = ["Voicemail Transcripts", "=" * 50, ""]
        for vm in voicemails:
            caller = vm.get("from", "unknown").replace("+", "")
            lines.append(f"From: {caller}")
            lines.append(f"Duration: {vm.get('duration', 0)}s")
            lines.append(f"Time: {vm.get('time', 'unknown')}")
            lines.append(f"Transcript: {vm.get('transcript', '(none)')}")
            lines.append("-" * 50)
        zf.writestr("transcripts.txt", "\n".join(lines))

    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": "attachment; filename=hermes-phone-voicemails.zip"},
    )


@app.route("/export/transcripts", methods=["GET"])
@require_auth
def export_transcripts():
    """Export transcripts as a text file."""
    voicemails = load_voicemails()
    lines = [f"Hermes Phone — Voicemail Transcripts", f"Exported: {datetime.now().isoformat()}", "=" * 50, ""]
    for vm in voicemails:
        caller = vm.get("from", "unknown").replace("+", "")
        lines.append(f"From: {caller}")
        lines.append(f"Duration: {vm.get('duration', 0)}s")
        lines.append(f"Time: {vm.get('time', 'unknown')}")
        lines.append(f"Transcript: {vm.get('transcript', '(none)')}")
        lines.append("-" * 50)

    return Response(
        "\n".join(lines),
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=hermes-phone-transcripts.txt"},
    )


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"📞 Hermes Phone Agent")
    print(f"   Company: {COMPANY_NAME}")
    print(f"   LLM: {LLM_PROVIDER}/{LLM_MODEL}")
    print(f"   STT: {'Deepgram' if dg_client else '❌'}")
    print(f"   Twilio: {'✅' if TWILIO_SID else '❌'}")
    print(f"   LLM: {'✅' if llm_client else '❌'}")
    print(f"   PIN: {'set' if VOICEMAIL_PIN else 'unset'}")
    print(f"   Voicemails: {DATA_DIR}")
    print(f"   Auth: {'token + localhost' if API_TOKEN else 'localhost-only (set HERMES_API_TOKEN for remote)'}")
    print(f"   Twilio sig check: {'on' if (VALIDATE_TWILIO and twilio_validator) else 'OFF'}")
    print(f"   Listening on {HOST}:{PORT} (debug={DEBUG})")
    app.run(host=HOST, port=PORT, debug=DEBUG)
