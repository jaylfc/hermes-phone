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
import threading
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime

import requests as http_requests
from flask import Flask, request, Response, jsonify
from flask_sock import Sock
from twilio.rest import Client as TwilioClient
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
CALL_GOAL = os.environ.get("CALL_GOAL", "Have a helpful conversation.")
SYSTEM_PROMPT = os.environ.get("CALL_SYSTEM_PROMPT", "")

# Telegram (optional)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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
sock = Sock(app)

# Call state (in-memory)
call_states = {}

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

def get_system_prompt():
    if SYSTEM_PROMPT:
        return SYSTEM_PROMPT
    return f"""You are {COMPANY_NAME}'s AI phone assistant.

GOAL: {CALL_GOAL}

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

    messages = [{"role": "system", "content": get_system_prompt()}]
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

    # Fallback: OpenAI TTS (if key available)
    if OPENAI_KEY:
        try:
            resp = http_requests.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                json={"model": "tts-1", "input": text, "voice": "alloy"},
                timeout=30,
            )
            if resp.status_code == 200:
                # OpenAI returns MP3, convert to PCM
                # For now, return None and let Twilio's <Say> handle it
                pass
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
                from deepgram.options import PrerecordedOptions
                with open(wav_path, "rb") as audio_file:
                    buffer_data = audio_file.read()
                payload = {"buffer": buffer_data}
                options = {"model": "nova-2", "language": "en", "punctuate": True}
                response = dg_client.listen.rest.v("1").transcribe_file(payload, options)
                if response and response.results:
                    transcript_text = response.results.channels[0].alternatives[0].transcript
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
def handle_incoming():
    """Handle incoming call — greeting with PIN capture."""
    call_sid = request.form.get("CallSid", "unknown")
    caller = request.form.get("From", "unknown")
    print(f"📞 Incoming: {call_sid} from {caller}")

    resp = VoiceResponse()

    # Build greeting
    greeting = f"Thank you for calling {COMPANY_NAME}. "
    greeting += "Your call is important to us but unfortunately we are unable to answer right now. "
    greeting += "Please leave a message and we will get back to you as soon as possible"
    if VOICEMAIL_EMAIL:
        greeting += f", or if you prefer, you could email us at {VOICEMAIL_EMAIL}"
    greeting += "."

    # Play greeting while listening for PIN
    gather = Gather(
        num_digits=len(VOICEMAIL_PIN),
        action="/voice/check-pin",
        method="POST",
        timeout=5,
        finish_on_key="#",
    )
    gather.say(greeting, voice="Polly.Amy", language="en-GB")
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

    resp.say("Goodbye.", voice="Polly.Amy", language="en-GB")
    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/check-pin", methods=["POST"])
def check_pin():
    """Check if entered PIN matches."""
    digits = request.form.get("Digits", "")
    call_sid = request.form.get("CallSid", "unknown")
    caller = request.form.get("From", "unknown")
    print(f"🔑 PIN check: {digits} (expected {VOICEMAIL_PIN})")

    resp = VoiceResponse()

    if digits == VOICEMAIL_PIN:
        print(f"✅ PIN correct — connecting {caller} to AI")
        resp.say("Connecting you now.", voice="Polly.Amy", language="en-GB")
        connect = Connect()
        connect.stream(url=f"wss://{request.host}/ws/call")
        resp.append(connect)
    else:
        # Wrong PIN → voicemail (no hint that a PIN exists)
        print(f"❌ Wrong PIN: {digits}")
        resp.say(
            "Please leave a message after the tone. Press hash when finished.",
            voice="Polly.Amy", language="en-GB",
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
def voicemail_complete():
    """After voicemail recording is done."""
    call_sid = request.form.get("CallSid", "unknown")
    recording_url = request.form.get("RecordingUrl", "")
    recording_sid = request.form.get("RecordingSid", "")
    duration = request.form.get("RecordingDuration", "0")
    caller = request.form.get("From", "unknown")

    print(f"📩 Voicemail from {caller}: {duration}s, SID={recording_sid}")

    # Store voicemail metadata
    voicemails = load_voicemails()
    voicemails.append({
        "sid": recording_sid,
        "from": caller,
        "duration": int(duration),
        "url": f"{recording_url}.wav",
        "time": datetime.now().isoformat(),
        "timestamp": time.time(),
        "transcript": "",
        "read": False,
    })
    save_voicemails(voicemails)

    resp = VoiceResponse()
    resp.say("Thank you for your message. Goodbye.", voice="Polly.Amy", language="en-GB")
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/recording-ready", methods=["POST"])
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
                payload = {"buffer": r.content}
                options = {"model": "nova-2", "language": "en", "punctuate": True}
                response = dg_client.listen.rest.v("1").transcribe_file(payload, options)
                if response and response.results:
                    transcript = response.results.channels[0].alternatives[0].transcript
            except Exception as e:
                print(f"⚠️ Deepgram transcription failed: {e}")

        # Update metadata with transcript
        voicemails = load_voicemails()
        for vm in voicemails:
            if vm["sid"] == recording_sid:
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
def handle_outgoing():
    """Handle outgoing call — connect to AI."""
    call_sid = request.form.get("CallSid", "unknown")
    print(f"📱 Outgoing connected: {call_sid}")

    resp = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{request.host}/ws/call")
    resp.append(connect)
    return Response(str(resp), mimetype="text/xml")


@app.route("/voice/status", methods=["POST"])
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
def make_call():
    """Make an outbound call."""
    global CALL_GOAL
    data = request.json or {}
    to_number = data.get("to", "")
    goal = data.get("goal", CALL_GOAL)

    if not to_number:
        return jsonify({"error": "Missing 'to'"}), 400
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM]):
        return jsonify({"error": "Twilio not configured"}), 500

    old_goal = CALL_GOAL
    CALL_GOAL = goal

    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        call = client.calls.create(
            to=to_number,
            from_=TWILIO_FROM,
            url=f"https://{request.host}/voice/outgoing",
            status_callback=f"https://{request.host}/voice/status",
            status_callback_event=["completed", "failed", "busy", "no-answer"],
            timeout=30,
        )
        CALL_GOAL = old_goal
        return jsonify({"sid": call.sid, "status": call.status, "to": to_number})
    except Exception as e:
        CALL_GOAL = old_goal
        return jsonify({"error": str(e)}), 500


@app.route("/voicemails", methods=["GET"])
def list_voicemails():
    """List all voicemails."""
    return jsonify(load_voicemails())


@app.route("/voicemails/<sid>", methods=["DELETE"])
def delete_voicemail(sid):
    """Delete a voicemail."""
    voicemails = load_voicemails()
    voicemails = [vm for vm in voicemails if vm["sid"] != sid]
    save_voicemails(voicemails)

    # Delete audio file
    audio_path = AUDIO_DIR / f"{sid}.wav"
    if audio_path.exists():
        audio_path.unlink()

    return jsonify({"status": "deleted", "sid": sid})


@app.route("/health", methods=["GET"])
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
# Web Dashboard
# ═══════════════════════════════════════════════════════════════════

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
    </style>
</head>
<body>
    <div class="header">
        <h1>📞 Hermes Phone</h1>
        <div class="subtitle">AI-powered phone agent</div>
    </div>
    <div class="status-bar" id="status-bar">
        <div class="status-item"><div class="dot" id="status-dot"></div><span id="status-text">Loading...</span></div>
    </div>
    <div class="container">
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
    <script>
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
            alert(d.sid ? 'Calling ' + num + '...' : 'Error: ' + d.error);
        }
        function exportAll() { window.location = '/export/zip'; }
        function exportTranscripts() { window.location = '/export/transcripts'; }
        loadStatus(); loadVoicemails();
        setInterval(() => { loadStatus(); loadVoicemails(); }, 15000);
    </script>
</body>
</html>"""


@app.route("/", methods=["GET"])
def dashboard():
    """Web dashboard."""
    return Response(DASHBOARD_HTML, mimetype="text/html")


@app.route("/voicemails/<sid>/audio", methods=["GET"])
def serve_voicemail_audio(sid):
    """Serve voicemail audio file."""
    audio_path = AUDIO_DIR / f"{sid}.wav"
    if audio_path.exists():
        return Response(audio_path.read_bytes(), mimetype="audio/wav")
    return jsonify({"error": "Audio not found"}), 404


@app.route("/export/zip", methods=["GET"])
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
    print(f"   PIN: {VOICEMAIL_PIN}")
    print(f"   Voicemails: {DATA_DIR}")
    print(f"   Listening on 0.0.0.0:5050")
    app.run(host="0.0.0.0", port=5050, debug=True)
