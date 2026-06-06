#!/usr/bin/env bash
# Hermes Phone Agent — Setup script
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$DIR/.env"

echo "🤖 Hermes Phone Agent Setup"
echo "=========================="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 not found"
    exit 1
fi

echo "📦 Installing dependencies..."
pip3 install -q -r "$DIR/requirements.txt" 2>/dev/null || \
pip install -q -r "$DIR/requirements.txt" 2>/dev/null || \
python3 -m pip install -q -r "$DIR/requirements.txt"

echo "✅ Dependencies installed"
echo ""

# Create .env if it doesn't exist
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'EOF'
# Twilio (already configured)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# Deepgram (free tier: $200 credit at https://console.deepgram.com)
DEEPGRAM_API_KEY=

# OpenRouter (for MiMo 2.5 — already configured in Hermes)
OPENROUTER_API_KEY=

# LLM Model (default: MiMo 2.5 via OpenRouter)
LLM_MODEL=xiaomi/mimo-v2.5

# TTS: "deepgram" (needs Deepgram key) or "edge" (free, no key)
TTS_PROVIDER=deepgram

# Call defaults
CALL_GOAL=Have a friendly conversation.
EOF
    echo "📝 Created $ENV_FILE — fill in your API keys"
else
    echo "📝 $ENV_FILE already exists"
fi

echo ""
echo "🔑 Required API keys:"
echo "   1. Twilio — already have ✅"
echo "   2. Deepgram — free $200 credit at https://console.deepgram.com"
echo "   3. OpenRouter — check ~/.hermes/config for existing key"
echo ""
echo "🚀 To run: ./run.sh"
echo "   Or: source .env && python3 server.py"
