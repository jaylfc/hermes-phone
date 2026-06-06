"""
Hermes Phone — macOS Menu Bar App

Single phone icon that changes color:
  🟢 = server running
  🔴 = server stopped

Native settings panel via pywebview (not a browser redirect).
"""

import os
import sys
import json
import subprocess
import time
import threading
import webbrowser
from pathlib import Path
from datetime import datetime

import requests
import rumps

# Hide dock icon — menu bar only app
try:
    import AppKit
    NSApplication = AppKit.NSApplication
    NSApplicationActivationPolicyAccessory = 1
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
except ImportError:
    pass  # pyobjc not available, dock icon will show

# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

AGENT_DIR = Path(__file__).parent
ICON_DIR = AGENT_DIR / "icons"
ICON_GREEN = str(ICON_DIR / "phone_green.png")
ICON_RED = str(ICON_DIR / "phone_red.png")
HEALTH_URL = "http://localhost:5050/health"
VOICEMAILS_URL = "http://localhost:5051/voicemails"
SETTINGS_URL = "http://localhost:5051/api/settings"
CALL_URL = "http://localhost:5051/call"
MODELS_URL = "http://localhost:5051/api/models"
CHECK_INTERVAL = 10

# Read dashboard token from .env for API auth
def _load_dashboard_token():
    env_path = AGENT_DIR / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DASHBOARD_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

DASHBOARD_TOKEN = _load_dashboard_token()

def api_headers():
    if DASHBOARD_TOKEN:
        return {"Authorization": f"Bearer {DASHBOARD_TOKEN}"}
    return {}

# ═══════════════════════════════════════════════════════════════════
# Settings HTML (served in pywebview)
# ═══════════════════════════════════════════════════════════════════

