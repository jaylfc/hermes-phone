#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║  Hermes Phone — Linux/WSL2 Installer                            ║
# ║  Sets up everything: deps, config, systemd service              ║
# ╚══════════════════════════════════════════════════════════════════╝
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="$HOME/.hermes/phone-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="com.janlabs.phone-agent"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
VENV_DIR="$INSTALL_DIR/venv"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  📞 Hermes Phone — Linux/WSL2 Installer                     ║${NC}"
echo -e "${CYAN}║  AI-powered phone agent                                      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Check platform ─────────────────────────────────────────────────
echo -e "${BLUE}Checking platform...${NC}"

if [[ "$(uname)" == "Darwin" ]]; then
    echo -e "${RED}❌ This is the Linux installer. For macOS, use ./install.sh${NC}"
    exit 1
fi

ARCH=$(uname -m)
echo -e "${GREEN}  ✅ Linux ($(uname -s) ${ARCH})${NC}"

# Check if running in WSL2
if grep -qi "microsoft" /proc/version 2>/dev/null; then
    echo -e "${GREEN}  ✅ WSL2 detected${NC}"
fi

# ── Find or install Python ─────────────────────────────────────────
echo ""
echo -e "${BLUE}Checking Python...${NC}"

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" &>/dev/null; then
        PY_VERSION=$("$candidate" --version 2>&1 | awk '{print $2}')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [[ "$PY_MAJOR" -gt $PYTHON_MIN_MAJOR ]] || \
           [[ "$PY_MAJOR" -eq $PYTHON_MIN_MAJOR && "$PY_MINOR" -ge $PYTHON_MIN_MINOR ]]; then
            PYTHON="$candidate"
            echo -e "${GREEN}  ✅ Python $PY_VERSION ($candidate)${NC}"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo -e "${YELLOW}  ⚠️  Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ not found. Attempting to install...${NC}"

    # Detect package manager
    if command -v apt-get &>/dev/null; then
        echo -e "${BLUE}  Installing via apt...${NC}"
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3 python3-pip python3-venv python3-dev
        PYTHON="python3"
    elif command -v dnf &>/dev/null; then
        echo -e "${BLUE}  Installing via dnf...${NC}"
        sudo dnf install -y python3 python3-pip python3-devel
        PYTHON="python3"
    elif command -v pacman &>/dev/null; then
        echo -e "${BLUE}  Installing via pacman...${NC}"
        sudo pacman -S --noconfirm python python-pip
        PYTHON="python3"
    elif command -v apk &>/dev/null; then
        echo -e "${BLUE}  Installing via apk...${NC}"
        sudo apk add python3 py3-pip py3-virtualenv
        PYTHON="python3"
    else
        echo -e "${RED}  ❌ Could not detect package manager.${NC}"
        echo -e "${RED}  Please install Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ manually and re-run.${NC}"
        exit 1
    fi

    # Verify installation
    PY_VERSION=$("$PYTHON" --version 2>&1 | awk '{print $2}')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [[ "$PY_MAJOR" -lt $PYTHON_MIN_MAJOR ]] || \
       [[ "$PY_MAJOR" -eq $PYTHON_MIN_MAJOR && "$PY_MINOR" -lt $PYTHON_MIN_MINOR ]]; then
        echo -e "${RED}  ❌ Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ required (installed: $PY_VERSION)${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✅ Python $PY_VERSION installed${NC}"
fi

# Check for venv module
if ! "$PYTHON" -c "import venv" &>/dev/null; then
    echo -e "${YELLOW}  ⚠️  python3-venv not found. Installing...${NC}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y -qq python3-venv
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3-virtualenv
    fi
fi

