"""
Hermes Phone — macOS Menu Bar App

Features:
- Server status (running/stopped)
- Start/Stop/Restart server
- Voicemail manager (list, play, delete, callback)
- Make outbound calls
- View logs
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

# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

AGENT_DIR = Path(__file__).parent
HEALTH_URL = "http://localhost:5050/health"
VOICEMAILS_URL = "http://localhost:5050/voicemails"
CHECK_INTERVAL = 10
SERVICE_LABEL = "com.hermes-phone.server"

TITLE = "📞"


# ═══════════════════════════════════════════════════════════════════
# Menu Bar App
# ═══════════════════════════════════════════════════════════════════

class HermesPhoneApp(rumps.App):
    def __init__(self):
        super().__init__(TITLE, quit_button=None)
        self.status = "stopped"
        self.voicemails = []

        # Status item (non-clickable)
        self.status_item = rumps.MenuItem("Status: Checking...")

        # Build menu
        self.start_item = rumps.MenuItem("Start Server", callback=self.start_server)
        self.stop_item = rumps.MenuItem("Stop Server", callback=self.stop_server)
        self.restart_item = rumps.MenuItem("Restart Server", callback=self.restart_server)
        self.menu = [
            self.status_item,
            None,
            self.start_item,
            self.stop_item,
            self.restart_item,
            None,
            rumps.MenuItem("Voicemails", callback=self.show_voicemails),
            rumps.MenuItem("Make Call...", callback=self.make_call),
            None,
            rumps.MenuItem("Open Web Dashboard", callback=self.open_dashboard),
            rumps.MenuItem("View Logs", callback=self.view_logs),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        # Start monitoring
        threading.Thread(target=self._health_loop, daemon=True).start()
        threading.Thread(target=self._voicemail_loop, daemon=True).start()

    # ── Service control ────────────────────────────────────────────

    def _launchctl(self, *args):
        try:
            result = subprocess.run(
                ["launchctl"] + list(args),
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0, result.stdout.strip()
        except:
            return False, ""

    def start_server(self, _):
        plist = Path.home() / "Library/LaunchAgents" / f"{SERVICE_LABEL}.plist"
        if not plist.exists():
            rumps.alert("Server not installed", "Run install.sh first to set up the server.")
            return
        ok, _ = self._launchctl("load", "-w", str(plist))
        if ok:
            rumps.notification(TITLE, "", "Server started")
        else:
            rumps.notification(TITLE, "", "Failed to start server")

    def stop_server(self, _):
        plist = Path.home() / "Library/LaunchAgents" / f"{SERVICE_LABEL}.plist"
        self._launchctl("unload", "-w", str(plist))
        self._update_status("stopped")
        rumps.notification(TITLE, "", "Server stopped")

    def restart_server(self, _):
        self.stop_server(None)
        time.sleep(2)
        self.start_server(None)

    # ── Health monitoring ───────────────────────────────────────────

    def _health_loop(self):
        while True:
            try:
                r = requests.get(HEALTH_URL, timeout=3)
                if r.status_code == 200:
                    self._update_status("running", r.json())
                else:
                    self._update_status("error")
            except:
                self._update_status("stopped")
            time.sleep(CHECK_INTERVAL)

    def _update_status(self, status, health_data=None):
        self.status = status
        if status == "running":
            icon = "🟢"
            p = health_data.get("provider", "?") if health_data else "?"
            m = health_data.get("model", "?") if health_data else "?"
            vm_count = health_data.get("voicemails", 0) if health_data else 0
            self.status_item.title = f"Running ({p}/{m}) — {vm_count} voicemails"
            self.start_item.set_callback(None)
            self.stop_item.set_callback(self.stop_server)
            self.restart_item.set_callback(self.restart_server)
        elif status == "starting":
            icon = "🟡"
            self.status_item.title = "Starting..."
            self.start_item.set_callback(None)
            self.stop_item.set_callback(None)
            self.restart_item.set_callback(None)
        else:
            icon = "🔴"
            self.status_item.title = "Stopped"
            self.start_item.set_callback(self.start_server)
            self.stop_item.set_callback(None)
            self.restart_item.set_callback(None)
        self.title = f"{TITLE} {icon}"

    # ── Voicemail management ───────────────────────────────────────

    def _voicemail_loop(self):
        while True:
            if self.status == "running":
                self._fetch_voicemails()
            time.sleep(30)

    def _fetch_voicemails(self):
        try:
            r = requests.get(VOICEMAILS_URL, timeout=5)
            if r.status_code == 200:
                self.voicemails = r.json()
        except:
            pass

    def show_voicemails(self, _):
        """Show voicemail manager window."""
        self._fetch_voicemails()

        if not self.voicemails:
            rumps.alert(
                title="Voicemails",
                message="No voicemails yet.",
                ok="Close",
            )
            return

        # Build voicemail list
        vm_list = []
        for vm in reversed(self.voicemails):  # newest first
            caller = vm.get("from", "Unknown").replace("+", "")
            duration = vm.get("duration", 0)
            transcript = vm.get("transcript", "")
            time_str = vm.get("time", "")

            # Format time
            try:
                dt = datetime.fromisoformat(time_str)
                time_display = dt.strftime("%b %d, %H:%M")
            except:
                time_display = time_str

            # Truncate transcript
            if transcript:
                transcript_preview = transcript[:100] + ("..." if len(transcript) > 100 else "")
            else:
                transcript_preview = "(no transcript)"

            vm_list.append(
                f"📞 {caller} — {duration}s — {time_display}\n"
                f"   {transcript_preview}\n"
            )

        message = f"You have {len(self.voicemails)} voicemail(s):\n\n" + "\n".join(vm_list)

        # Show with action buttons
        response = rumps.alert(
            title="Voicemail Manager",
            message=message,
            ok="Close",
            other="Play Latest",
        )

        # If "Play Latest" clicked
        if response == 1 and self.voicemails:
            self._play_voicemail(self.voicemails[-1])

    def _play_voicemail(self, vm):
        """Play a voicemail audio file."""
        audio_path = vm.get("audio_path", "")
        if audio_path and Path(audio_path).exists():
            subprocess.Popen(["afplay", audio_path])
        else:
            # Try to download from Twilio
            url = vm.get("url", "")
            if url:
                rumps.notification(TITLE, "", "Downloading voicemail...")
                # Would need Twilio auth here — for now just notify
                rumps.notification(TITLE, "", "Audio file not cached locally")

    def make_call(self, _):
        """Show dialog to make an outbound call."""
        # Simple input dialog
        window = rumps.Window(
            message="Enter phone number to call:",
            title="Make Call",
            default_text="+44",
            ok="Call",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        response = window.run()
        if response.clicked and response.text:
            to_number = response.text.strip()
            if to_number:
                try:
                    r = requests.post(
                        "http://localhost:5050/call",
                        json={"to": to_number},
                        timeout=10,
                    )
                    if r.status_code == 200:
                        rumps.notification(TITLE, "", f"Calling {to_number}...")
                    else:
                        rumps.notification(TITLE, "", f"Call failed: {r.json().get('error', 'unknown')}")
                except Exception as e:
                    rumps.notification(TITLE, "", f"Call failed: {e}")

    # ── Other actions ──────────────────────────────────────────────

    def open_dashboard(self, _):
        webbrowser.open("http://localhost:5050/health")

    def view_logs(self, _):
        log = AGENT_DIR / "server.log"
        if log.exists():
            subprocess.Popen(["open", "-a", "Console", str(log)])
        else:
            rumps.notification(TITLE, "", "No logs yet")

    def quit_app(self, _):
        rumps.quit_application()


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = HermesPhoneApp()
    app.run()
