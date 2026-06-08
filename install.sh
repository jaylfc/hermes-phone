#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  Hermes Phone — Installer                                       ║
# ║  Sets up everything: deps, config, LaunchAgents, Twilio         ║
# ╚══════════════════════════════════════════════════════════════════╝
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="$HOME/.hermes-phone"
PYTHON="python3"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  📞 Hermes Phone — Installer                                ║${NC}"
echo -e "${CYAN}║  AI-powered phone agent for macOS                           ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Check requirements ─────────────────────────────────────────────
echo -e "${BLUE}Checking requirements...${NC}"

# Check macOS
if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}❌ macOS required (detected $(uname))${NC}"
    exit 1
fi
echo -e "${GREEN}  ✅ macOS $(sw_vers -productVersion)${NC}"

# Check Python
if ! command -v $PYTHON &>/dev/null; then
    echo -e "${RED}❌ Python 3 not found. Install with: brew install python${NC}"
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ]]; then
    echo -e "${RED}❌ Python 3.11+ required (found $PY_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}  ✅ Python $PY_VERSION${NC}"

# ── Create install directory ───────────────────────────────────────
echo ""
echo -e "${BLUE}Setting up $INSTALL_DIR...${NC}"
mkdir -p "$INSTALL_DIR/voicemails/audio"

# Copy files
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/menubar.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/provider_registry.py" "$INSTALL_DIR/"
cp -R "$SCRIPT_DIR/agents" "$INSTALL_DIR/"          # required: server.py does `from agents import ...`
cp "$SCRIPT_DIR/local_voice.py" "$INSTALL_DIR/"    2>/dev/null || true
cp "$SCRIPT_DIR/native_settings.py" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/dashboard.html" "$INSTALL_DIR/"     2>/dev/null || true
cp "$SCRIPT_DIR/settings.html" "$INSTALL_DIR/"      2>/dev/null || true
cp "$SCRIPT_DIR/env.template" "$INSTALL_DIR/"       2>/dev/null || true
cp -R "$SCRIPT_DIR/icons" "$INSTALL_DIR/"           2>/dev/null || true
echo -e "${GREEN}  ✅ Files copied${NC}"

# ── Install dependencies ───────────────────────────────────────────
echo ""
echo -e "${BLUE}Creating virtual environment + installing dependencies...${NC}"
VENV_DIR="$INSTALL_DIR/venv"
if [[ ! -d "$VENV_DIR" ]]; then
    $PYTHON -m venv "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python3"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}  ✅ Dependencies installed into $VENV_DIR${NC}"

# ── Setup wizard ───────────────────────────────────────────────────
echo ""
echo -e "${CYAN}═══ Setup Wizard ═══${NC}"
echo -e "I need a few API keys to get started."
echo ""

ENV_FILE="$INSTALL_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    echo -e "${YELLOW}Existing .env found. Press Enter to keep existing values, or type new ones.${NC}"
    echo ""
fi

# Helper to read existing value
get_env() {
    local key="$1"
    grep "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | sed 's/^"//' | sed 's/"$//' || echo ""
}

# .env is written in full below; existing values are preserved via get_env so a
# re-run keeps anything you don't re-enter (no template pre-copy that would shadow
# real values with placeholders).

# Twilio
echo -e "${BLUE}── Twilio ──${NC}"
echo "Get these from https://console.twilio.com"
echo ""

read -p "Twilio Account SID: " TWILIO_SID
read -p "Twilio Auth Token: " TWILIO_TOKEN
read -p "Twilio Phone Number (e.g. +443301234567): " TWILIO_PHONE

# Deepgram
echo ""
echo -e "${BLUE}── Deepgram (Speech-to-Text) ──${NC}"
echo "Free \$200 credit at https://console.deepgram.com"
echo ""

read -p "Deepgram API Key: " DEEPGRAM_KEY