SETTINGS_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>Hermes Phone Settings</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#1a1a1a;color:#e0e0e0;padding:20px;font-size:13px}
h1{font-size:18px;margin-bottom:4px}
.sub{color:#888;font-size:12px;margin-bottom:20px}
.section{margin-bottom:24px}
.section h2{font-size:14px;color:#3b82f6;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #333}
.row{display:flex;gap:12px;margin-bottom:12px}
.fg{flex:1;margin-bottom:12px}
.fg label{display:block;font-size:11px;color:#888;margin-bottom:4px;font-weight:500;text-transform:uppercase;letter-spacing:0.5px}
.fg input,.fg select,.fg textarea{width:100%;padding:8px 10px;border-radius:6px;border:1px solid #333;background:#111;color:#e0e0e0;font-size:13px;font-family:inherit}
.fg textarea{min-height:60px;resize:vertical}
.fg select{cursor:pointer}
.fg .hint{font-size:10px;color:#555;margin-top:3px}
.fg input:focus,.fg select:focus,.fg textarea:focus{outline:none;border-color:#3b82f6}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:500}
.badge.green{background:#065f46;color:#6ee7b7}
.badge.red{background:#7f1d1d;color:#fca5a5}
.badge.yellow{background:#78350f;color:#fcd34d}
.badge.blue{background:#1e3a5f;color:#93c5fd}
.status-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px}
.status-item{display:flex;align-items:center;gap:6px;font-size:12px}
.dot{width:8px;height:8px;border-radius:50%}
.dot.green{background:#4ade80;box-shadow:0 0 6px #4ade80}
.dot.red{background:#f87171}
.dot.yellow{background:#fbbf24}
.btn{padding:8px 16px;border-radius:6px;border:1px solid #444;background:#222;color:#e0e0e0;font-size:12px;cursor:pointer;transition:all .15s}
.btn:hover{background:#333;border-color:#666}
.btn.primary{background:#1d4ed8;border-color:#1d4ed8;color:#fff}
.btn.primary:hover{background:#2563eb}
.btn.danger{border-color:#991b1b;color:#f87171}
.btn.danger:hover{background:#991b1b;color:#fff}
.save-bar{position:sticky;bottom:0;padding:12px 0;background:linear-gradient(transparent,#1a1a1a 20%);display:flex;justify-content:flex-end;gap:8px}
.toast{position:fixed;bottom:16px;right:16px;padding:10px 16px;border-radius:6px;font-size:12px;z-index:100;animation:fadeIn .2s}
.toast.success{background:#065f46;color:#6ee7b7;border:1px solid #059669}
.toast.error{background:#7f1d1d;color:#fca5a5;border:1px solid #dc2626}
@keyframes fadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.model-list{max-height:120px;overflow-y:auto;background:#111;border:1px solid #333;border-radius:6px;padding:8px;margin-top:4px}
.model-item{padding:4px 6px;border-radius:4px;font-size:11px;cursor:pointer;display:flex;justify-content:space-between;align-items:center}
.model-item:hover{background:#222}
.model-item.selected{background:#1e3a5f;color:#93c5fd}
.model-item .cost{color:#666;font-size:10px}
.tabs{display:flex;gap:0;margin-bottom:16px;border-bottom:1px solid #333}
.tab{padding:8px 16px;color:#888;font-size:12px;cursor:pointer;border-bottom:2px solid transparent}
.tab:hover{color:#e0e0e0}
.tab.active{color:#fff;border-bottom-color:#3b82f6}
.page{display:none}.page.active{display:block}
.key-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.key-row input{flex:1}
.key-row .btn{padding:4px 8px;font-size:11px}
</style></head><body>
<h1>📞 Hermes Phone Settings</h1>
<div class="sub">Configure your AI phone agent</div>

<div class="tabs">
<div class="tab active" onclick="showTab('general')">General</div>
<div class="tab" onclick="showTab('voice')">Voice</div>
<div class="tab" onclick="showTab('ai')">AI Agent</div>
<div class="tab" onclick="showTab('providers')">Providers</div>
<div class="tab" onclick="showTab('network')">Network</div>
</div>

<!-- General Tab -->
<div class="page active" id="tab-general">
<div class="section">
<h2>Service Status</h2>
<div class="status-grid" id="status-grid">Loading...</div>
</div>
<div class="section">
<h2>Company & Voicemail</h2>
<div class="row">
<div class="fg"><label>Company Name</label><input id="set-COMPANY_NAME" placeholder="My Company"></div>
<div class="fg"><label>Voicemail Email</label><input id="set-VOICEMAIL_EMAIL" placeholder="hello@company.com"></div>
</div>
<div class="fg"><label>Voicemail Greeting</label><textarea id="set-VOICEMAIL_GREETING" placeholder="Leave empty for default"></textarea>
<div class="hint">Played to all callers. Leave empty for: 'Thank you for calling [company]...'</div></div>
<div class="row">
<div class="fg"><label>Voicemail PIN</label><input id="set-VOICEMAIL_PIN" placeholder="1234">
<div class="hint">Callers dial this during greeting to reach AI</div></div>
<div class="fg"><label>Max Recording (sec)</label><input type="number" id="set-VOICEMAIL_MAX_LENGTH" placeholder="120"></div>
</div>
</div>
</div>

<!-- Voice Tab -->
<div class="page" id="tab-voice">
<div class="section">
<h2>Speech-to-Text (STT)</h2>
<div class="fg"><label>STT Provider</label><select id="set-STT_PROVIDER"></select></div>
<div class="key-row">
<div class="fg"><label>Deepgram API Key</label><input type="password" id="set-DEEPGRAM_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('DEEPGRAM_API_KEY')">Clear</button>
</div>
<div class="key-row">
<div class="fg"><label>AssemblyAI API Key</label><input type="password" id="set-ASSEMBLYAI_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('ASSEMBLYAI_API_KEY')">Clear</button>
</div>
<div class="key-row">
<div class="fg"><label>Groq API Key</label><input type="password" id="set-GROQ_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('GROQ_API_KEY')">Clear</button>
</div>
<div class="key-row">
<div class="fg"><label>Speechmatics API Key</label><input type="password" id="set-SPEECHMATICS_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('SPEECHMATICS_API_KEY')">Clear</button>
</div>
</div>
<div class="section">
<h2>Text-to-Speech (TTS)</h2>
<div class="fg"><label>TTS Provider</label><select id="set-TTS_PROVIDER"></select></div>
<div class="fg"><label>TTS Voice</label><select id="set-TTS_VOICE"></select></div>
<div class="fg"><label>Language</label><select id="set-TTS_LANGUAGE">
<option value="en-GB">English (UK)</option><option value="en-US">English (US)</option><option value="en-AU">English (AU)</option>
</select></div>
<div class="key-row">
<div class="fg"><label>ElevenLabs API Key</label><input type="password" id="set-ELEVENLABS_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('ELEVENLABS_API_KEY')">Clear</button>
</div>
<div class="fg"><label>ElevenLabs Voice ID</label><input id="set-ELEVENLABS_VOICE_ID" placeholder="21m00Tcm4TlvDq8ikWAM"></div>
<div class="key-row">
<div class="fg"><label>Cartesia API Key</label><input type="password" id="set-CARTESIA_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('CARTESIA_API_KEY')">Clear</button>
</div>
<div class="fg"><label>Cartesia Voice ID</label><input id="set-CARTESIA_VOICE_ID" placeholder="sonic-voice-id"></div>
<div class="fg"><label>Voice Engine (Local)</label><select id="set-USE_LOCAL_VOICE">
<option value="auto">Auto (local if available)</option><option value="true">Local Only (MLX)</option><option value="false">Cloud Only</option>
</select></div>
</div>
</div>

<!-- AI Agent Tab -->
<div class="page" id="tab-ai">
<div class="section">
<h2>Hermes Gateway (Recommended)</h2>
<div class="fg"><label>Gateway URL</label><input id="set-HERMES_GATEWAY_URL" placeholder="http://127.0.0.1:8642"></div>
<div class="key-row">
<div class="fg"><label>Gateway Token</label><input type="password" id="set-HERMES_GATEWAY_TOKEN" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('HERMES_GATEWAY_TOKEN')">Clear</button>
</div>
<div class="fg"><label>Model Override</label><input id="set-HERMES_MODEL_OVERRIDE" placeholder="Leave empty for agent default">
<div class="hint">e.g. anthropic/claude-sonnet-4, openai/gpt-4o</div></div>
<div id="model-discovery"><div class="hint">Loading available models...</div></div>
</div>
<div class="section">
<h2>Legacy LLM (Fallback)</h2>
<div class="row">
<div class="fg"><label>Provider</label><select id="set-LLM_PROVIDER">
<option value="xiaomi">Xiaomi MiMo</option><option value="openai">OpenAI</option><option value="openrouter">OpenRouter</option>
</select></div>
<div class="fg"><label>Model</label><input id="set-LLM_MODEL" placeholder="mimo-v2.5"></div>
</div>
<div class="key-row">
<div class="fg"><label>Xiaomi API Key</label><input type="password" id="set-XIAOMI_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('XIAOMI_API_KEY')">Clear</button>
</div>
<div class="fg"><label>Xiaomi Base URL</label><input id="set-XIAOMI_BASE_URL" placeholder="https://token-plan-ams.xiaomimimo.com/v1"></div>
<div class="key-row">
<div class="fg"><label>OpenAI API Key</label><input type="password" id="set-OPENAI_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('OPENAI_API_KEY')">Clear</button>
</div>
<div class="fg"><label>OpenAI Base URL</label><input id="set-OPENAI_BASE_URL" placeholder="https://api.openai.com/v1"></div>
<div class="key-row">
<div class="fg"><label>OpenRouter API Key</label><input type="password" id="set-OPENROUTER_API_KEY" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('OPENROUTER_API_KEY')">Clear</button>
</div>
</div>
<div class="section">
<h2>Agent Behavior</h2>
<div class="fg"><label>Call Goal</label><input id="set-CALL_GOAL" placeholder="Have a helpful conversation."></div>
<div class="fg"><label>System Prompt</label><textarea id="set-CALL_SYSTEM_PROMPT" placeholder="Leave empty for default"></textarea></div>
</div>
</div>

<!-- Providers Tab -->
<div class="page" id="tab-providers">
<div class="section">
<h2>Twilio</h2>
<div class="fg"><label>Account SID</label><input id="set-TWILIO_ACCOUNT_SID" placeholder="ACxxxxxxxx"></div>
<div class="key-row">
<div class="fg"><label>Auth Token</label><input type="password" id="set-TWILIO_AUTH_TOKEN" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('TWILIO_AUTH_TOKEN')">Clear</button>
</div>
<div class="fg"><label>Phone Number</label><input id="set-TWILIO_PHONE_NUMBER" placeholder="+443xxxxxxxxx"></div>
</div>
<div class="section">
<h2>Telegram (Optional)</h2>
<div class="key-row">
<div class="fg"><label>Bot Token</label><input type="password" id="set-TELEGRAM_BOT_TOKEN" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('TELEGRAM_BOT_TOKEN')">Clear</button>
</div>
<div class="fg"><label>Chat ID</label><input id="set-TELEGRAM_CHAT_ID" placeholder="Your chat ID"></div>
</div>
</div>

<!-- Network Tab -->
<div class="page" id="tab-network">
<div class="section">
<h2>Ports</h2>
<div class="row">
<div class="fg"><label>Webhook Port (public)</label><input type="number" id="set-WEBHOOK_PORT" placeholder="5050"></div>
<div class="fg"><label>Dashboard Port (protected)</label><input type="number" id="set-DASHBOARD_PORT" placeholder="5051"></div>
</div>
</div>
<div class="section">
<h2>Webhook URL Override</h2>
<div class="fg"><label>Override URL</label><input id="set-WEBHOOK_URL_OVERRIDE" placeholder="Leave empty to auto-detect">
<div class="hint">Use if behind a proxy (e.g. https://phone.example.com)</div></div>
</div>
<div class="section">
<h2>Dashboard Security</h2>
<div class="key-row">
<div class="fg"><label>Dashboard Token</label><input type="password" id="set-DASHBOARD_TOKEN" placeholder="••••••••"></div>
<button class="btn danger" onclick="clearKey('DASHBOARD_TOKEN')">Clear</button>
</div>
</div>
</div>

<div class="save-bar">
<button class="btn" onclick="loadSettings()">Reset</button>
<button class="btn primary" onclick="saveSettings()">💾 Save Settings</button>
</div>

<script>
const API = 'http://localhost:5051';
const HEADERS = {'Content-Type':'application/json','Authorization':'Bearer '+pywebview.api.get_token()};

function showTab(name){
document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
document.getElementById('tab-'+name).classList.add('active');
event.target.classList.add('active');
}

function toast(msg,type='success'){
const el=document.createElement('div');el.className='toast '+type;el.textContent=msg;
document.body.appendChild(el);setTimeout(()=>el.remove(),3000);
}

function clearKey(key){
const el=document.getElementById('set-'+key);
if(el){el.value='';el.placeholder='Cleared — save to confirm'}
}

let settings={};

// Provider status
let providerStatus={};

async function loadProviders(){
try{
const r=await fetch(API+'/api/providers',{headers:{'Authorization':'Bearer '+pywebview.api.get_token()}});
providerStatus=await r.json();
updateProviderDropdowns();
}catch(e){console.error('Load providers error:',e)}
}

function updateProviderDropdowns(){
// STT
const sttSelect=document.getElementById('set-STT_PROVIDER');
if(sttSelect){
const currentVal=sttSelect.value;
sttSelect.innerHTML='';
Object.entries(providerStatus).filter(([k,v])=>v.type==='stt').forEach(([k,v])=>{
const opt=document.createElement('option');
opt.value=k;
const status=v.installed?'✓':'✗ needs install';
const rec=v.recommended?' ⭐':'';
opt.textContent=`${v.name} (${v.backend}, ${status})${rec}`;
if(!v.installed)opt.style.color='#f87171';
if(k===currentVal)opt.selected=true;
sttSelect.appendChild(opt);
});
sttSelect.onchange=()=>autoInstall(sttSelect.value);
}
// TTS
const ttsSelect=document.getElementById('set-TTS_PROVIDER');
if(ttsSelect){
const currentVal=ttsSelect.value;
ttsSelect.innerHTML='';
Object.entries(providerStatus).filter(([k,v])=>v.type==='tts').forEach(([k,v])=>{
const opt=document.createElement('option');
opt.value=k;
const status=v.installed?'✓':'✗ needs install';
const rec=v.recommended?' ⭐':'';
opt.textContent=`${v.name} (${v.backend}, ${status})${rec}`;
if(!v.installed)opt.style.color='#f87171';
if(k===currentVal)opt.selected=true;
ttsSelect.appendChild(opt);
});
ttsSelect.onchange=()=>autoInstall(ttsSelect.value);
}
}

async function autoInstall(providerId){
const p=providerStatus[providerId];
if(!p||p.installed)return;
if(!confirm(`${p.name} is not installed. Install now?`))return;
try{
const r=await fetch(API+'/api/providers/install',{
method:'POST',headers:HEADERS,body:JSON.stringify({provider:providerId})
});
const d=await r.json();
toast(`Installing ${p.name}...`,'success');
// Poll for completion
setTimeout(()=>loadProviders(),10000);
}catch(e){toast('Install failed: '+e,'error')}
}

async function loadSettings(){
try{
const r=await fetch(API+'/api/settings',{headers:{'Authorization':'Bearer '+pywebview.api.get_token()}});
settings=await r.json();

// Populate all fields
const allFields=['COMPANY_NAME','VOICEMAIL_EMAIL','VOICEMAIL_GREETING','VOICEMAIL_PIN','VOICEMAIL_MAX_LENGTH',
'TTS_VOICE','TTS_LANGUAGE','USE_LOCAL_VOICE','STT_PROVIDER','TTS_PROVIDER',
'CALL_GOAL','CALL_SYSTEM_PROMPT',
'TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_PHONE_NUMBER',
'DEEPGRAM_API_KEY','ASSEMBLYAI_API_KEY','GROQ_API_KEY','SPEECHMATICS_API_KEY',
'ELEVENLABS_API_KEY','ELEVENLABS_VOICE_ID','CARTESIA_API_KEY','CARTESIA_VOICE_ID',
'HERMES_GATEWAY_URL','HERMES_GATEWAY_TOKEN','HERMES_MODEL_OVERRIDE',
'LLM_PROVIDER','LLM_MODEL','XIAOMI_API_KEY','XIAOMI_BASE_URL',
'OPENAI_API_KEY','OPENAI_BASE_URL','OPENROUTER_API_KEY',
'WEBHOOK_URL_OVERRIDE','WEBHOOK_PORT','DASHBOARD_PORT',
'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','DASHBOARD_TOKEN'];

allFields.forEach(f=>{
const el=document.getElementById('set-'+f);
if(el&&settings[f]!==undefined)el.value=settings[f];
});

// Populate STT provider dropdown
const sttSelect=document.getElementById('set-STT_PROVIDER');
const sttProviders=settings._stt_providers||[];
sttSelect.innerHTML=sttProviders.map(p=>
`<option value="${p.id}" ${p.id===settings.STT_PROVIDER?'selected':''}>${p.name} (${p.type}, ${p.cost})${p.recommended?' ⭐':''}</option>`
).join('');

// Populate TTS provider dropdown
const ttsSelect=document.getElementById('set-TTS_PROVIDER');
const ttsProviders=settings._tts_providers||[];
ttsSelect.innerHTML=ttsProviders.map(p=>
`<option value="${p.id}" ${p.id===settings.TTS_PROVIDER?'selected':''}>${p.name} (${p.type}, ${p.cost})${p.recommended?' ⭐':''}</option>`
).join('');

// Populate TTS voice dropdown
const voiceSelect=document.getElementById('set-TTS_VOICE');
const voices=settings._available_voices||[];
voiceSelect.innerHTML=voices.map(v=>
`<option value="${v.id}" ${v.id===settings.TTS_VOICE?'selected':''}>${v.name} (${v.lang}, ${v.gender})</option>`
).join('');

// Status
const st=settings._status||{};
document.getElementById('status-grid').innerHTML=`
<div class="status-item"><div class="dot ${st.twilio?'green':'red'}"></div>Twilio: ${st.twilio?'Connected':'Not configured'}</div>
<div class="status-item"><div class="dot ${st.deepgram?'green':'red'}"></div>Deepgram: ${st.deepgram?'Connected':'Not configured'}</div>
<div class="status-item"><div class="dot ${st.hermes_gateway?'green':'yellow'}"></div>Hermes Gateway: ${st.hermes_gateway?'Connected':'Not configured'}</div>
<div class="status-item"><div class="dot ${st.voice_engine?.includes('local')?'green':'yellow'}"></div>Voice: ${st.voice_engine||'none'}</div>
`;

// Load models
loadModels();
}catch(e){console.error('Load settings error:',e)}
}

async function loadModels(){
try{
const r=await fetch(API+'/api/models',{headers:{'Authorization':'Bearer '+pywebview.api.get_token()}});
const models=await r.json();
let html='<div class="fg"><label>Available Models</label>';

if(models.hermes&&models.hermes.length){
html+='<div class="model-list">';
models.hermes.forEach(m=>{
const selected=settings.HERMES_MODEL_OVERRIDE===m?' selected':'';
html+=`<div class="model-item${selected}" onclick="selectModel('${m}')">${m}</div>`;
});
html+='</div>';
}
if(models.ollama&&models.ollama.length){
html+='<div style="margin-top:8px;font-size:11px;color:#888">Ollama:</div><div class="model-list">';
models.ollama.forEach(m=>{html+=`<div class="model-item" onclick="selectModel('ollama/${m}')">${m} <span class="cost">local</span></div>`});
html+='</div>';
}
if(models.lmstudio&&models.lmstudio.length){
html+='<div style="margin-top:8px;font-size:11px;color:#888">LM Studio:</div><div class="model-list">';
models.lmstudio.forEach(m=>{html+=`<div class="model-item" onclick="selectModel('${m}')">${m} <span class="cost">local</span></div>`});
html+='</div>';
}
if(!models.hermes?.length&&!models.ollama?.length&&!models.lmstudio?.length){
html+='<div class="hint">No models discovered. Start Hermes Gateway, Ollama, or LM Studio.</div>';
}
html+='</div>';
document.getElementById('model-discovery').innerHTML=html;
}catch(e){document.getElementById('model-discovery').innerHTML='<div class="hint">Could not load models</div>'}
}

function selectModel(model){
document.getElementById('set-HERMES_MODEL_OVERRIDE').value=model;
document.querySelectorAll('.model-item').forEach(el=>{
el.classList.toggle('selected',el.textContent.includes(model));
});
}

async function saveSettings(){
const allFields=['COMPANY_NAME','VOICEMAIL_EMAIL','VOICEMAIL_GREETING','VOICEMAIL_PIN','VOICEMAIL_MAX_LENGTH',
'TTS_VOICE','TTS_LANGUAGE','USE_LOCAL_VOICE','STT_PROVIDER','TTS_PROVIDER',
'CALL_GOAL','CALL_SYSTEM_PROMPT',
'TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_PHONE_NUMBER',
'DEEPGRAM_API_KEY','ASSEMBLYAI_API_KEY','GROQ_API_KEY','SPEECHMATICS_API_KEY',
'ELEVENLABS_API_KEY','ELEVENLABS_VOICE_ID','CARTESIA_API_KEY','CARTESIA_VOICE_ID',
'HERMES_GATEWAY_URL','HERMES_GATEWAY_TOKEN','HERMES_MODEL_OVERRIDE',
'LLM_PROVIDER','LLM_MODEL','XIAOMI_API_KEY','XIAOMI_BASE_URL',
'OPENAI_API_KEY','OPENAI_BASE_URL','OPENROUTER_API_KEY',
'WEBHOOK_URL_OVERRIDE','WEBHOOK_PORT','DASHBOARD_PORT',
'TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','DASHBOARD_TOKEN'];

const data={};
allFields.forEach(f=>{
const el=document.getElementById('set-'+f);
if(el)data[f]=el.value;
});

try{
const r=await fetch(API+'/api/settings',{
method:'POST',headers:HEADERS,body:JSON.stringify(data)
});
const result=await r.json();
toast('Settings saved! Restart server for some changes.','success');
pywebview.api.on_saved();
}catch(e){toast('Failed to save: '+e,'error')}
}

loadSettings();
loadProviders();
</script></body></html>"""

# ═══════════════════════════════════════════════════════════════════
# pywebview API (Python↔JS bridge)
# ═══════════════════════════════════════════════════════════════════

class SettingsApi:
    def __init__(self):
        self.window = None

    def get_token(self):
        return DASHBOARD_TOKEN

    def on_saved(self):
        """Called from JS after settings are saved."""
        pass

# ═══════════════════════════════════════════════════════════════════
# Menu Bar App
# ═══════════════════════════════════════════════════════════════════

class PhoneMenuBar(rumps.App):
    def __init__(self):
        # Start with red icon (not running)
        icon = ICON_RED if Path(ICON_RED).exists() else None
        super().__init__(name="Hermes Phone", title="", icon=icon, quit_button=None)
        self.running = False
        self.voicemails = []
        self.health_data = {}
        self.settings_api = SettingsApi()

        # Menu items
        self.status_item = rumps.MenuItem("Checking...")
        self.start_item = rumps.MenuItem("Start Server", callback=self.start_server)
        self.stop_item = rumps.MenuItem("Stop Server", callback=self.stop_server)
        self.restart_item = rumps.MenuItem("Restart Server", callback=self.restart_server)
        self.call_item = rumps.MenuItem("📞 Make Call...", callback=self.make_call)
        self.vm_menu = rumps.MenuItem("🎙️ Voicemails")
        self.settings_item = rumps.MenuItem("⚙️ Settings...", callback=self.open_settings)
        self.dash_item = rumps.MenuItem("🌐 Open Dashboard", callback=self.open_dashboard)
        self.quit_item = rumps.MenuItem("Quit", callback=self.quit_app)

        # Build menu
        self.menu = [
            self.status_item,
            rumps.separator,
            self.start_item,
            self.stop_item,
            self.restart_item,
            rumps.separator,
            self.call_item,
            self.vm_menu,
            rumps.separator,
            self.settings_item,
            self.dash_item,
            rumps.separator,
            self.quit_item,
        ]

        # Background health check
        self._start_health_check()

    def _start_health_check(self):
        def check():
            while True:
                try:
                    r = requests.get(HEALTH_URL, timeout=3)
                    if r.status_code == 200:
                        self._update_running(True, r.json())
                    else:
                        self._update_running(False)
                except:
                    self._update_running(False)
                time.sleep(CHECK_INTERVAL)
        threading.Thread(target=check, daemon=True).start()

    def _update_running(self, running, data=None):
        self.running = running
        if data:
            self.health_data = data
        # Update icon color
        icon_path = ICON_GREEN if running else ICON_RED
        if Path(icon_path).exists():
            self.icon = icon_path
        self.title = ""  # No text, just the icon
        # Update menu state
        self.start_item.set_callback(None if not running else self.start_server)
        self.stop_item.set_callback(None if running else self.stop_server)
        self.restart_item.set_callback(None if running else self.restart_server)
        # Update status text
        if running and data:
            provider = data.get("hermes_model") or data.get("llm_legacy", "unknown")
            vm_count = data.get("voicemails", 0)
            self.status_item.title = f"Running ({provider}) — {vm_count} voicemails"
        else:
            self.status_item.title = "Server stopped"

    def start_server(self, _):
        subprocess.Popen(
            ["bash", str(AGENT_DIR / "run.sh")],
            cwd=str(AGENT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rumps.notification("Hermes Phone", "", "Server starting...")

    def stop_server(self, _):
        subprocess.run(["pkill", "-f", "server.py"], capture_output=True)
        rumps.notification("Hermes Phone", "", "Server stopped")

    def restart_server(self, _):
        self.stop_server(_)
        time.sleep(1)
        self.start_server(_)

    def make_call(self, _):
        window = rumps.Window(
            message="Enter phone number to call:",
            title="📞 Make Call",
            default_text="",
            ok="Call",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        response = window.run()
        if response.clicked and response.text:
            try:
                r = requests.post(CALL_URL, json={"to": response.text}, headers=api_headers(), timeout=10)
                if r.status_code == 200:
                    rumps.notification("Hermes Phone", "", f"Calling {response.text}...")
                else:
                    rumps.notification("Hermes Phone", "", f"Call failed: {r.json().get('error', 'unknown')}")
            except Exception as e:
                rumps.notification("Hermes Phone", "", f"Call failed: {e}")

    def open_settings(self, _):
        """Open native settings panel in pywebview."""
        def run_window():
            try:
                import webview
                window = webview.create_window(
                    "Hermes Phone Settings",
                    html=SETTINGS_HTML,
                    js_api=self.settings_api,
                    width=700,
                    height=800,
                    resizable=True,
                    min_size=(500, 600),
                )
                self.settings_api.window = window
                webview.start()
            except Exception as e:
                print(f"Settings window error: {e}")
                # Fallback: open web dashboard
                import webbrowser
                webbrowser.open("http://localhost:5051")
        threading.Thread(target=run_window, daemon=True).start()

    def open_dashboard(self, _):
        webbrowser.open(f"http://localhost:{5051}")

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    PhoneMenuBar().run()
