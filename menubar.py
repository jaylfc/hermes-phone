"""
Hermes Phone — macOS Menu Bar App

Single phone icon that changes color:
  🟢 = server running
  🔴 = server stopped

Native settings panel via AppKit (not a web view).
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


def _load_dashboard_token():
    """Read DASHBOARD_TOKEN from .env file."""
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
        # Update menu state (None = disabled, callback = enabled)
        self.start_item.set_callback(None if running else self.start_server)
        self.stop_item.set_callback(None if not running else self.stop_server)
        self.restart_item.set_callback(None if not running else self.restart_server)
        # Update status text
        if running and data:
            provider = data.get("hermes_model") or data.get("llm_legacy", "unknown")
            vm_count = data.get("voicemails", 0)
            self.status_item.title = f"Running ({provider}) — {vm_count} voicemails"
        else:
            self.status_item.title = "Server stopped"

    def start_server(self, _):
        # Check if already running
        try:
            r = requests.get(HEALTH_URL, timeout=2)
            if r.status_code == 200:
                rumps.notification("Hermes Phone", "", "Server already running")
                return
        except:
            pass

        # Start server
        log_path = AGENT_DIR / "server.log"
        with open(log_path, "a") as log:
            subprocess.Popen(
                ["bash", str(AGENT_DIR / "run.sh")],
                cwd=str(AGENT_DIR),
                stdout=log,
                stderr=log,
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
        """Open native macOS settings window."""
        try:
            from native_settings import open_settings as _open_native
            _open_native(
                api_url="http://localhost:5051",
                token=DASHBOARD_TOKEN,
            )
        except Exception as e:
            print(f"Native settings error: {e}")
            # Fallback to browser with auto-auth
            webbrowser.open(f"http://localhost:5051/?token={DASHBOARD_TOKEN}")

    def open_dashboard(self, _):
        webbrowser.open(f"http://localhost:5051/?token={DASHBOARD_TOKEN}")

    def quit_app(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    PhoneMenuBar().run()