# LLM
echo ""
echo -e "${BLUE}── LLM Provider ──${NC}"
echo "Choose your AI provider:"
echo "  1) OpenAI (GPT-4o, GPT-4-mini)"
echo "  2) Xiaomi MiMo (free tier)"
echo "  3) OpenRouter (100+ models)"
echo "  4) Local (Ollama)"
echo "  5) Other (OpenAI-compatible)"
echo ""
read -p "Choice [1-5]: " LLM_CHOICE

case $LLM_CHOICE in
    1)
        read -p "OpenAI API Key: " LLM_KEY
        LLM_BASE_URL="https://api.openai.com/v1"
        LLM_PROVIDER="openai"
        read -p "Model [gpt-4o-mini]: " LLM_MODEL
        LLM_MODEL="${LLM_MODEL:-gpt-4o-mini}"
        ;;
    2)
        read -p "Xiaomi API Key: " LLM_KEY
        LLM_BASE_URL="https://token-plan-ams.xiaomimimo.com/v1"
        LLM_PROVIDER="xiaomi"
        LLM_MODEL="mimo-v2.5"
        ;;
    3)
        read -p "OpenRouter API Key: " LLM_KEY
        LLM_BASE_URL="https://openrouter.ai/api/v1"
        LLM_PROVIDER="openrouter"
        read -p "Model [anthropic/claude-sonnet-4]: " LLM_MODEL
        LLM_MODEL="${LLM_MODEL:-anthropic/claude-sonnet-4}"
        ;;
    4)
        LLM_KEY="ollama"
        LLM_BASE_URL="http://localhost:11434/v1"
        LLM_PROVIDER="openai"
        read -p "Model [llama3]: " LLM_MODEL
        LLM_MODEL="${LLM_MODEL:-llama3}"
        ;;
    5)
        read -p "API Key: " LLM_KEY
        read -p "Base URL: " LLM_BASE_URL
        LLM_PROVIDER="openai"
        read -p "Model: " LLM_MODEL
        ;;
esac

# Phone settings
echo ""
echo -e "${BLUE}── Phone Settings ──${NC}"
read -p "Company Name [My Company]: " COMPANY_NAME
COMPANY_NAME="${COMPANY_NAME:-My Company}"
read -p "Voicemail Email (optional): " VOICEMAIL_EMAIL
read -p "Voicemail PIN [1234]: " VOICEMAIL_PIN
VOICEMAIL_PIN="${VOICEMAIL_PIN:-1234}"

# Telegram (optional)
echo ""
echo -e "${BLUE}── Telegram Notifications (optional) ──${NC}"
echo "Get a bot token from @BotFather on Telegram"
read -p "Telegram Bot Token (or press Enter to skip): " TELEGRAM_TOKEN
if [[ -n "$TELEGRAM_TOKEN" ]]; then
    read -p "Telegram Chat ID: " TELEGRAM_CHAT_ID
fi

# ── Write .env ─────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}Writing configuration...${NC}"

# Preserve existing values where the user pressed Enter (no clobber on re-run)
TWILIO_SID="${TWILIO_SID:-$(get_env TWILIO_ACCOUNT_SID)}"
TWILIO_TOKEN="${TWILIO_TOKEN:-$(get_env TWILIO_AUTH_TOKEN)}"
TWILIO_PHONE="${TWILIO_PHONE:-$(get_env TWILIO_PHONE_NUMBER)}"
DEEPGRAM_KEY="${DEEPGRAM_KEY:-$(get_env DEEPGRAM_API_KEY)}"
LLM_KEY="${LLM_KEY:-$(get_env OPENAI_API_KEY)}"
LLM_BASE_URL="${LLM_BASE_URL:-$(get_env OPENAI_BASE_URL)}"
LLM_PROVIDER="${LLM_PROVIDER:-$(get_env LLM_PROVIDER)}"
LLM_MODEL="${LLM_MODEL:-$(get_env LLM_MODEL)}"
VOICEMAIL_EMAIL="${VOICEMAIL_EMAIL:-$(get_env VOICEMAIL_EMAIL)}"
TELEGRAM_TOKEN="${TELEGRAM_TOKEN:-$(get_env TELEGRAM_BOT_TOKEN)}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-$(get_env TELEGRAM_CHAT_ID)}"

