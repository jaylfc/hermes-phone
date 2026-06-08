"""
Hermes Phone — AI-powered VoIP server for macOS.

Architecture:
  Twilio (audio) → WebSocket → Deepgram (STT) → Hermes Agent (LLM + tools + memory) → TTS → Twilio

Two ports:
  5050 — Public webhook server (Twilio calls, no auth)
  5051 — Protected dashboard + API (token auth required)

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
import threading
import tempfile
import subprocess
import hmac
import secrets
import functools
import uuid
from pathlib import Path
from datetime import datetime
from provider_registry import PROVIDER_DEPS, check_provider_installed, get_provider_status

# Agent backend (lazy-loaded)
from agents import get_agent_backend

import requests as http_requests
from flask import Flask, request, Response, jsonify
from flask_sock import Sock
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Connect, Gather

# ═══════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════

ENV_FILE = Path(__file__).parent / ".env"

def load_env():
    """Load .env file into os.environ."""
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip().strip('"').strip("'")
                    os.environ.setdefault(key.strip(), value)

load_env()

def env(key, default=""):
    return os.environ.get(key, default)

# Twilio
TWILIO_SID = env("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = env("TWILIO_AUTH_TOKEN")
TWILIO_FROM = env("TWILIO_PHONE_NUMBER")

# Deepgram (STT)
DEEPGRAM_KEY = env("DEEPGRAM_API_KEY")

# Hermes Gateway (LLM — wraps the Hermes agent)
HERMES_GATEWAY_URL = env("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")
HERMES_GATEWAY_TOKEN = env("HERMES_GATEWAY_TOKEN")

# Model override (use specific model instead of agent default)
HERMES_MODEL_OVERRIDE = env("HERMES_MODEL_OVERRIDE")

# Legacy LLM (fallback if Hermes Gateway not available).
# Offline-by-default: a fresh install runs a local Ollama model + local voice, so
# no API keys are needed except Twilio. Override any of these from the dashboard.
AGENT_PROVIDER = env("AGENT_PROVIDER", "ollama")
LLM_PROVIDER = env("LLM_PROVIDER", "ollama")
LLM_MODEL = env("LLM_MODEL", "qwen3:8b")  # installer tiers this by Mac RAM
XIAOMI_KEY = env("XIAOMI_API_KEY")
XIAOMI_BASE_URL = env("XIAOMI_BASE_URL", "https://token-plan-ams.xiaomimimo.com/v1")
OPENAI_KEY = env("OPENAI_API_KEY")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENROUTER_KEY = env("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Phone Agent
VOICEMAIL_PIN = env("VOICEMAIL_PIN", "1234")
COMPANY_NAME = env("COMPANY_NAME", "My Company")
VOICEMAIL_EMAIL = env("VOICEMAIL_EMAIL")
VOICEMAIL_MAX_LENGTH = int(env("VOICEMAIL_MAX_LENGTH", "120"))
VOICEMAIL_GREETING = env("VOICEMAIL_GREETING")
TTS_VOICE = env("TTS_VOICE", "Polly.Brian")
TTS_LANGUAGE = env("TTS_LANGUAGE", "en-GB")
CALL_GOAL = env("CALL_GOAL", "Have a helpful conversation.")
SYSTEM_PROMPT = env("CALL_SYSTEM_PROMPT")

# Telegram
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")

# Dashboard auth
DASHBOARD_TOKEN = env("DASHBOARD_TOKEN")
AUTH_COOKIE = "hp_auth"
SESSION_TTL = 86400 * 30  # 30 days

# Dashboard browser sessions: opaque session_id -> created_ts (in-memory, revocable).
# The cookie holds a random id, NOT the dashboard token, so a leaked cookie can be
# revoked and never exposes the master token.
dashboard_sessions = {}


def _new_session():
    sid = secrets.token_urlsafe(32)
    dashboard_sessions[sid] = time.time()
    return sid


def _session_valid(sid):
    if not sid:
        return False
    created = dashboard_sessions.get(sid)
    if created is None:
        return False
    if time.time() - created > SESSION_TTL:
        dashboard_sessions.pop(sid, None)
        return False
    return True

# Webhook URL override (use if behind a proxy or different URL)
WEBHOOK_URL_OVERRIDE = env("WEBHOOK_URL_OVERRIDE")

# Webhook security
VALIDATE_TWILIO = env("VALIDATE_TWILIO_SIGNATURE", "true").lower() in ("1", "true", "yes")
PIN_MAX_ATTEMPTS = int(env("PIN_MAX_ATTEMPTS", "5"))
PIN_LOCKOUT_WINDOW = int(env("PIN_LOCKOUT_WINDOW", "600"))

# Voice engine
USE_LOCAL_VOICE = env("USE_LOCAL_VOICE", "auto").lower()
# ═══════════════════════════════════════════════════════════════════
# STT Provider Configuration
# ═══════════════════════════════════════════════════════════════════
# Options: deepgram, assemblyai, google, azure, whisper, groq, speechmatics, local
STT_PROVIDER = env("STT_PROVIDER", "whisper")  # local STT by default (no key)
# Provider-specific API keys
ASSEMBLYAI_API_KEY = env("ASSEMBLYAI_API_KEY")
GOOGLE_STT_CREDENTIALS = env("GOOGLE_STT_CREDENTIALS")  # Path to JSON key file
AZURE_SPEECH_KEY = env("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION = env("AZURE_SPEECH_REGION")
GROQ_API_KEY = env("GROQ_API_KEY")
SPEECHMATICS_API_KEY = env("SPEECHMATICS_API_KEY")

# ═══════════════════════════════════════════════════════════════════
# TTS Provider Configuration
# ═══════════════════════════════════════════════════════════════════
# Options: polly, elevenlabs, openai, azure, google, cartesia, deepgram_aura, kokoro, edge, mimo
TTS_PROVIDER = env("TTS_PROVIDER", "kokoro")  # local TTS by default (no key)
# Provider-specific API keys
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel
CARTESIA_API_KEY = env("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = env("CARTESIA_VOICE_ID")
AZURE_TTS_KEY = env("AZURE_TTS_KEY")
AZURE_TTS_REGION = env("AZURE_TTS_REGION")
GOOGLE_TTS_CREDENTIALS = env("GOOGLE_TTS_CREDENTIALS")



# Ports
WEBHOOK_PORT = int(env("WEBHOOK_PORT", "5050"))
DASHBOARD_PORT = int(env("DASHBOARD_PORT", "5051"))

# Data directory
DATA_DIR = Path(__file__).parent / "voicemails"
AUDIO_DIR = DATA_DIR / "audio"
METADATA_FILE = DATA_DIR / "metadata.json"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# Flask Apps — two ports
# ═══════════════════════════════════════════════════════════════════

webhook_app = Flask("webhook")
dashboard_app = Flask("dashboard")
dashboard_app.secret_key = secrets.token_hex(32)
webhook_sock = Sock(webhook_app)

# ── Twilio webhook signature validation (public app) ────────────────
twilio_validator = RequestValidator(TWILIO_TOKEN) if TWILIO_TOKEN else None


@webhook_app.before_request
def _validate_twilio_signature():
    """Reject /voice/* webhooks that aren't signed by Twilio.

    Skipped when no auth token is configured (first-run/dev) or when
    VALIDATE_TWILIO_SIGNATURE=false. The /ws media stream is not guarded here —
    it only carries audio for an already-connected call.
    """
    if not request.path.startswith("/voice/"):
        return None
    # Read live so the Settings UI toggle / URL override apply without a restart.
    validate = env("VALIDATE_TWILIO_SIGNATURE", "true").lower() in ("1", "true", "yes")
    if not (validate and twilio_validator):
        return None
    override = env("WEBHOOK_URL_OVERRIDE")
    signature = request.headers.get("X-Twilio-Signature", "")
    url = (override.rstrip("/") + request.path) if override else request.url
    if not twilio_validator.validate(url, request.form.to_dict(), signature):
        print(f"⛔ Invalid Twilio signature for {request.path}")
        return Response("Invalid signature", status=403)
    return None


def _ws_url():
    """Media Streams require wss://; honour WEBHOOK_URL_OVERRIDE (read live) when set."""
    override = env("WEBHOOK_URL_OVERRIDE")
    if override:
        host = override.split("://", 1)[-1].rstrip("/")
        return f"wss://{host}/ws/call"
    return f"wss://{request.host}/ws/call"


# ── PIN brute-force tracking (per caller) ───────────────────────────
pin_attempts = {}


def _pin_locked(caller):
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

# ═══════════════════════════════════════════════════════════════════
# Auth helpers (dashboard only)
# ═══════════════════════════════════════════════════════════════════

def check_auth(token):
    if not DASHBOARD_TOKEN:
        return True  # No token set = no auth (first run)
    return hmac.compare_digest(token, DASHBOARD_TOKEN)

def get_auth_headers():
    """Headers for outbound API calls to Hermes Gateway."""
    if HERMES_GATEWAY_TOKEN:
        return {"Authorization": f"Bearer {HERMES_GATEWAY_TOKEN}"}
    return {}

# Login page HTML
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>📞 Hermes Phone — Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.login-card{background:#1a1a1a;border:1px solid #333;border-radius:16px;padding:40px;width:100%;max-width:380px}
.login-card h1{font-size:24px;text-align:center;margin-bottom:8px}
.login-card .sub{text-align:center;color:#888;font-size:14px;margin-bottom:32px}
.fg{margin-bottom:20px}
.fg label{display:block;font-size:13px;color:#888;margin-bottom:6px;font-weight:500}
.fg input{width:100%;padding:12px;border-radius:8px;border:1px solid #333;background:#111;color:#e0e0e0;font-size:15px;font-family:monospace;letter-spacing:2px;text-align:center}
.fg input:focus{outline:none;border-color:#3b82f6}
.btn{width:100%;padding:12px;border-radius:8px;border:none;background:#1d4ed8;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
.btn:hover{background:#2563eb}
.error{background:#7f1d1d;color:#fca5a5;padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:16px;text-align:center;display:none}
.hint{text-align:center;color:#555;font-size:12px;margin-top:16px}
</style></head><body>
<div class="login-card">
<h1>📞 Hermes Phone</h1>
<div class="sub">Enter your dashboard token</div>
<div class="error" id="error">Invalid token</div>
<form onsubmit="return doLogin(event)">
<div class="fg"><label>Access Token</label><input type="password" id="token" placeholder="••••••••••••••••" autofocus></div>
<button type="submit" class="btn">🔓 Login</button>
</form>
<div class="hint">Find the token in your .env as DASHBOARD_TOKEN</div>
</div>
<script>
async function doLogin(e){
e.preventDefault();
const t=document.getElementById('token').value;
if(!t)return false;
try{
const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})});
const d=await r.json();
if(d.status==='ok'){window.location='/'}else{document.getElementById('error').style.display='block'}
}catch(e){document.getElementById('error').style.display='block'}
return false}
</script></body></html>"""

# Dashboard auth middleware
@dashboard_app.before_request
def require_dashboard_auth():
    path = request.path
    if path in ("/login", "/logout", "/health"):
        return None
    if not DASHBOARD_TOKEN:
        return None  # No token configured = open (first run)
    # Valid session cookie?
    if _session_valid(request.cookies.get(AUTH_COOKIE, "")):
        return None
    # Authorization: Bearer <token> (API clients)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and check_auth(auth[7:]):
        return None
    # One-time ?token= bootstrap (e.g. menubar) → a session cookie is issued below
    token = request.args.get("token", "")
    if token and check_auth(token):
        request._issue_session = True
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "Unauthorized"}), 401
    return Response(LOGIN_HTML, mimetype="text/html", status=401)

# ═══════════════════════════════════════════════════════════════════
# Shared state
# ═══════════════════════════════════════════════════════════════════

call_states = {}

# Voice engine (lazy init)
voice_engine = None
dg_client = None

def init_voice_engine():
    global voice_engine, dg_client
    if USE_LOCAL_VOICE in ("auto", "true", "1"):
        try:
            from local_voice import VoiceEngine
            voice_engine = VoiceEngine(prefer_local=USE_LOCAL_VOICE != "false")
            print(f"  Voice: {voice_engine.mode}")
        except Exception as e:
            if USE_LOCAL_VOICE == "true":
                print(f"  ❌ Local voice failed: {e}")
            else:
                print(f"  ℹ️  Local voice not available, using cloud TTS")

    if DEEPGRAM_KEY and (not voice_engine or not voice_engine.stt):
        try:
            from deepgram import DeepgramClient
            dg_client = DeepgramClient(api_key=DEEPGRAM_KEY)
        except ImportError:
            print("⚠️ Deepgram SDK not installed — STT disabled")

# ═══════════════════════════════════════════════════════════════════
# Voicemail helpers
# ═══════════════════════════════════════════════════════════════════

# Serialises read-modify-write on the voicemail metadata file
_vm_lock = threading.RLock()

def load_voicemails():
    if METADATA_FILE.exists():
        return json.loads(METADATA_FILE.read_text())
    return []

def save_voicemails(voicemails):
    METADATA_FILE.write_text(json.dumps(voicemails, indent=2))


# ═══════════════════════════════════════════════════════════════════
# Caller info helper
# ═══════════════════════════════════════════════════════════════════

def get_caller_info(form_data):
    """Extract the best available caller info from Twilio webhook data.
    
    Checks (in order):
    1. ForwardedFrom — original caller if call was forwarded
    2. From — standard caller number
    3. Caller — can differ in SIP/forwarding scenarios
    4. CallerName — CNAM lookup (US only, requires VoiceCallerIdLookup)
    """
    forwarded = form_data.get("ForwardedFrom", "").strip()
    from_num = form_data.get("From", "").strip()
    caller_num = form_data.get("Caller", "").strip()
    caller_name = form_data.get("CallerName", "").strip()
    
    # Anonymous/restricted indicators
    blocked_indicators = ("anonymous", "restricted", "private", "unavailable", "unknown", "")
    
    # Prefer forwarded number (original caller in forwarding scenario)
    if forwarded and forwarded.lower() not in blocked_indicators:
        return forwarded
    
    # Standard From field
    if from_num and from_num.lower() not in blocked_indicators:
        return from_num
    
    # Caller field fallback
    if caller_num and caller_num.lower() not in blocked_indicators:
        return caller_num
    
    # CallerName (CNAM) — better display than number
    if caller_name:
        return caller_name
    
    return "Unknown Caller"

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
# Webhook URL helper
# ═══════════════════════════════════════════════════════════════════

def get_webhook_base():
    """Get the public URL for Twilio webhooks."""
    if WEBHOOK_URL_OVERRIDE:
        return WEBHOOK_URL_OVERRIDE.rstrip("/")
    host = request.host
    # Strip port if present — webhook URL should be the public-facing host
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    # Use HTTP for localhost/development, HTTPS for production
    scheme = "http" if host in ("localhost", "127.0.0.1") else "https"
    return f"{scheme}://{host}:{WEBHOOK_PORT}"

# ═══════════════════════════════════════════════════════════════════
# LLM — Pluggable agent backend
# ═══════════════════════════════════════════════════════════════════

def get_llm_response(call_sid, user_text):
    """Get response from the configured agent backend."""
    state = call_states.setdefault(call_sid, {"messages": [], "transcript": [], "conversation_id": f"call-{call_sid}"})
    state["transcript"].append({"role": "user", "text": user_text})

    backend = get_agent_backend()
    try:
        reply = backend.chat(
            call_sid=call_sid,
            user_text=user_text,
            system_prompt=get_system_prompt(state.get("goal")),
            conversation_id=state["conversation_id"],
            history=state["messages"],
        )
        state["messages"].append({"role": "user", "content": user_text})
        state["messages"].append({"role": "assistant", "content": reply})
        if len(state["messages"]) > 40:
            state["messages"] = state["messages"][-40:]
        state["transcript"].append({"role": "assistant", "text": reply})
        return reply
    except Exception as e:
        print(f"Agent error: {e}")
        return "Sorry, I'm having technical difficulties."

# ═══════════════════════════════════════════════════════════════════
# TTS
# ═══════════════════════════════════════════════════════════════════

def synthesize_speech(text):
    """Convert text to audio using the best available TTS."""
    if voice_engine and voice_engine.tts:
        audio = voice_engine.speak(text)
        if audio:
            return audio

    if TTS_PROVIDER == "mimo" and XIAOMI_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(base_url=XIAOMI_BASE_URL, api_key=XIAOMI_KEY)
            resp = client.chat.completions.create(
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

    # Cloud TTS fallback — OpenAI /audio/speech as raw PCM (24 kHz) → 8 kHz for
    # Twilio. This covers the common case where the live media-stream voice would
    # otherwise be silent: TTS_PROVIDER=polly only applies to Twilio <Say>
    # greetings, not the streaming conversation, so fall back to OpenAI TTS when a
    # key is available.
    tts_key = env("OPENAI_TTS_API_KEY", OPENAI_KEY)
    if tts_key and TTS_PROVIDER in ("openai", "polly"):
        try:
            base = env("OPENAI_TTS_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            resp = http_requests.post(
                f"{base}/audio/speech",
                headers={"Authorization": f"Bearer {tts_key}"},
                json={
                    "model": env("OPENAI_TTS_MODEL", "tts-1"),
                    "input": text,
                    "voice": env("OPENAI_TTS_VOICE", "alloy"),
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

def send_audio_to_ws(ws, stream_sid, audio_data):
    """Send synthesized audio back to Twilio via WebSocket."""
    try:
        if audio_data[:4] == b"RIFF":
            audio_data = audio_data[44:]
        mulaw = audioop.lin2ulaw(audio_data, 2)
        chunk_size = 160
        for i in range(0, len(mulaw), chunk_size):
            chunk = mulaw[i:i + chunk_size]
            if len(chunk) < chunk_size:
                chunk += b"\xff" * (chunk_size - len(chunk))
            ws.send(json.dumps({
                "event": "media", "streamSid": stream_sid,
                "media": {"payload": base64.b64encode(chunk).decode()},
            }))
        ws.send(json.dumps({
            "event": "mark", "streamSid": stream_sid,
            "mark": {"name": f"resp_{int(time.time())}"},
        }))
    except Exception as e:
        print(f"Audio send error: {e}")

# ═══════════════════════════════════════════════════════════════════
# Deepgram (prerecorded transcription — SDK v7)
# ═══════════════════════════════════════════════════════════════════

def _deepgram_transcribe_file(audio_bytes):
    """Transcribe audio bytes with Deepgram SDK v7 (listen.v1.media). Returns text or ""."""
    if not dg_client:
        return ""
    try:
        response = dg_client.listen.v1.media.transcribe_file(
            request=audio_bytes, model="nova-2", language="en",
            punctuate=True, smart_format=True,
        )
        return response.results.channels[0].alternatives[0].transcript or ""
    except Exception as e:
        print(f"⚠️ Deepgram transcription failed: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
# Telegram notifications
# ═══════════════════════════════════════════════════════════════════

def notify_telegram(recording_url, caller, duration, transcription=""):
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        audio_url = f"{recording_url}.wav"
        r = http_requests.get(audio_url, auth=(TWILIO_SID, TWILIO_TOKEN), timeout=30)
        if r.status_code != 200:
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(r.content)
            wav_path = f.name

        transcript_text = transcription
        if not transcript_text:
            transcript_text = _deepgram_transcribe_file(r.content)

        caller_display = caller.replace("+", "")
        caption = f"📞 Voicemail from {caller_display}\n⏱️ {duration}s"
        if transcript_text:
            caption += f'\n\n📝 "{transcript_text}"'

        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
        with open(wav_path, "rb") as audio_file:
            http_requests.post(tg_url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                              files={"voice": ("voicemail.wav", audio_file, "audio/wav")}, timeout=30)
        try:
            os.unlink(wav_path)
        except:
            pass
    except Exception as e:
        print(f"❌ Telegram notification error: {e}")

# ═══════════════════════════════════════════════════════════════════
# WEBHOOK APP (port 5050 — public, no auth)
# ═══════════════════════════════════════════════════════════════════

@webhook_app.route("/voice/incoming", methods=["POST"])
def handle_incoming():
    call_sid = request.form.get("CallSid", "unknown")
    caller = get_caller_info(request.form)
    print(f"📞 Incoming: {call_sid} from {caller}")

    resp = VoiceResponse()
    greeting = VOICEMAIL_GREETING or f"Thank you for calling {COMPANY_NAME}. Please leave a message after the tone."
    if not VOICEMAIL_GREETING and VOICEMAIL_EMAIL:
        greeting += f" Or email us at {VOICEMAIL_EMAIL}."

    gather = Gather(num_digits=max(len(VOICEMAIL_PIN), 4), action="/voice/check-pin", method="POST",
                    timeout=1, finish_on_key="#")
    gather.say(greeting, voice=TTS_VOICE, language=TTS_LANGUAGE)
    resp.append(gather)

    resp.record(action="/voice/voicemail-complete", method="POST", max_length=VOICEMAIL_MAX_LENGTH,
                play_beep=True, finish_on_key="#",
                recording_status_callback="/voice/recording-ready", recording_status_callback_method="POST")
    resp.say("Goodbye.", voice=TTS_VOICE, language=TTS_LANGUAGE)
    return Response(str(resp), mimetype="text/xml")

@webhook_app.route("/voice/check-pin", methods=["POST"])
def check_pin():
    digits = request.form.get("Digits", "")
    call_sid = request.form.get("CallSid", "unknown")
    caller = get_caller_info(request.form)
    resp = VoiceResponse()
    locked = _pin_locked(caller)
    if (not locked) and VOICEMAIL_PIN and hmac.compare_digest(digits, VOICEMAIL_PIN):
        pin_attempts.pop(caller, None)
        print(f"✅ PIN correct — connecting {caller} to AI")
        resp.say("Connecting you now.", voice=TTS_VOICE, language=TTS_LANGUAGE)
        connect = Connect()
        connect.stream(url=_ws_url())
        resp.append(connect)
    else:
        if not locked:
            _record_pin_fail(caller)
        print(f"❌ PIN rejected for {caller}")
        resp.say("Please leave a message after the tone. Press hash when finished.",
                 voice=TTS_VOICE, language=TTS_LANGUAGE)
        resp.record(action="/voice/voicemail-complete", method="POST", max_length=VOICEMAIL_MAX_LENGTH,
                    play_beep=True, finish_on_key="#",
                    recording_status_callback="/voice/recording-ready", recording_status_callback_method="POST")
    return Response(str(resp), mimetype="text/xml")

@webhook_app.route("/voice/voicemail-complete", methods=["POST"])
def voicemail_complete():
    call_sid = request.form.get("CallSid", "unknown")
    recording_url = request.form.get("RecordingUrl", "")
    recording_sid = request.form.get("RecordingSid", "")
    duration = request.form.get("RecordingDuration", "0")
    caller = get_caller_info(request.form)
    print(f"📩 Voicemail from {caller}: {duration}s, SID={recording_sid}")

    with _vm_lock:
        voicemails = load_voicemails()
        voicemails.append({
            "sid": recording_sid, "from": caller, "duration": int(duration or 0),
            "url": f"{recording_url}.wav", "time": datetime.now().isoformat(),
            "timestamp": time.time(), "transcript": "", "read": False,
        })
        save_voicemails(voicemails)

    resp = VoiceResponse()
    resp.say("Thank you for your message. Goodbye.", voice=TTS_VOICE, language=TTS_LANGUAGE)
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")

@webhook_app.route("/voice/recording-ready", methods=["POST"])
def recording_ready():
    recording_sid = request.form.get("RecordingSid", "")
    recording_url = request.form.get("RecordingUrl", "")
    caller = get_caller_info(request.form)
    duration = request.form.get("RecordingDuration", "0")
    transcription_text = request.form.get("TranscriptionText", "")
    print(f"🎙️ Recording ready: {recording_sid}")
    threading.Thread(target=_process_voicemail,
                     args=(recording_sid, recording_url, caller, duration, transcription_text),
                     daemon=True).start()
    return "", 204

def _process_voicemail(recording_sid, recording_url, caller, duration, transcription_text):
    try:
        audio_url = f"{recording_url}.wav"
        r = http_requests.get(audio_url, auth=(TWILIO_SID, TWILIO_TOKEN), timeout=30)
        if r.status_code != 200:
            return
        audio_path = AUDIO_DIR / f"{recording_sid}.wav"
        audio_path.write_bytes(r.content)

        transcript = transcription_text
        if not transcript and voice_engine and voice_engine.stt:
            try:
                transcript = voice_engine.transcribe(str(audio_path))
            except Exception as e:
                print(f"⚠️ Local STT failed: {e}")
        if not transcript:
            transcript = _deepgram_transcribe_file(r.content)

        with _vm_lock:
            voicemails = load_voicemails()
            for vm in voicemails:
                if vm.get("sid") == recording_sid:
                    vm["transcript"] = transcript
                    vm["audio_path"] = str(audio_path)
                    break
            save_voicemails(voicemails)
        notify_telegram(recording_url, caller, duration, transcript)
        print(f"✅ Voicemail saved: {audio_path}")
    except Exception as e:
        print(f"❌ Voicemail processing error: {e}")

@webhook_app.route("/voice/outgoing", methods=["POST"])
def handle_outgoing():
    call_sid = request.form.get("CallSid", "unknown")
    print(f"📱 Outgoing connected: {call_sid}")
    resp = VoiceResponse()
    connect = Connect()
    connect.stream(url=_ws_url())
    resp.append(connect)
    return Response(str(resp), mimetype="text/xml")

@webhook_app.route("/voice/status", methods=["POST"])
def handle_status():
    call_sid = request.form.get("CallSid", "")
    status = request.form.get("CallStatus", "")
    print(f"📊 {call_sid}: {status}")
    if status in ("completed", "failed", "busy", "no-answer"):
        state = call_states.pop(call_sid, None)
        if state and state.get("transcript"):
            print(f"\n{'='*50}\n📝 TRANSCRIPT:")
            for m in state["transcript"]:
                tag = "🎤" if m["role"] == "user" else "🤖"
                print(f"  {tag} {m['text']}")
            print(f"{'='*50}")
    return "", 204

@webhook_sock.route("/ws/call")
def handle_ws(ws):
    """Bi-directional audio: Twilio ↔ Deepgram STT ↔ LLM ↔ TTS."""
    print("🔌 WebSocket connected")
    stream_sid = None
    call_sid = None

    dg_conn = None
    if dg_client:
        try:
            from deepgram.core.events import EventType
            dg_conn = dg_client.listen.v1.connect(
                model="nova-2-phonecall", encoding="linear16",
                sample_rate=8000, channels=1, punctuate=True, interim_results=True,
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
            elif msg["event"] == "media":
                audio = base64.b64decode(msg["media"]["payload"])
                if dg_conn:
                    dg_conn.send_media(audio)
            elif msg["event"] == "stop":
                break

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

import time as _time
_health_cache = {"data": None, "ts": 0}
_HEALTH_TTL = 30  # seconds

@webhook_app.route("/health", methods=["GET"])
@dashboard_app.route("/health", methods=["GET"])
def health():
    now = _time.time()
    if _health_cache["data"] and now - _health_cache["ts"] < _HEALTH_TTL:
        return jsonify(_health_cache["data"])
    backend = get_agent_backend()
    agent_health = backend.health_check()

    result = {
        "status": "ok",
        "twilio": bool(TWILIO_SID),
        "deepgram": bool(DEEPGRAM_KEY),
        "agent_backend": AGENT_PROVIDER or "auto",
        "agent_ok": agent_health.get("ok", False),
        "agent_model": agent_health.get("model"),
        "voicemails": len(load_voicemails()),
        "webhook_port": WEBHOOK_PORT,
        "dashboard_port": DASHBOARD_PORT,
    }
    _health_cache["data"] = result
    _health_cache["ts"] = now
    return jsonify(result)

# ═══════════════════════════════════════════════════════════════════
# DASHBOARD APP (port 5051 — auth protected)
# ═══════════════════════════════════════════════════════════════════

@dashboard_app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.json or {}
        token = data.get("token", "")
        if check_auth(token):
            resp = jsonify({"status": "ok"})
            resp.set_cookie(AUTH_COOKIE, _new_session(), httponly=True,
                            samesite="Strict", secure=request.is_secure, max_age=SESSION_TTL)
            return resp
        return jsonify({"status": "error"}), 401
    return Response(LOGIN_HTML, mimetype="text/html")

@dashboard_app.route("/logout", methods=["GET"])
def logout():
    dashboard_sessions.pop(request.cookies.get(AUTH_COOKIE, ""), None)
    resp = Response('<script>window.location="/login";</script>', mimetype="text/html")
    resp.delete_cookie(AUTH_COOKIE)
    return resp

@dashboard_app.route("/call", methods=["POST"])
def make_call():
    data = request.json or {}
    to_number = data.get("to", "")
    goal = data.get("goal", CALL_GOAL)
    if not to_number:
        return jsonify({"error": "Missing 'to'"}), 400
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM]):
        return jsonify({"error": "Twilio not configured"}), 500

    webhook_base = env("WEBHOOK_URL_OVERRIDE") or f"https://{request.host}".replace(f":{DASHBOARD_PORT}", f":{WEBHOOK_PORT}")
    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        call = client.calls.create(
            to=to_number, from_=TWILIO_FROM,
            url=f"{webhook_base}/voice/outgoing",
            status_callback=f"{webhook_base}/voice/status",
            status_callback_event=["completed", "failed", "busy", "no-answer"],
            timeout=30,
        )
        # Remember this call's goal so the WS turn uses it (keyed by CallSid),
        # instead of mutating a shared global that is reset before the call connects.
        call_states[call.sid] = {
            "messages": [], "transcript": [],
            "conversation_id": f"call-{call.sid}", "goal": goal,
        }
        return jsonify({"sid": call.sid, "status": call.status, "to": to_number})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@dashboard_app.route("/voicemails", methods=["GET"])
def list_voicemails():
    return jsonify(load_voicemails())

@dashboard_app.route("/voicemails/<sid>", methods=["DELETE"])
def delete_voicemail(sid):
    with _vm_lock:
        voicemails = [vm for vm in load_voicemails() if vm.get("sid") != sid]
        save_voicemails(voicemails)
    audio_path = AUDIO_DIR / f"{sid}.wav"
    if audio_path.exists():
        audio_path.unlink()
    return jsonify({"status": "deleted", "sid": sid})

@dashboard_app.route("/voicemails/<sid>/audio", methods=["GET"])
def serve_voicemail_audio(sid):
    audio_path = AUDIO_DIR / f"{sid}.wav"
    if audio_path.exists():
        return Response(audio_path.read_bytes(), mimetype="audio/wav")
    return jsonify({"error": "Audio not found"}), 404

# ═══════════════════════════════════════════════════════════════════
# Settings API
# ═══════════════════════════════════════════════════════════════════

# All configurable settings with metadata
# STT/TTS provider options for dropdown
STT_PROVIDERS = [
    # Cloud
    {"id": "deepgram", "name": "Deepgram Nova-3", "type": "cloud", "cost": "$0.29/hr", "recommended": True},
    {"id": "mimo-stt", "name": "MiMo 2.5 STT", "type": "cloud", "cost": "Free", "recommended": True},
    {"id": "assemblyai", "name": "AssemblyAI Universal-3", "type": "cloud", "cost": "$0.21/hr"},
    {"id": "google", "name": "Google Cloud STT", "type": "cloud", "cost": "$0.96/hr"},
    {"id": "azure", "name": "Azure Speech", "type": "cloud", "cost": "$1.00/hr"},
    {"id": "groq", "name": "Groq Whisper", "type": "cloud", "cost": "$0.04/hr"},
    {"id": "speechmatics", "name": "Speechmatics", "type": "cloud", "cost": "$0.24/hr"},
    {"id": "whisper", "name": "OpenAI Whisper API", "type": "cloud", "cost": "$0.06/hr"},
    # Local / Offline
    {"id": "mlx-whisper", "name": "mlx-whisper (Apple Silicon)", "type": "local", "cost": "Free", "recommended": True},
    {"id": "faster-whisper", "name": "faster-whisper (CTranslate2)", "type": "local", "cost": "Free"},
    {"id": "whisper.cpp", "name": "whisper.cpp (C/C++)", "type": "local", "cost": "Free"},
    {"id": "vosk", "name": "Vosk (offline, lightweight)", "type": "local", "cost": "Free"},
    {"id": "wav2vec2", "name": "wav2vec2 (Meta)", "type": "local", "cost": "Free"},
    {"id": "canary", "name": "NVIDIA Canary (local)", "type": "local", "cost": "Free"},
]

TTS_PROVIDERS = [
    # Cloud
    {"id": "polly", "name": "AWS Polly", "type": "cloud", "cost": "$16/1M chars"},
    {"id": "elevenlabs", "name": "ElevenLabs", "type": "cloud", "cost": "~$0.30/min"},
    {"id": "openai", "name": "OpenAI TTS", "type": "cloud", "cost": "$15/1M chars"},
    {"id": "azure", "name": "Azure Speech", "type": "cloud", "cost": "$15/1M chars"},
    {"id": "google", "name": "Google Cloud TTS", "type": "cloud", "cost": "$16/1M chars"},
    {"id": "cartesia", "name": "Cartesia Sonic", "type": "cloud", "cost": "~$0.003/credit"},
    {"id": "deepgram_aura", "name": "Deepgram Aura", "type": "cloud", "cost": "$0.03/1K chars"},
    {"id": "fish", "name": "Fish Audio", "type": "cloud", "cost": "~$0.03/min"},
    {"id": "edge", "name": "Edge TTS (free)", "type": "cloud", "cost": "Free"},
    {"id": "mimo", "name": "MiMo 2.5 TTS", "type": "cloud", "cost": "Free", "recommended": True},
    # Local / Offline
    {"id": "kokoro", "name": "Kokoro 82M (MLX)", "type": "local", "cost": "Free", "recommended": True},
    {"id": "piper", "name": "Piper (C++, lightweight)", "type": "local", "cost": "Free"},
    {"id": "coqui", "name": "Coqui XTTS v2", "type": "local", "cost": "Free"},
    {"id": "bark", "name": "Bark (Suno)", "type": "local", "cost": "Free"},
    {"id": "tortoise", "name": "Tortoise TTS", "type": "local", "cost": "Free"},
    {"id": "vits", "name": "VITS / VITS2", "type": "local", "cost": "Free"},
    {"id": "styletts2", "name": "StyleTTS 2", "type": "local", "cost": "Free"},
    {"id": "chattts", "name": "ChatTTS", "type": "local", "cost": "Free"},
    {"id": "sesame", "name": "Sesame CSM", "type": "local", "cost": "Free"},
    {"id": "speecht5", "name": "SpeechT5 (Microsoft)", "type": "local", "cost": "Free"},
]

AGENT_PROVIDERS = [
    {"id": "", "name": "Auto-detect (recommended)", "type": "auto"},
    {"id": "hermes-gateway", "name": "Hermes Agent (Gateway API)", "type": "cloud", "recommended": True},
    {"id": "openai", "name": "OpenAI", "type": "cloud"},
    {"id": "xiaomi", "name": "Xiaomi MiMo", "type": "cloud"},
    {"id": "openrouter", "name": "OpenRouter", "type": "cloud"},
    {"id": "ollama", "name": "Ollama (local)", "type": "local"},
    {"id": "lmstudio", "name": "LM Studio (local)", "type": "local"},
    {"id": "openai-compat", "name": "Custom OpenAI-Compatible", "type": "cloud"},
]

SETTINGS_SCHEMA = {
    "AGENT_PROVIDER": {"label": "Agent Backend", "type": "select", "section": "ai"},
    "COMPANY_NAME": {"label": "Company Name", "type": "text", "section": "company"},
    "VOICEMAIL_EMAIL": {"label": "Voicemail Email", "type": "email", "section": "company"},
    "VOICEMAIL_GREETING": {"label": "Voicemail Greeting", "type": "textarea", "section": "company"},
    "VOICEMAIL_PIN": {"label": "Voicemail PIN", "type": "text", "section": "company"},
    "VOICEMAIL_MAX_LENGTH": {"label": "Max Recording (seconds)", "type": "number", "section": "company"},
    "TTS_VOICE": {"label": "TTS Voice", "type": "select", "section": "voice"},
    "TTS_LANGUAGE": {"label": "Language", "type": "select", "section": "voice"},
    "USE_LOCAL_VOICE": {"label": "Voice Engine", "type": "select", "section": "voice"},
    # STT providers
    "STT_PROVIDER": {"label": "STT Provider", "type": "select", "section": "stt"},
    "DEEPGRAM_API_KEY": {"label": "Deepgram API Key", "type": "password", "section": "stt", "sensitive": True},
    "ASSEMBLYAI_API_KEY": {"label": "AssemblyAI API Key", "type": "password", "section": "stt", "sensitive": True},
    "GROQ_API_KEY": {"label": "Groq API Key", "type": "password", "section": "stt", "sensitive": True},
    "SPEECHMATICS_API_KEY": {"label": "Speechmatics API Key", "type": "password", "section": "stt", "sensitive": True},
    "AZURE_SPEECH_KEY": {"label": "Azure Speech Key", "type": "password", "section": "stt", "sensitive": True, "hint": "Azure Cognitive Services speech key"},
    "AZURE_SPEECH_REGION": {"label": "Azure Speech Region", "type": "text", "section": "stt", "hint": "e.g. eastus, westeurope"},
    "GOOGLE_STT_CREDENTIALS": {"label": "Google STT Credentials File", "type": "text", "section": "stt", "hint": "Absolute path to Google Cloud service account JSON key file"},
    # TTS providers
    "TTS_PROVIDER": {"label": "TTS Provider", "type": "select", "section": "tts"},
    "ELEVENLABS_API_KEY": {"label": "ElevenLabs API Key", "type": "password", "section": "tts", "sensitive": True},
    "ELEVENLABS_VOICE_ID": {"label": "ElevenLabs Voice ID", "type": "text", "section": "tts"},
    "CARTESIA_API_KEY": {"label": "Cartesia API Key", "type": "password", "section": "tts", "sensitive": True},
    "CARTESIA_VOICE_ID": {"label": "Cartesia Voice ID", "type": "text", "section": "tts"},
    "AZURE_TTS_KEY": {"label": "Azure TTS Key", "type": "password", "section": "tts", "sensitive": True, "hint": "Azure Cognitive Services speech key"},
    "AZURE_TTS_REGION": {"label": "Azure TTS Region", "type": "text", "section": "tts", "hint": "e.g. eastus, westeurope"},
    "GOOGLE_TTS_CREDENTIALS": {"label": "Google TTS Credentials File", "type": "text", "section": "tts", "hint": "Absolute path to Google Cloud service account JSON key file"},
    "OPENAI_TTS_API_KEY": {"label": "OpenAI TTS API Key", "type": "password", "section": "tts", "sensitive": True, "hint": "Defaults to OPENAI_API_KEY if blank"},
    "OPENAI_TTS_BASE_URL": {"label": "OpenAI TTS Base URL", "type": "text", "section": "tts", "default": "https://api.openai.com/v1", "hint": "Override for OpenAI-compatible TTS endpoint"},
    "OPENAI_TTS_MODEL": {"label": "OpenAI TTS Model", "type": "text", "section": "tts", "default": "tts-1"},
    "OPENAI_TTS_VOICE": {"label": "OpenAI TTS Voice", "type": "text", "section": "tts", "default": "alloy", "hint": "alloy, echo, fable, onyx, nova, shimmer"},
    "CALL_GOAL": {"label": "Call Goal", "type": "text", "section": "ai"},
    "CALL_SYSTEM_PROMPT": {"label": "System Prompt", "type": "textarea", "section": "ai"},
    # API keys (sensitive)
    "TWILIO_ACCOUNT_SID": {"label": "Twilio Account SID", "type": "password", "section": "twilio", "sensitive": True, "hint": "Starts with AC, 34 chars"},
    "TWILIO_AUTH_TOKEN": {"label": "Twilio Auth Token", "type": "password", "section": "twilio", "sensitive": True, "hint": "32-char secret from console.twilio.com"},
    "TWILIO_PHONE_NUMBER": {"label": "Twilio Phone Number", "type": "tel", "section": "twilio", "hint": "E.164 — country code + number, no spaces, e.g. +1XXXXXXXXXX"},
    "XIAOMI_API_KEY": {"label": "Xiaomi API Key", "type": "password", "section": "llm", "sensitive": True},
    "XIAOMI_BASE_URL": {"label": "Xiaomi Base URL", "type": "text", "section": "llm"},
    "OPENAI_API_KEY": {"label": "OpenAI API Key", "type": "password", "section": "llm", "sensitive": True},
    "OPENAI_BASE_URL": {"label": "OpenAI Base URL", "type": "text", "section": "llm"},
    "OPENROUTER_API_KEY": {"label": "OpenRouter API Key", "type": "password", "section": "llm", "sensitive": True},
    "OPENROUTER_BASE_URL": {"label": "OpenRouter Base URL", "type": "text", "section": "llm", "default": "https://openrouter.ai/api/v1"},
    "OLLAMA_BASE_URL": {"label": "Ollama Base URL", "type": "text", "section": "llm", "default": "http://localhost:11434/v1", "hint": "URL of your local Ollama instance"},
    "LMSTUDIO_BASE_URL": {"label": "LM Studio Base URL", "type": "text", "section": "llm", "default": "http://localhost:1234/v1", "hint": "URL of your local LM Studio instance"},
    "HERMES_GATEWAY_URL": {"label": "Hermes Gateway URL", "type": "text", "section": "hermes"},
    "HERMES_GATEWAY_TOKEN": {"label": "Hermes Gateway Token", "type": "password", "section": "hermes", "sensitive": True},
    "HERMES_MODEL_OVERRIDE": {"label": "Model Override (calls)", "type": "text", "section": "hermes", "hint": "Leave empty for agent default"},
    "LLM_PROVIDER": {"label": "LLM Provider", "type": "select", "section": "llm"},
    "LLM_MODEL": {"label": "LLM Model", "type": "text", "section": "llm"},
    "LLM_BASE_URL_OVERRIDE": {"label": "Custom Base URL", "type": "text", "section": "llm", "hint": "Override base URL for custom OpenAI-compatible endpoint"},
    "LLM_API_KEY_OVERRIDE": {"label": "Custom API Key", "type": "password", "section": "llm", "sensitive": True},
    "LLM_MODEL_OVERRIDE": {"label": "Custom Model", "type": "text", "section": "llm", "hint": "Override model name for custom endpoint"},
    "WEBHOOK_URL_OVERRIDE": {"label": "Webhook URL Override", "type": "text", "section": "network", "hint": "Public https base Twilio reaches you at (tunnel/proxy). Applies live."},
    "WEBHOOK_PORT": {"label": "Webhook Port (Twilio)", "type": "number", "section": "network", "default": "5050", "hint": "Restart required"},
    "DASHBOARD_PORT": {"label": "Dashboard Port", "type": "number", "section": "network", "default": "5051", "hint": "Restart required"},
    "VALIDATE_TWILIO_SIGNATURE": {"label": "Verify Twilio Signatures", "type": "select", "section": "network", "default": "true", "hint": "Reject unsigned webhooks. Set a correct Webhook URL Override first. Applies live."},
    "PIN_MAX_ATTEMPTS": {"label": "PIN Max Attempts", "type": "number", "section": "network", "default": "5", "hint": "Restart required"},
    "PIN_LOCKOUT_WINDOW": {"label": "PIN Lockout Window (seconds)", "type": "number", "section": "network", "default": "600", "hint": "Restart required"},
    "TELEGRAM_BOT_TOKEN": {"label": "Telegram Bot Token", "type": "password", "section": "telegram", "sensitive": True},
    "TELEGRAM_CHAT_ID": {"label": "Telegram Chat ID", "type": "text", "section": "telegram"},
    "DASHBOARD_TOKEN": {"label": "Dashboard Password", "type": "password", "section": "general", "sensitive": True, "hint": "Changing this signs out existing dashboard sessions"},
}

def get_all_settings():
    """Read all settings from .env."""
    settings = {}
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    settings[k.strip()] = v.strip().strip('"').strip("'")
    return settings

def update_setting(key, value):
    """Update a single setting in .env."""
    # Strip CR/LF (prevents injecting extra .env lines) and escape quotes.
    value = str(value).replace("\r", " ").replace("\n", " ")
    safe_value = value.replace('"', '\\"').replace("'", "\\'")
    lines = []
    found = False
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    lines.append(f'{key}="{safe_value}"\n')
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f'\n{key}="{safe_value}"\n')
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)
    os.environ[key] = value

def mask_value(key, value):
    """Mask sensitive values."""
    schema = SETTINGS_SCHEMA.get(key, {})
    if not schema.get("sensitive"):
        return value
    if len(value) > 8:
        return value[:4] + "..." + value[-4:]
    if value:
        return "***"
    return ""

@dashboard_app.route("/api/settings", methods=["GET"])
def api_get_settings():
    settings = get_all_settings()
    result = {}
    for key, schema in SETTINGS_SCHEMA.items():
        val = settings.get(key, "") or schema.get("default", "")
        result[key] = mask_value(key, val) if schema.get("sensitive") else val

    # Service status
    backend = get_agent_backend()
    agent_health = backend.health_check()
    result["_status"] = {
        "twilio": bool(TWILIO_SID),
        "deepgram": bool(DEEPGRAM_KEY),
        "agent_backend": AGENT_PROVIDER or "auto",
        "agent_ok": agent_health.get("ok", False),
        "voice_engine": voice_engine.mode if voice_engine else "none",
    }
    result["_schema"] = SETTINGS_SCHEMA
    result["_stt_providers"] = STT_PROVIDERS
    result["_tts_providers"] = TTS_PROVIDERS
    result["_agent_providers"] = AGENT_PROVIDERS
    result["_available_voices"] = [
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
    return jsonify(result)

@dashboard_app.route("/api/settings", methods=["POST"])
def api_update_settings():
    data = request.json or {}
    updated = []
    deleted = []
    for key, value in data.items():
        if key in SETTINGS_SCHEMA:
            if value == "" and SETTINGS_SCHEMA[key].get("sensitive"):
                # Empty value for sensitive field = clear it
                update_setting(key, "")
                deleted.append(key)
            else:
                update_setting(key, str(value))
                updated.append(key)
    return jsonify({"status": "ok", "updated": updated, "deleted": deleted})

@dashboard_app.route("/api/models", methods=["GET"])
def list_models():
    """List available models from the configured agent backend."""
    backend = get_agent_backend()
    models = {"active": backend.get_models(), "hermes": [], "ollama": [], "lmstudio": []}

    # Also probe common local endpoints for the UI model picker
    for name, url in [("ollama", "http://localhost:11434/api/tags"), ("lmstudio", "http://localhost:1234/v1/models")]:
        try:
            r = http_requests.get(url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                if "models" in data:  # Ollama format
                    models[name] = [m["name"] for m in data["models"]]
                elif "data" in data:  # OpenAI format
                    models[name] = [m["id"] for m in data["data"]]
        except Exception:
            pass

    return jsonify(models)

# ═══════════════════════════════════════════════════════════════════
# Export endpoints
# ═══════════════════════════════════════════════════════════════════

@dashboard_app.route("/export/zip", methods=["GET"])
def export_zip():
    import zipfile, io
    voicemails = load_voicemails()
    if not voicemails:
        return jsonify({"error": "No voicemails to export"}), 404
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("voicemails.json", json.dumps(voicemails, indent=2))
        for vm in voicemails:
            audio_path = AUDIO_DIR / f"{vm['sid']}.wav"
            if audio_path.exists():
                caller = vm.get("from", "unknown").replace("+", "")
                zf.writestr(f"audio/{caller}_{vm['sid']}.wav", audio_path.read_bytes())
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
    return Response(buffer.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": "attachment; filename=dialtone-voicemails.zip"})

@dashboard_app.route("/export/transcripts", methods=["GET"])
def export_transcripts():
    voicemails = load_voicemails()
    lines = [f"Hermes Phone — Voicemail Transcripts", f"Exported: {datetime.now().isoformat()}", "=" * 50, ""]
    for vm in voicemails:
        caller = vm.get("from", "unknown").replace("+", "")
        lines.append(f"From: {caller}")
        lines.append(f"Duration: {vm.get('duration', 0)}s")
        lines.append(f"Time: {vm.get('time', 'unknown')}")
        lines.append(f"Transcript: {vm.get('transcript', '(none)')}")
        lines.append("-" * 50)
    return Response("\n".join(lines), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=dialtone-transcripts.txt"})



@dashboard_app.after_request
def set_auth_cookie_from_token(response):
    """Issue a session cookie after a successful ?token= bootstrap."""
    if getattr(request, "_issue_session", False):
        response.set_cookie(AUTH_COOKIE, _new_session(), httponly=True,
                            samesite="Strict", secure=request.is_secure, max_age=SESSION_TTL)
    return response
# ═══════════════════════════════════════════════════════════════════
# Dashboard HTML (served from port 5051)
# ═══════════════════════════════════════════════════════════════════

DASHBOARD_HTML = open(Path(__file__).parent / "dashboard.html").read() if (Path(__file__).parent / "dashboard.html").exists() else ""
SETTINGS_HTML = open(Path(__file__).parent / "settings.html").read() if (Path(__file__).parent / "settings.html").exists() else ""

@dashboard_app.route("/", methods=["GET"])
def dashboard():
    return Response(DASHBOARD_HTML, mimetype="text/html")

@dashboard_app.route("/settings.html", methods=["GET"])
def settings_page():
    return Response(SETTINGS_HTML, mimetype="text/html")

# ═══════════════════════════════════════════════════════════════════
# Provider Management API
# ═══════════════════════════════════════════════════════════════════

@dashboard_app.route("/api/providers", methods=["GET"])
def list_providers():
    """Get installation status of all STT/TTS providers."""
    return jsonify(get_provider_status())

@dashboard_app.route("/api/providers/install", methods=["POST"])
def install_provider():
    """Auto-install a provider's dependencies."""
    data = request.json or {}
    provider_id = data.get("provider", "")
    
    if provider_id not in PROVIDER_DEPS:
        return jsonify({"error": f"Unknown provider: {provider_id}"}), 400
    
    provider = PROVIDER_DEPS[provider_id]
    install_cmd = provider.get("pip_install") or provider.get("install_cmd")
    
    if not install_cmd:
        return jsonify({"status": "ok", "message": "No install needed"})
    
    # Run install in background thread
    def do_install():
        import subprocess
        try:
            result = subprocess.run(
                install_cmd.split(),
                capture_output=True, text=True, timeout=300
            )
            print(f"Install {provider_id}: {'OK' if result.returncode == 0 else 'FAILED'}")
            if result.returncode != 0:
                print(f"  stderr: {result.stderr[:500]}")
        except Exception as e:
            print(f"Install {provider_id} failed: {e}")
    
    threading.Thread(target=do_install, daemon=True).start()
    return jsonify({"status": "installing", "command": install_cmd})

@dashboard_app.route("/api/providers/models", methods=["GET"])
def list_provider_models():
    """List available models for a provider."""
    provider_id = request.args.get("provider", "")
    if provider_id not in PROVIDER_DEPS:
        return jsonify({"error": "Unknown provider"}), 400
    
    provider = PROVIDER_DEPS[provider_id]
    models = provider.get("models", {})
    return jsonify({"models": models})


# ═══════════════════════════════════════════════════════════════════
# Main — run both servers
# ═══════════════════════════════════════════════════════════════════

def run_dashboard():
    from werkzeug.serving import run_simple
    print(f"  Dashboard: http://localhost:{DASHBOARD_PORT}")
    run_simple("0.0.0.0", DASHBOARD_PORT, dashboard_app, use_reloader=False, threaded=True)

if __name__ == "__main__":
    init_voice_engine()

    print(f"📞 Dialtone — AI Phone Agent")
    print(f"   Company: {COMPANY_NAME}")
    backend = get_agent_backend()
    agent_health = backend.health_check()
    print(f"   Agent: {AGENT_PROVIDER or 'auto'} ({'✅' if agent_health.get('ok') else '⚠️  ' + agent_health.get('error', 'not connected')})")
    print(f"   STT: {'Deepgram' if dg_client else '❌'}")
    print(f"   Twilio: {'✅' if TWILIO_SID else '❌'}")
    print(f"   PIN: {VOICEMAIL_PIN}")
    print(f"   Voicemails: {DATA_DIR}")
    print(f"   Webhook: http://0.0.0.0:{WEBHOOK_PORT}")

    # Start dashboard in background thread
    threading.Thread(target=run_dashboard, daemon=True).start()

    # Run webhook server (main thread)
    webhook_app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)
