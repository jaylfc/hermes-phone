"""
Hermes Phone — macOS Menu Bar App

Full control center: server, voicemails, calls, settings.
Everything accessible from the menu bar.
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
SETTINGS_URL = "http://localhost:5050/api/settings"
CHECK_INTERVAL = 10
SERVICE_LABEL = "com.hermes-phone.server"

TITLE = "📞"

# Available TTS voices
VOICES = [
    ("Polly.Amy", "Amy (UK, F)"),
    ("Polly.Brian", "Brian (UK, M)"),
    ("Polly.Emma", "Emma (UK, F)"),
    ("Polly.Joanna", "Joanna (US, F)"),
    ("Polly.Matthew", "Matthew (US, M)"),
    ("Polly.Ivy", "Ivy (US, F)"),
    ("Polly.Justin", "Justin (US, M)"),
    ("Polly.Kendra", "Kendra (US, F)"),
    ("Polly.Kimberly", "Kimberly (US, F)"),
    ("Polly.Salli", "Salli (US, F)"),
    ("Polly.Nicole", "Nicole (AU, F)"),
    ("Polly.Russell", "Russell (AU, M)"),
]


# ═══════════════════════════════════════════════════════════════════
# Menu Bar App
# ═══════════════════════════════════════════════════════════════════

class HermesPhoneApp(rumps.App):
    def __init__(self):
        super().__init__(TITLE, quit_button=None)
        self.status = "stopped"
        self.voicemails = []
        self.settings = {}

        # Status item
        self.status_item = rumps.MenuItem("Status: Checking...")

        # Server control
        self.start_item = rumps.MenuItem("Start Server", callback=self.start_server)
        self.stop_item = rumps.MenuItem("Stop Server", callback=self.stop_server)
        self.restart_item = rumps.MenuItem("Restart Server", callback=self.restart_server)

        # Build menu
        self.menu = [
            self.status_item,
            None,
            self.start_item,
            self.stop_item,
            self.restart_item,
            None,
            rumps.MenuItem("📞 Make Call...", callback=self.make_call),
            self._build_voicemail_menu(),
            self._build_settings_menu(),
            None,
            rumps.MenuItem("🌐 Open Dashboard", callback=self.open_dashboard),
            rumps.MenuItem("📋 View Logs", callback=self.view_logs),
            None,
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

        # Start monitoring
        threading.Thread(target=self._health_loop, daemon=True).start()
        threading.Thread(target=self._voicemail_loop, daemon=True).start()
        threading.Thread(target=self._settings_loop, daemon=True).start()

    # ── Menu builders ──────────────────────────────────────────────

    def _build_voicemail_menu(self):
        """Build voicemails submenu."""
        self.vm_menu = rumps.MenuItem("🎙️ Voicemails")
        self.vm_menu.add(rumps.MenuItem("No voicemails yet"))
        return self.vm_menu

    def _build_settings_menu(self):
        """Build settings submenu."""
        settings_menu = rumps.MenuItem("⚙️ Settings")

        # Voice
        voice_menu = rumps.MenuItem("🔊 Voice")
        for voice_id, voice_name in VOICES:
            item = rumps.MenuItem(voice_name, callback=self._set_voice)
            item.voice_id = voice_id
            voice_menu.add(item)
        settings_menu.add(voice_menu)

        # Voice engine
        engine_menu = rumps.MenuItem("🧠 Voice Engine")
        for mode, label in [("auto", "Auto (local/cloud)"), ("true", "Local Only (MLX)"), ("false", "Cloud Only")]:
            item = rumps.MenuItem(label, callback=self._set_engine)
            item.mode = mode
            engine_menu.add(item)
        settings_menu.add(engine_menu)

        settings_menu.add(None)  # separator

        # Quick settings
        settings_menu.add(rumps.MenuItem("✏️ Edit Company Name...", callback=self._edit_company))
        settings_menu.add(rumps.MenuItem("📧 Edit Email...", callback=self._edit_email))
        settings_menu.add(rumps.MenuItem("👋 Edit Greeting...", callback=self._edit_greeting))
        settings_menu.add(rumps.MenuItem("🔑 Change PIN...", callback=self._edit_pin))

        settings_menu.add(None)  # separator
        settings_menu.add(rumps.MenuItem("🌐 Open Full Settings", callback=self.open_settings))

        return settings_menu

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
            rumps.alert("Server not installed", "Run install.sh first.")
            return
        ok, _ = self._launchctl("load", "-w", str(plist))
        if ok:
            rumps.notification(TITLE, "", "Server started")

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
                self._update_voicemail_menu()
            time.sleep(30)

    def _fetch_voicemails(self):
        try:
            r = requests.get(VOICEMAILS_URL, timeout=5)
            if r.status_code == 200:
                self.voicemails = r.json()
        except:
            pass

    def _update_voicemail_menu(self):
        """Update voicemail submenu with current voicemails."""
        self.vm_menu.clear()

        if not self.voicemails:
            self.vm_menu.add(rumps.MenuItem("No voicemails"))
            return

        for vm in reversed(self.voicemails):
            caller = vm.get("from", "Unknown").replace("+", "")
            duration = vm.get("duration", 0)
            transcript = vm.get("transcript", "")
            time_str = vm.get("time", "")

            # Format time
            try:
                dt = datetime.fromisoformat(time_str)
                time_display = dt.strftime("%b %d, %H:%M")
            except:
                time_display = time_str[:16] if time_str else ""

            # Menu item label
            label = f"📞 {caller} ({duration}s) {time_display}"
            item = rumps.MenuItem(label)
            self.vm_menu.add(item)

            # Sub-items for this voicemail
            if transcript:
                transcript_item = rumps.MenuItem(f'📝 "{transcript[:60]}..."')
                transcript_item.set_callback(None)
                self.vm_menu.add(transcript_item)

            if vm.get("audio_path"):
                play_item = rumps.MenuItem(f"▶️ Play", callback=self._play_voicemail)
                play_item.vm = vm
                self.vm_menu.add(play_item)

            callback_item = rumps.MenuItem(f"📞 Call Back", callback=self._callback_voicemail)
            callback_item.vm = vm
            self.vm_menu.add(callback_item)

            delete_item = rumps.MenuItem(f"🗑️ Delete", callback=self._delete_voicemail)
            delete_item.vm = vm
            self.vm_menu.add(delete_item)

            self.vm_menu.add(None)  # separator

        # Export options
        self.vm_menu.add(rumps.MenuItem("📦 Export All (ZIP)", callback=self._export_zip))
        self.vm_menu.add(rumps.MenuItem("📝 Export Transcripts", callback=self._export_transcripts))

    def _play_voicemail(self, sender):
        """Play a voicemail."""
        vm = sender.vm
        audio_path = vm.get("audio_path", "")
        if audio_path and Path(audio_path).exists():
            subprocess.Popen(["afplay", audio_path])
        else:
            rumps.notification(TITLE, "", "Audio file not available")

    def _callback_voicemail(self, sender):
        """Call back the voicemail sender."""
        vm = sender.vm
        caller = vm.get("from", "")
        if caller:
            self._do_call(caller)

    def _delete_voicemail(self, sender):
        """Delete a voicemail."""
        vm = sender.vm
        sid = vm.get("sid", "")
        if sid:
            try:
                requests.delete(f"{VOICEMAILS_URL}/{sid}", timeout=5)
                rumps.notification(TITLE, "", "Voicemail deleted")
                self._fetch_voicemails()
                self._update_voicemail_menu()
            except:
                rumps.notification(TITLE, "", "Failed to delete")

    def _export_zip(self, _):
        """Open export ZIP in browser."""
        webbrowser.open("http://localhost:5050/export/zip")

    def _export_transcripts(self, _):
        """Open export transcripts in browser."""
        webbrowser.open("http://localhost:5050/export/transcripts")

    # ── Make call ──────────────────────────────────────────────────

    def make_call(self, _):
        """Show dialog to make a call."""
        window = rumps.Window(
            message="Enter phone number to call:",
            title="Make Call",
            default_text="+",
            ok="Call",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        response = window.run()
        if response.clicked and response.text:
            self._do_call(response.text.strip())

    def _do_call(self, number):
        """Make an outbound call."""
        if not number:
            return
        try:
            r = requests.post(
                "http://localhost:5050/call",
                json={"to": number},
                timeout=10,
            )
            if r.status_code == 200:
                rumps.notification(TITLE, "", f"Calling {number}...")
            else:
                rumps.notification(TITLE, "", f"Call failed: {r.json().get('error', 'unknown')}")
        except Exception as e:
            rumps.notification(TITLE, "", f"Call failed: {e}")

    # ── Settings management ────────────────────────────────────────

    def _settings_loop(self):
        """Periodically fetch settings."""
        while True:
            if self.status == "running":
                self._fetch_settings()
            time.sleep(60)

    def _fetch_settings(self):
        """Fetch current settings from API."""
        try:
            r = requests.get(SETTINGS_URL, timeout=5)
            if r.status_code == 200:
                self.settings = r.json()
        except:
            pass

    def _save_setting(self, key, value):
        """Save a single setting."""
        try:
            r = requests.post(
                SETTINGS_URL,
                json={key: value},
                timeout=5,
            )
            if r.status_code == 200:
                rumps.notification(TITLE, "", f"{key} updated")
            else:
                rumps.notification(TITLE, "", f"Failed to update {key}")
        except:
            rumps.notification(TITLE, "", "Server not reachable")

    def _set_voice(self, sender):
        """Set TTS voice."""
        self._save_setting("TTS_VOICE", sender.voice_id)

    def _set_engine(self, sender):
        """Set voice engine mode."""
        self._save_setting("USE_LOCAL_VOICE", sender.mode)

    def _edit_company(self, _):
        """Edit company name."""
        current = self.settings.get("COMPANY_NAME", "")
        window = rumps.Window(
            message="Company name:",
            title="Company Name",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        response = window.run()
        if response.clicked and response.text:
            self._save_setting("COMPANY_NAME", response.text.strip())

    def _edit_email(self, _):
        """Edit voicemail email."""
        current = self.settings.get("VOICEMAIL_EMAIL", "")
        window = rumps.Window(
            message="Email for voicemail greeting:",
            title="Voicemail Email",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        response = window.run()
        if response.clicked and response.text:
            self._save_setting("VOICEMAIL_EMAIL", response.text.strip())

    def _edit_greeting(self, _):
        """Edit voicemail greeting."""
        current = self.settings.get("VOICEMAIL_GREETING", "")
        window = rumps.Window(
            message="Voicemail greeting (leave empty for default):",
            title="Voicemail Greeting",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(400, 100),
        )
        response = window.run()
        if response.clicked:
            self._save_setting("VOICEMAIL_GREETING", response.text.strip())

    def _edit_pin(self, _):
        """Change voicemail PIN."""
        current = self.settings.get("VOICEMAIL_PIN", "1234")
        window = rumps.Window(
            message="New PIN (callers dial this to reach AI):",
            title="Change PIN",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        response = window.run()
        if response.clicked and response.text:
            self._save_setting("VOICEMAIL_PIN", response.text.strip())

    # ── Other actions ──────────────────────────────────────────────

    def open_dashboard(self, _):
        webbrowser.open("http://localhost:5050/")

    def open_settings(self, _):
        webbrowser.open("http://localhost:5050/#settings")

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