# Check for ffmpeg (needed by pydub for audio processing)
if ! command -v ffmpeg &>/dev/null; then
    echo -e "${YELLOW}  ⚠️  ffmpeg not found (needed for audio). Installing...${NC}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y -qq ffmpeg 2>/dev/null || true
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y ffmpeg 2>/dev/null || true
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm ffmpeg 2>/dev/null || true
    elif command -v apk &>/dev/null; then
        sudo apk add ffmpeg 2>/dev/null || true
    fi
    if command -v ffmpeg &>/dev/null; then
        echo -e "${GREEN}  ✅ ffmpeg installed${NC}"
    else
        echo -e "${YELLOW}  ⚠️  ffmpeg not available — some audio features may not work${NC}"
    fi
else
    echo -e "${GREEN}  ✅ ffmpeg available${NC}"
fi

# ── Create install directory ───────────────────────────────────────
echo ""
echo -e "${BLUE}Setting up $INSTALL_DIR...${NC}"
mkdir -p "$INSTALL_DIR/voicemails/audio"
mkdir -p "$INSTALL_DIR/launchagents"
mkdir -p "$INSTALL_DIR/icons"

# Copy files
cp "$SCRIPT_DIR/server.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/provider_registry.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
cp -R "$SCRIPT_DIR/agents" "$INSTALL_DIR/"          # required: server.py does `from agents import ...`
cp "$SCRIPT_DIR/local_voice.py" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/dashboard.html" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/settings.html" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/native_settings.py" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/env.template" "$INSTALL_DIR/" 2>/dev/null || true
cp -r "$SCRIPT_DIR/icons/" "$INSTALL_DIR/icons/" 2>/dev/null || true
echo -e "${GREEN}  ✅ Files copied${NC}"

# ── Create virtual environment ─────────────────────────────────────
echo ""
echo -e "${BLUE}Creating virtual environment...${NC}"

if [[ -d "$VENV_DIR" ]]; then
    echo -e "${YELLOW}  ⚠️  Existing venv found at $VENV_DIR${NC}"
    read -p "  Recreate? (y/N): " RECREATE
    if [[ "$RECREATE" =~ ^[Yy]$ ]]; then
        rm -rf "$VENV_DIR"
        "$PYTHON" -m venv "$VENV_DIR"
        echo -e "${GREEN}  ✅ Virtual environment recreated${NC}"
    else
        echo -e "${CYAN}  ℹ️  Using existing venv${NC}"
    fi
else
    "$PYTHON" -m venv "$VENV_DIR"
    echo -e "${GREEN}  ✅ Virtual environment created${NC}"
fi

# ── Install dependencies ───────────────────────────────────────────
echo ""
echo -e "${BLUE}Installing Python dependencies...${NC}"
"$VENV_DIR/bin/pip" install --upgrade pip -q 2>/dev/null
"$VENV_DIR/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt" 2>&1 | tail -5
echo -e "${GREEN}  ✅ Dependencies installed${NC}"

# ── Setup wizard ───────────────────────────────────────────────────
echo ""
echo -e "${CYAN}═══ Setup Wizard ═══${NC}"
echo -e "I need a few API keys to get started."
echo -e "You can also configure everything from the web dashboard later."
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

# Create .env from template if it doesn't exist
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# Hermes Phone — Configuration

# Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# Deepgram (STT) — free $200 credit at https://console.deepgram.com
DEEPGRAM_API_KEY=

# LLM
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1

# Phone Agent
VOICEMAIL_PIN=1234
COMPANY_NAME=My Company
VOICEMAIL_EMAIL=
VOICEMAIL_MAX_LENGTH=120

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Call Settings
CALL_GOAL=Have a helpful conversation.

# Dashboard security (auto-generated)
DASHBOARD_TOKEN=
ENVEOF
fi

# ── Twilio ─────────────────────────────────────────────────────────
echo -e "${BLUE}── Twilio ──${NC}"
echo "Get these from https://console.twilio.com"
echo ""

EXISTING_SID=$(get_env TWILIO_ACCOUNT_SID)
EXISTING_TOKEN=$(get_env TWILIO_AUTH_TOKEN)
EXISTING_PHONE=$(get_env TWILIO_PHONE_NUMBER)

