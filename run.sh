#!/usr/bin/env bash
# Hermes Phone Agent — Launcher
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Run ./setup.sh first"
    exit 1
fi

# Load env
set -a
source "$ENV_FILE"
set +a

echo "🤖 Starting Hermes Phone Agent..."
echo "   Server: http://localhost:5050"
echo "   WebSocket: ws://localhost:5050/ws/call"
echo ""
echo "📡 Endpoints:"
echo "   POST /call        — Make outbound call"
echo "   POST /voice/incoming — Twilio incoming webhook"
echo "   GET  /health      — Health check"
echo ""

cd "$DIR"
python3 server.py
