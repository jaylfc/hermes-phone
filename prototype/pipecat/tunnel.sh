#!/usr/bin/env bash
# tunnel.sh — start a TLS tunnel to expose localhost:5050 to Twilio
#
# Usage: ./tunnel.sh [port]
#   port defaults to 5050 (matches PORT in pipeline.py)
#
# Prefers cloudflared (free, no account needed for ephemeral tunnels).
# Falls back to ngrok if cloudflared is not installed.
# Exits with a clear error if neither is available.

PORT="${1:-5050}"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'  # No Colour

echo ""
echo -e "${BOLD}Hermes Phone — TLS tunnel helper${NC}"
echo -e "Target: ${CYAN}http://localhost:${PORT}${NC}"
echo ""

# ── Helper: print instructions after URL is known ─────────────────
print_instructions() {
    local url="$1"
    echo ""
    echo -e "${GREEN}Tunnel URL: ${BOLD}${url}${NC}"
    echo ""
    echo -e "${BOLD}Next steps:${NC}"
    echo ""
    echo "  1. Set PUBLIC_URL in your .env (or export it):"
    echo -e "     ${CYAN}export PUBLIC_URL=${url}${NC}"
    echo ""
    echo "  2. Start the Pipecat server (in another terminal):"
    echo -e "     ${CYAN}python pipeline.py${NC}"
    echo ""
    echo "  3. Point your Twilio phone number's Voice webhook to:"
    echo -e "     ${BOLD}${url}/twiml${NC}"
    echo "     Method: HTTP POST"
    echo "     (Twilio Console → Phone Numbers → Manage → Active Numbers"
    echo "      → click your number → Voice & Fax → 'A CALL COMES IN')"
    echo ""
    echo "  4. Call your Twilio number.  Audio flows:"
    echo "     Twilio → wss://<tunnel>/ws → Pipecat pipeline → Deepgram STT"
    echo "     → OpenAI LLM → Cartesia TTS → wss://<tunnel>/ws → Twilio"
    echo ""
    echo -e "${YELLOW}The tunnel URL changes every time you restart this script.${NC}"
    echo -e "${YELLOW}Update the Twilio webhook and PUBLIC_URL each time.${NC}"
    echo ""
}

# ── cloudflared ────────────────────────────────────────────────────
if command -v cloudflared &>/dev/null; then
    echo -e "Using ${BOLD}cloudflared${NC} (preferred)..."
    echo -e "${YELLOW}Press Ctrl-C to stop the tunnel.${NC}"
    echo ""

    # cloudflared prints the public URL to stderr; capture it
    # Run in the foreground so Ctrl-C stops it cleanly.
    # We tail the log line containing "trycloudflare.com" to extract the URL
    # and print instructions, then continue running in the foreground.

    # Use a temp file to capture cloudflared output
    TMPLOG=$(mktemp)
    trap "rm -f '$TMPLOG'" EXIT

    cloudflared tunnel --url "http://localhost:${PORT}" 2>&1 | tee "$TMPLOG" | while IFS= read -r line; do
        echo "$line"
        # cloudflared outputs the URL on a line like:
        #   | https://abc123.trycloudflare.com
        if echo "$line" | grep -qE 'https://[a-z0-9-]+\.trycloudflare\.com'; then
            TUNNEL_URL=$(echo "$line" | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com')
            print_instructions "$TUNNEL_URL"
        fi
    done
    exit 0
fi

# ── ngrok ──────────────────────────────────────────────────────────
if command -v ngrok &>/dev/null; then
    echo -e "Using ${BOLD}ngrok${NC} (fallback)..."
    echo ""
    echo -e "${YELLOW}ngrok requires a free account and authtoken.${NC}"
    echo -e "${YELLOW}Get one at https://dashboard.ngrok.com/get-started/your-authtoken${NC}"
    echo -e "${YELLOW}Then run: ngrok config add-authtoken <token>${NC}"
    echo ""
    echo -e "${YELLOW}Press Ctrl-C to stop the tunnel.${NC}"
    echo ""

    # ngrok writes its URL to its API; poll until available
    ngrok http "$PORT" &
    NGROK_PID=$!
    trap "kill $NGROK_PID 2>/dev/null" EXIT

    echo "Waiting for ngrok to start..."
    for i in $(seq 1 20); do
        sleep 1
        TUNNEL_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
            | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for t in d.get('tunnels', []):
        if t.get('proto') == 'https':
            print(t['public_url'])
            break
except:
    pass
" 2>/dev/null)
        if [ -n "$TUNNEL_URL" ]; then
            print_instructions "$TUNNEL_URL"
            wait $NGROK_PID
            exit 0
        fi
    done

    echo -e "${RED}Could not get ngrok URL after 20 seconds.${NC}"
    echo "Check http://localhost:4040 in your browser."
    wait $NGROK_PID
    exit 1
fi

# ── Neither available ──────────────────────────────────────────────
echo -e "${RED}ERROR: Neither 'cloudflared' nor 'ngrok' is installed.${NC}"
echo ""
echo "Install one of the following:"
echo ""
echo -e "  ${BOLD}cloudflared${NC} (recommended — free, no signup for ephemeral tunnels):"
echo "    brew install cloudflared"
echo "    # or download from https://github.com/cloudflare/cloudflared/releases"
echo ""
echo -e "  ${BOLD}ngrok${NC} (free tier, requires account):"
echo "    brew install ngrok/ngrok/ngrok"
echo "    # or download from https://ngrok.com/download"
echo ""
exit 1