# Dashboard token: preserve if present, otherwise generate one
DASHBOARD_TOKEN="$(get_env DASHBOARD_TOKEN)"
[[ -z "$DASHBOARD_TOKEN" ]] && DASHBOARD_TOKEN="$($PYTHON -c 'import secrets; print(secrets.token_urlsafe(32))')"

cat > "$ENV_FILE" << EOF
# Hermes Phone — Configuration
# Generated by installer on $(date)

# Twilio
TWILIO_ACCOUNT_SID=$TWILIO_SID
TWILIO_AUTH_TOKEN=$TWILIO_TOKEN
TWILIO_PHONE_NUMBER=$TWILIO_PHONE

# Deepgram (STT)
DEEPGRAM_API_KEY=$DEEPGRAM_KEY

# LLM
LLM_PROVIDER=$LLM_PROVIDER
LLM_MODEL=$LLM_MODEL
OPENAI_API_KEY=$LLM_KEY
OPENAI_BASE_URL=$LLM_BASE_URL

# Phone Agent
VOICEMAIL_PIN=$VOICEMAIL_PIN
COMPANY_NAME=$COMPANY_NAME
VOICEMAIL_EMAIL=$VOICEMAIL_EMAIL
VOICEMAIL_MAX_LENGTH=120

# Telegram (optional)
TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN:-}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}

# Call Settings
CALL_GOAL=Have a helpful conversation.

# Dashboard security
DASHBOARD_TOKEN=$DASHBOARD_TOKEN
EOF

chmod 600 "$ENV_FILE"
echo -e "${GREEN}  ✅ Configuration saved to $ENV_FILE (chmod 600)${NC}"

# ── Configure Twilio webhook ───────────────────────────────────────
echo ""
echo -e "${BLUE}── Network Setup ──${NC}"
echo "How will Twilio reach your Mac?"
echo "  1) Port forwarding (I have a static IP)"
echo "  2) ngrok (quick setup, dynamic URL)"
echo "  3) Manual (I'll configure later)"
echo ""
read -p "Choice [1-3]: " NET_CHOICE

case $NET_CHOICE in
    1)
        read -p "Your static IP or domain: " STATIC_IP
        WEBHOOK_URL="http://$STATIC_IP:5050/voice/incoming"
        echo ""
        echo -e "${YELLOW}Make sure port 5050 is forwarded to your Mac's local IP.${NC}"
        ;;
    2)
        if ! command -v ngrok &>/dev/null; then
            echo -e "${YELLOW}Installing ngrok...${NC}"
            brew install ngrok 2>/dev/null || {
                echo -e "${RED}Failed to install ngrok. Install manually: brew install ngrok${NC}"
                WEBHOOK_URL=""
            }
        fi
        if command -v ngrok &>/dev/null; then
            read -p "ngrok authtoken: " NGROK_TOKEN
            ngrok config add-authtoken "$NGROK_TOKEN" 2>/dev/null
            echo -e "${GREEN}  ✅ ngrok configured${NC}"
            echo -e "${YELLOW}Start ngrok manually: ngrok http 5050${NC}"
            echo -e "${YELLOW}Then update Twilio webhook to the ngrok URL.${NC}"
            WEBHOOK_URL=""
        fi
        ;;
    3)
        WEBHOOK_URL=""
        ;;
esac