if [[ -n "$EXISTING_SID" ]]; then
    echo -e "${CYAN}  Current SID: ${EXISTING_SID:0:8}...${NC}"
fi
read -p "Twilio Account SID (Enter to keep): " TWILIO_SID
TWILIO_SID="${TWILIO_SID:-$EXISTING_SID}"

if [[ -n "$EXISTING_TOKEN" ]]; then
    echo -e "${CYAN}  Current token: ${EXISTING_TOKEN:0:4}...${NC}"
fi
read -p "Twilio Auth Token (Enter to keep): " TWILIO_TOKEN
TWILIO_TOKEN="${TWILIO_TOKEN:-$EXISTING_TOKEN}"

if [[ -n "$EXISTING_PHONE" ]]; then
    echo -e "${CYAN}  Current phone: $EXISTING_PHONE${NC}"
fi
read -p "Twilio Phone Number (Enter to keep): " TWILIO_PHONE
TWILIO_PHONE="${TWILIO_PHONE:-$EXISTING_PHONE}"

# ── Deepgram ───────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}── Deepgram (Speech-to-Text) ──${NC}"
echo "Free \$200 credit at https://console.deepgram.com"
echo ""

EXISTING_DG=$(get_env DEEPGRAM_API_KEY)
if [[ -n "$EXISTING_DG" ]]; then
    echo -e "${CYAN}  Current key: ${EXISTING_DG:0:8}...${NC}"
fi
read -p "Deepgram API Key (Enter to keep): " DEEPGRAM_KEY
DEEPGRAM_KEY="${DEEPGRAM_KEY:-$EXISTING_DG}"

# ── LLM ────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}── LLM Provider ──${NC}"
echo "Choose your AI provider:"
echo "  1) OpenAI (GPT-4o, GPT-4-mini)"
echo "  2) Xiaomi MiMo (free tier)"
echo "  3) OpenRouter (100+ models)"
echo "  4) Local (Ollama)"
echo "  5) Hermes Gateway (if running)"
echo "  6) Other (OpenAI-compatible)"
echo "  7) Skip (configure later)"
echo ""
read -p "Choice [1-7]: " LLM_CHOICE

case ${LLM_CHOICE:-7} in
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
        read -p "Hermes Gateway URL [http://127.0.0.1:8642]: " HERMES_URL
        HERMES_URL="${HERMES_URL:-http://127.0.0.1:8642}"
        read -p "Hermes Gateway Token: " HERMES_TOKEN
        LLM_PROVIDER="hermes"
        LLM_KEY=""
        LLM_BASE_URL=""
        LLM_MODEL=""
        ;;
    6)
        read -p "API Key: " LLM_KEY
        read -p "Base URL: " LLM_BASE_URL
        LLM_PROVIDER="openai"
        read -p "Model: " LLM_MODEL
        ;;
    7)
        LLM_PROVIDER="openai"
        LLM_KEY=""
        LLM_BASE_URL="https://api.openai.com/v1"
        LLM_MODEL="gpt-4o-mini"
        echo -e "${CYAN}  ℹ️  Configure LLM from dashboard later${NC}"
        ;;
esac

# ── Phone settings ─────────────────────────────────────────────────
echo ""
echo -e "${BLUE}── Phone Settings ──${NC}"
EXISTING_COMPANY=$(get_env COMPANY_NAME)
EXISTING_PIN=$(get_env VOICEMAIL_PIN)
EXISTING_EMAIL=$(get_env VOICEMAIL_EMAIL)

read -p "Company Name [${EXISTING_COMPANY:-My Company}]: " COMPANY_NAME
COMPANY_NAME="${COMPANY_NAME:-${EXISTING_COMPANY:-My Company}}"

read -p "Voicemail Email (optional): " VOICEMAIL_EMAIL
VOICEMAIL_EMAIL="${VOICEMAIL_EMAIL:-$EXISTING_EMAIL}"

read -p "Voicemail PIN [${EXISTING_PIN:-1234}]: " VOICEMAIL_PIN
VOICEMAIL_PIN="${VOICEMAIL_PIN:-${EXISTING_PIN:-1234}}"