# Set Twilio webhook if we have a URL
if [[ -n "$WEBHOOK_URL" ]]; then
    echo ""
    echo -e "${BLUE}Configuring Twilio webhook...${NC}"

    # Find the phone number SID
    PN_SID=$(curl -s "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_SID/IncomingPhoneNumbers.json" \
        -u "$TWILIO_SID:$TWILIO_TOKEN" | \
        $PYTHON -c "import json,sys; d=json.load(sys.stdin); print(d['incoming_phone_numbers'][0]['sid'])" 2>/dev/null)

    if [[ -n "$PN_SID" ]]; then
        curl -s -X POST "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_SID/IncomingPhoneNumbers/$PN_SID.json" \
            -u "$TWILIO_SID:$TWILIO_TOKEN" \
            --data-urlencode "VoiceUrl=$WEBHOOK_URL" \
            --data-urlencode "VoiceMethod=POST" > /dev/null
        echo -e "${GREEN}  ✅ Twilio webhook → $WEBHOOK_URL${NC}"
    else
        echo -e "${RED}  ❌ Could not find phone number. Set webhook manually.${NC}"
    fi
fi

# ── Install LaunchAgents ───────────────────────────────────────────
echo ""
echo -e "${BLUE}Installing macOS services...${NC}"

# Run the LaunchAgents from the venv interpreter
PYTHON_PATH="$VENV_PY"

# Server LaunchAgent
cat > "$HOME/Library/LaunchAgents/com.hermes-phone.server.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hermes-phone.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$INSTALL_DIR/server.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/server.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/server.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

# Menu bar LaunchAgent
cat > "$HOME/Library/LaunchAgents/com.hermes-phone.menubar.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hermes-phone.menubar</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$INSTALL_DIR/menubar.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
EOF

echo -e "${GREEN}  ✅ LaunchAgents installed${NC}"

# ── Start services ─────────────────────────────────────────────────
echo ""
echo -e "${BLUE}Starting services...${NC}"

# Load server
launchctl unload "$HOME/Library/LaunchAgents/com.hermes-phone.server.plist" 2>/dev/null || true
launchctl load -w "$HOME/Library/LaunchAgents/com.hermes-phone.server.plist"
sleep 3

# Check if server started
if curl -s http://localhost:5050/health > /dev/null 2>&1; then
    echo -e "${GREEN}  ✅ Server running on port 5050${NC}"
else
    echo -e "${YELLOW}  ⚠️ Server starting... (may take a moment)${NC}"
fi

# Load menu bar app
launchctl unload "$HOME/Library/LaunchAgents/com.hermes-phone.menubar.plist" 2>/dev/null || true
launchctl load -w "$HOME/Library/LaunchAgents/com.hermes-phone.menubar.plist"
echo -e "${GREEN}  ✅ Menu bar app started (look for 📞 in your menu bar)${NC}"

# ── Done! ──────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  📞 Hermes Phone — Installed!                               ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Server:${NC}    http://localhost:5050"
echo -e "  ${GREEN}Config:${NC}    $INSTALL_DIR/.env"
echo -e "  ${GREEN}Logs:${NC}      $INSTALL_DIR/server.log"
echo -e "  ${GREEN}Voicemails:${NC} $INSTALL_DIR/voicemails/"
echo ""
echo -e "  ${CYAN}Menu bar:${NC} Look for 📞 in your menu bar"
echo -e "  ${CYAN}PIN:${NC}      $VOICEMAIL_PIN (callers dial this to reach AI)"
echo -e "  ${CYAN}Dashboard token:${NC} Check .env (DASHBOARD_TOKEN)"
echo ""
echo -e "  ${YELLOW}Call ${TWILIO_PHONE} to test!${NC}"
echo ""
echo -e "  ${YELLOW}🔒 Dashboard is protected by token auth.${NC}"
echo -e "  ${YELLOW}   Use the DASHBOARD_TOKEN from .env to login.${NC}"
echo ""
echo -e "  Manage services:"
echo -e "    launchctl stop com.hermes-phone.server"
echo -e "    launchctl start com.hermes-phone.server"
echo ""