# ── Telegram (optional) ───────────────────────────────────────────
echo ""
echo -e "${BLUE}── Telegram Notifications (optional) ──${NC}"
echo "Get a bot token from @BotFather on Telegram"
EXISTING_TG_TOKEN=$(get_env TELEGRAM_BOT_TOKEN)
EXISTING_TG_CHAT=$(get_env TELEGRAM_CHAT_ID)

if [[ -n "$EXISTING_TG_TOKEN" ]]; then
    echo -e "${CYAN}  Current bot: ${EXISTING_TG_TOKEN:0:10}...${NC}"
fi
read -p "Telegram Bot Token (Enter to keep/skip): " TELEGRAM_TOKEN
TELEGRAM_TOKEN="${TELEGRAM_TOKEN:-$EXISTING_TG_TOKEN}"

if [[ -n "$TELEGRAM_TOKEN" ]]; then
    read -p "Telegram Chat ID: " TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-$EXISTING_TG_CHAT}"
fi

# ── Generate DASHBOARD_TOKEN ───────────────────────────────────────
EXISTING_DASH_TOKEN=$(get_env DASHBOARD_TOKEN)
if [[ -z "$EXISTING_DASH_TOKEN" ]]; then
    DASHBOARD_TOKEN=$("$VENV_DIR/bin/python3" -c "import secrets; print(secrets.token_urlsafe(32))")
else
    DASHBOARD_TOKEN="$EXISTING_DASH_TOKEN"
fi

# ── Write .env ─────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}Writing configuration...${NC}"

cat > "$ENV_FILE" << EOF
# Hermes Phone — Configuration
# Generated by Linux installer on $(date)

# Twilio
TWILIO_ACCOUNT_SID=$TWILIO_SID
TWILIO_AUTH_TOKEN=$TWILIO_TOKEN
TWILIO_PHONE_NUMBER=$TWILIO_PHONE

# Deepgram (STT)
DEEPGRAM_API_KEY=$DEEPGRAM_KEY

# LLM
LLM_PROVIDER=$LLM_PROVIDER
LLM_MODEL=$LLM_MODEL
OPENAI_API_KEY=${LLM_KEY:-}
OPENAI_BASE_URL=${LLM_BASE_URL:-}

# Hermes Gateway (if configured)
HERMES_GATEWAY_URL=${HERMES_URL:-}
HERMES_GATEWAY_TOKEN=${HERMES_TOKEN:-}

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

# Dashboard security (auto-generated)
DASHBOARD_TOKEN=$DASHBOARD_TOKEN
EOF

echo -e "${GREEN}  ✅ Configuration saved to $ENV_FILE${NC}"

# ── Configure Twilio webhook ───────────────────────────────────────
echo ""
echo -e "${BLUE}── Network Setup ──${NC}"
echo "How will Twilio reach your server?"
echo "  1) ngrok (quick setup, dynamic URL)"
echo "  2) Public IP / domain (direct)"
echo "  3) Manual (configure later from dashboard)"
echo ""
read -p "Choice [1-3]: " NET_CHOICE

case ${NET_CHOICE:-3} in
    1)
        if ! command -v ngrok &>/dev/null; then
            echo -e "${YELLOW}  ⚠️  ngrok not found.${NC}"
            echo -e "${CYAN}  Install: https://ngrok.com/download${NC}"
            echo -e "${CYAN}  Then run: ngrok http 5050${NC}"
            WEBHOOK_URL=""
        else
            echo -e "${CYAN}  Start ngrok manually: ngrok http 5050${NC}"
            echo -e "${CYAN}  Then update Twilio webhook to the ngrok URL.${NC}"
            WEBHOOK_URL=""
        fi
        ;;
    2)
        read -p "Your public IP or domain: " PUBLIC_IP
        WEBHOOK_URL="https://$PUBLIC_IP:5050/voice/incoming"
        echo -e "${YELLOW}  Make sure port 5050 is accessible from the internet.${NC}"
        ;;
    3)
        WEBHOOK_URL=""
        echo -e "${CYAN}  ℹ️  Configure webhook from dashboard later.${NC}"
        ;;
esac

# Set Twilio webhook if we have a URL
if [[ -n "$WEBHOOK_URL" && -n "$TWILIO_SID" && -n "$TWILIO_TOKEN" ]]; then
    echo ""
    echo -e "${BLUE}Configuring Twilio webhook...${NC}"

    PN_SID=$(curl -s "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_SID/IncomingPhoneNumbers.json" \
        -u "$TWILIO_SID:$TWILIO_TOKEN" | \
        "$VENV_DIR/bin/python3" -c "import json,sys; d=json.load(sys.stdin); print(d['incoming_phone_numbers'][0]['sid'])" 2>/dev/null)

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

# ── Install systemd user service ───────────────────────────────────
echo ""
echo -e "${BLUE}Installing systemd service...${NC}"

mkdir -p "$HOME/.config/systemd/user"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Hermes Phone Agent — AI-powered VoIP server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python3 $INSTALL_DIR/server.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=-$INSTALL_DIR/.env

[Install]
WantedBy=default.target
EOF

echo -e "${GREEN}  ✅ Service file created: $SERVICE_FILE${NC}"

# Enable lingering so service runs without login
if command -v loginctl &>/dev/null; then
    sudo loginctl enable-linger "$(whoami)" 2>/dev/null || true
    echo -e "${GREEN}  ✅ Lingering enabled (service runs at boot)${NC}"
fi

# Reload systemd
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user enable "$SERVICE_NAME.service" 2>/dev/null || true
echo -e "${GREEN}  ✅ Service enabled${NC}"

# ── Start service ──────────────────────────────────────────────────
echo ""
echo -e "${BLUE}Starting service...${NC}"

systemctl --user restart "$SERVICE_NAME.service" 2>/dev/null || true
sleep 3

# Check if server started
if curl -s http://localhost:5050/health > /dev/null 2>&1; then
    echo -e "${GREEN}  ✅ Server running on port 5050${NC}"
else
    echo -e "${YELLOW}  ⚠️  Server starting... (may take a moment)${NC}"
    echo -e "${CYAN}  Check status: systemctl --user status $SERVICE_NAME${NC}"
fi

# ── Done! ──────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  📞 Hermes Phone — Installed!                               ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Webhook:${NC}   http://localhost:5050"
echo -e "  ${GREEN}Dashboard:${NC} http://localhost:5051"
echo -e "  ${GREEN}Config:${NC}    $INSTALL_DIR/.env"
echo -e "  ${GREEN}Logs:${NC}      journalctl --user -u $SERVICE_NAME -f"
echo -e "  ${GREEN}Voicemails:${NC} $INSTALL_DIR/voicemails/"
echo ""
echo -e "  ${CYAN}Dashboard Token:${NC} $DASHBOARD_TOKEN"
echo -e "  ${CYAN}Voicemail PIN:${NC}   $VOICEMAIL_PIN"
echo ""
if [[ -n "$TWILIO_PHONE" ]]; then
    echo -e "  ${YELLOW}Call $TWILIO_PHONE to test!${NC}"
    echo ""
fi
echo -e "  ${YELLOW}🔒 Dashboard is protected by token auth.${NC}"
echo -e "  ${YELLOW}   Use the DASHBOARD_TOKEN above to login.${NC}"
echo ""
echo -e "  Manage service:"
echo -e "    systemctl --user status $SERVICE_NAME"
echo -e "    systemctl --user restart $SERVICE_NAME"
echo -e "    systemctl --user stop $SERVICE_NAME"
echo ""
echo -e "  View logs:"
echo -e "    journalctl --user -u $SERVICE_NAME -f"
echo ""
echo -e "  ${CYAN}ℹ️  No menu bar app on Linux — use the web dashboard for all management.${NC}"
echo ""
