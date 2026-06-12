"""
Hermes Phone — Native macOS Settings Window

Pure AppKit: NSTabView, NSTextField, NSSecureTextField, NSPopUpButton, NSButton.
No web views, no HTML — looks and feels like a real Mac preferences pane.
"""

import requests

try:
    import objc
    from AppKit import (
        NSApp, NSApplication, NSWindow, NSView, NSButton, NSTextField,
        NSSecureTextField, NSPopUpButton, NSTabView, NSTabViewItem,
        NSScrollView, NSClipView, NSStackView, NSBox, NSAlert,
        NSMakeRect, NSBackingStoreBuffered, NSBezelStyleRounded,
        NSBezelStyleRegularSquare, NSButtonTypeSwitch, NSButtonTypeMomentaryPushIn,
        NSRoundedBezelStyle,
        NSLayoutAttributeTop, NSLayoutAttributeLeading, NSLayoutAttributeTrailing,
        NSLayoutAttributeBottom, NSLayoutAttributeWidth, NSLayoutAttributeCenterX,
        NSLayoutRelationEqual, NSLayoutPriorityRequired,
        NSUserInterfaceLayoutOrientationVertical,
        NSUserInterfaceLayoutOrientationHorizontal,
        NSStackViewGravityTop, NSStackViewGravityLeading,
        NSStackViewGravityTrailing, NSStackViewGravityBottom,
        NSControlSizeRegular, NSControlSizeSmall,
        NSTextAlignmentLeft, NSTextAlignmentRight,
        NSLineBreakByTruncatingTail,
        NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
        NSWindowStyleMaskResizable, NSWindowStyleMaskMiniaturizable,
        NSApplicationActivationPolicyAccessory,
        NSCompositingOperationSourceOver,
    )
    from Foundation import NSObject, NSMakeSize
    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False


# ═══════════════════════════════════════════════════════════════════
# Settings field definitions
# ═══════════════════════════════════════════════════════════════════

TABS = {
    "General": [
        {"key": "COMPANY_NAME", "label": "Company Name", "type": "text", "placeholder": "My Company"},
        {"key": "VOICEMAIL_EMAIL", "label": "Voicemail Email", "type": "text", "placeholder": "hello@company.com"},
        {"key": "VOICEMAIL_GREETING", "label": "Voicemail Greeting", "type": "text", "placeholder": "Leave empty for default"},
        {"key": "VOICEMAIL_PIN", "label": "Voicemail PIN", "type": "text", "placeholder": "1234"},
        {"key": "VOICEMAIL_MAX_LENGTH", "label": "Max Recording (sec)", "type": "text", "placeholder": "120"},
        {"key": "DASHBOARD_TOKEN", "label": "Dashboard Password", "type": "secure", "placeholder": "••••••••"},
    ],
    "Voice": [
        {"key": "STT_PROVIDER", "label": "STT Provider", "type": "dropdown", "source": "stt_providers"},
        {"key": "DEEPGRAM_API_KEY", "label": "Deepgram API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "ASSEMBLYAI_API_KEY", "label": "AssemblyAI API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "GROQ_API_KEY", "label": "Groq API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "SPEECHMATICS_API_KEY", "label": "Speechmatics API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "TTS_PROVIDER", "label": "TTS Provider", "type": "dropdown", "source": "tts_providers"},
        {"key": "TTS_VOICE", "label": "TTS Voice", "type": "dropdown", "source": "voices"},
        {"key": "TTS_LANGUAGE", "label": "Language", "type": "dropdown", "source": "languages"},
        {"key": "ELEVENLABS_API_KEY", "label": "ElevenLabs API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "ELEVENLABS_VOICE_ID", "label": "ElevenLabs Voice ID", "type": "text", "placeholder": "21m00Tcm4TlvDq8ikWAM"},
        {"key": "CARTESIA_API_KEY", "label": "Cartesia API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "CARTESIA_VOICE_ID", "label": "Cartesia Voice ID", "type": "text", "placeholder": "sonic-voice-id"},
        {"key": "USE_LOCAL_VOICE", "label": "Voice Engine (Local)", "type": "dropdown", "source": "local_voice"},
    ],
    "AI Agent": [
        {"key": "AGENT_PROVIDER", "label": "Agent Backend", "type": "dropdown", "source": "agent_providers"},
        {"key": "HERMES_GATEWAY_URL", "label": "Gateway URL", "type": "text", "placeholder": "http://127.0.0.1:8642"},
        {"key": "HERMES_GATEWAY_TOKEN", "label": "Gateway Token", "type": "secure", "placeholder": "••••••••"},
        {"key": "HERMES_MODEL_OVERRIDE", "label": "Model Override", "type": "text", "placeholder": "Leave empty for agent default"},
        {"key": "LLM_PROVIDER", "label": "Legacy LLM Provider", "type": "dropdown", "source": "llm_providers"},
        {"key": "LLM_MODEL", "label": "Legacy LLM Model", "type": "text", "placeholder": "mimo-v2.5"},
        {"key": "XIAOMI_API_KEY", "label": "Xiaomi API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "XIAOMI_BASE_URL", "label": "Xiaomi Base URL", "type": "text", "placeholder": "https://token-plan-ams.xiaomimimo.com/v1"},
        {"key": "OPENAI_API_KEY", "label": "OpenAI API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "OPENAI_BASE_URL", "label": "OpenAI Base URL", "type": "text", "placeholder": "https://api.openai.com/v1"},
        {"key": "OPENROUTER_API_KEY", "label": "OpenRouter API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "LLM_BASE_URL_OVERRIDE", "label": "Custom Base URL", "type": "text", "placeholder": "https://your-endpoint.com/v1"},
        {"key": "LLM_API_KEY_OVERRIDE", "label": "Custom API Key", "type": "secure", "placeholder": "••••••••"},
        {"key": "LLM_MODEL_OVERRIDE", "label": "Custom Model", "type": "text", "placeholder": "Leave empty for default"},
        {"key": "CALL_GOAL", "label": "Call Goal", "type": "text", "placeholder": "Have a helpful conversation."},
        {"key": "CALL_SYSTEM_PROMPT", "label": "System Prompt", "type": "text", "placeholder": "Leave empty for default"},
    ],
    "Providers": [
        {"key": "TWILIO_ACCOUNT_SID", "label": "Twilio Account SID", "type": "text", "placeholder": "ACxxxxxxxx"},
        {"key": "TWILIO_AUTH_TOKEN", "label": "Twilio Auth Token", "type": "secure", "placeholder": "••••••••"},
        {"key": "TWILIO_PHONE_NUMBER", "label": "Twilio Phone Number", "type": "text", "placeholder": "+443xxxxxxxxx"},
        {"key": "TELEGRAM_BOT_TOKEN", "label": "Telegram Bot Token", "type": "secure", "placeholder": "••••••••"},
        {"key": "TELEGRAM_CHAT_ID", "label": "Telegram Chat ID", "type": "text", "placeholder": "Your chat ID"},
    ],
    "Network": [
        {"key": "WEBHOOK_PORT", "label": "Webhook Port (public)", "type": "text", "placeholder": "5050"},
        {"key": "DASHBOARD_PORT", "label": "Dashboard Port (protected)", "type": "text", "placeholder": "5051"},
        {"key": "WEBHOOK_URL_OVERRIDE", "label": "Webhook URL Override", "type": "text", "placeholder": "Leave empty to auto-detect"},
    ],
}

# Dropdown sources (static values — dynamic ones loaded from API)
STATIC_DROPDOWNS = {
    "languages": [
        ("en-GB", "English (UK)"),
        ("en-US", "English (US)"),
        ("en-AU", "English (AU)"),
    ],
    "local_voice": [
        ("auto", "Auto (local if available)"),
        ("true", "Local Only (MLX)"),
        ("false", "Cloud Only"),
    ],
    "llm_providers": [
        ("xiaomi", "Xiaomi MiMo"),
        ("openai", "OpenAI"),
        ("openrouter", "OpenRouter"),
    ],
    "agent_providers": [
        ("", "Auto-detect (recommended)"),
        ("hermes-gateway", "Hermes Agent (Gateway API)"),
        ("openai", "OpenAI"),
        ("xiaomi", "Xiaomi MiMo"),
        ("openrouter", "OpenRouter"),
        ("ollama", "Ollama (local)"),
        ("lmstudio", "LM Studio (local)"),
        ("openai-compat", "Custom OpenAI-Compatible"),
    ],
}


# ═══════════════════════════════════════════════════════════════════
# Window Delegate (stops modal loop on close)
# ═══════════════════════════════════════════════════════════════════

if HAS_APPKIT:
    class _WindowDelegate(NSObject):
        _parent = None

        def windowWillClose_(self, notification):
            NSApplication.sharedApplication().stopModal()


# ═══════════════════════════════════════════════════════════════════
# Native Settings Window
# ═══════════════════════════════════════════════════════════════════

class NativeSettingsWindow:
    """Pure AppKit settings window — no web views."""

    def __init__(self, api_url="http://localhost:5051", token=""):
        self.api_url = api_url
        self.token = token
        self.fields = {}  # key -> NSControl
        self.dropdowns = {}  # key -> NSPopUpButton
        self.window = None
        self.status_labels = {}
        self._original_settings = {}

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def show(self):
        if not HAS_APPKIT:
            import webbrowser
            webbrowser.open(f"{self.api_url}/?token={self.token}")
            return

        # Create window
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                 NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 680, 620), style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Hermes Phone Settings")
        self.window.setMinSize_(NSMakeSize(560, 480))
        self.window.center()

        # Tab view
        tab_view = NSTabView.alloc().initWithFrame_(NSMakeRect(16, 60, 648, 540))
        tab_view.setAutoresizingMask_(18)  # width + height flexible

        for tab_name, fields in TABS.items():
            tab_item = NSTabViewItem.alloc().initWithIdentifier_(tab_name)
            tab_item.setLabel_(tab_name)

            # Scroll view inside tab
            scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 640, 500))
            scroll.setHasVerticalScroller_(True)
            scroll.setAutoresizingMask_(18)

            # Content view inside scroll
            content = self._build_tab_content(fields)
            scroll.setDocumentView_(content)

            tab_item.setView_(scroll)
            tab_view.addTabViewItem_(tab_item)

        self.window.contentView().addSubview_(tab_view)

        # Bottom buttons
        self._add_bottom_buttons()

        # Load settings from API
        self._load_settings()

        self.window.makeKeyAndOrderFront_(None)

        # Delegate to stop modal loop when window is closed
        delegate = _WindowDelegate.alloc().init()
        delegate._parent = self
        self.window.setDelegate_(delegate)

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _build_tab_content(self, fields):
        """Build a scrollable column of labeled fields."""
        width = 610
        row_height = 52
        content_h = len(fields) * row_height + 20
        # AppKit y=0 is bottom; start at top and work down
        y = content_h - row_height - 10

        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, content_h))

        for field_def in fields:
            key = field_def["key"]
            label_text = field_def["label"]
            ftype = field_def["type"]

            # Label
            label = NSTextField.labelWithString_(label_text)
            label.setFrame_(NSMakeRect(10, y + 26, 200, 16))
            label.setFont_(self._system_font(11))
            label.setTextColor_(self._color(0.55, 0.55, 0.55))
            content.addSubview_(label)

            # Field
            if ftype == "secure":
                field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(10, y, width - 20, 22))
                field.setPlaceholderString_(field_def.get("placeholder", ""))
            elif ftype == "dropdown":
                field = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(10, y, width - 20, 26))
                source = field_def.get("source", "")
                self._populate_dropdown(field, source)
                self.dropdowns[key] = field
            else:
                field = NSTextField.alloc().initWithFrame_(NSMakeRect(10, y, width - 20, 22))
                field.setPlaceholderString_(field_def.get("placeholder", ""))

            field.setFont_(self._system_font(13))
            content.addSubview_(field)
            self.fields[key] = field

            y -= row_height

        return content

    def _populate_dropdown(self, popup, source):
        """Fill a popup button from static or dynamic data."""
        if source in STATIC_DROPDOWNS:
            for value, display in STATIC_DROPDOWNS[source]:
                popup.addItemWithTitle_(display)
                popup.lastItem().setRepresentedObject_(value)
        # Dynamic sources (stt_providers, tts_providers, voices) filled after load

    def _add_bottom_buttons(self):
        """Save and Reset buttons at the bottom."""
        # Status label
        self.status_field = NSTextField.labelWithString_("")
        self.status_field.setFrame_(NSMakeRect(20, 20, 300, 20))
        self.status_field.setFont_(self._system_font(11))
        self.status_field.setTextColor_(self._color(0.4, 0.8, 0.4))
        self.window.contentView().addSubview_(self.status_field)

        # Reset button
        reset_btn = NSButton.alloc().initWithFrame_(NSMakeRect(440, 16, 100, 32))
        reset_btn.setTitle_("Reset")
        reset_btn.setBezelStyle_(NSRoundedBezelStyle)
        reset_btn.setTarget_(self)
        reset_btn.setAction_(objc.selector(self._on_reset, signature=b"v@:@"))
        self.window.contentView().addSubview_(reset_btn)

        # Save button
        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(550, 16, 110, 32))
        save_btn.setTitle_("Save Settings")
        save_btn.setBezelStyle_(NSRoundedBezelStyle)
        save_btn.setKeyEquivalent_("\r")  # Enter key
        # Make it the default (blue) button
        save_btn.setTag_(1)
        save_btn.setTarget_(self)
        save_btn.setAction_(objc.selector(self._on_save, signature=b"v@:@"))
        self.window.contentView().addSubview_(save_btn)

    def _load_settings(self):
        """Fetch settings from API and populate fields."""
        try:
            r = requests.get(f"{self.api_url}/api/settings", headers=self._headers(), timeout=5)
            if r.status_code != 200:
                self._show_status("Failed to load settings", error=True)
                return

            settings = r.json()
            self._original_settings = dict(settings)

            for key, field in self.fields.items():
                val = settings.get(key, "")
                if key in self.dropdowns:
                    popup = self.dropdowns[key]
                    # Find and select matching item
                    for i in range(popup.numberOfItems()):
                        item = popup.itemAtIndex_(i)
                        if item.representedObject() == val or item.title() == val:
                            popup.selectItemAtIndex_(i)
                            break
                else:
                    field.setStringValue_(str(val))

            # Populate dynamic dropdowns from API response
            self._populate_dynamic_dropdowns(settings)

        except Exception as e:
            self._show_status(f"Error: {e}", error=True)

    def _populate_dynamic_dropdowns(self, settings):
        """Fill STT/TTS provider and voice dropdowns from API data."""
        # Agent providers
        agent_providers = settings.get("_agent_providers", [])
        if agent_providers and "AGENT_PROVIDER" in self.dropdowns:
            popup = self.dropdowns["AGENT_PROVIDER"]
            popup.removeAllItems()
            current = settings.get("AGENT_PROVIDER", "")
            for p in agent_providers:
                name = p.get("name", p.get("id", ""))
                pid = p.get("id", "")
                rec = " ⭐" if p.get("recommended") else ""
                label = f"{name}{rec}"
                popup.addItemWithTitle_(label)
                popup.lastItem().setRepresentedObject_(pid)
                if pid == current:
                    popup.selectItem_(popup.lastItem())

        # STT providers
        stt_providers = settings.get("_stt_providers", [])
        if stt_providers and "STT_PROVIDER" in self.dropdowns:
            popup = self.dropdowns["STT_PROVIDER"]
            popup.removeAllItems()
            current = settings.get("STT_PROVIDER", "")
            for p in stt_providers:
                name = p.get("name", p.get("id", ""))
                pid = p.get("id", "")
                rec = " ⭐" if p.get("recommended") else ""
                label = f"{name} ({p.get('type', '')}, {p.get('cost', '')}){rec}"
                popup.addItemWithTitle_(label)
                popup.lastItem().setRepresentedObject_(pid)
                if pid == current:
                    popup.selectItem_(popup.lastItem())

        # TTS providers
        tts_providers = settings.get("_tts_providers", [])
        if tts_providers and "TTS_PROVIDER" in self.dropdowns:
            popup = self.dropdowns["TTS_PROVIDER"]
            popup.removeAllItems()
            current = settings.get("TTS_PROVIDER", "")
            for p in tts_providers:
                name = p.get("name", p.get("id", ""))
                pid = p.get("id", "")
                rec = " ⭐" if p.get("recommended") else ""
                label = f"{name} ({p.get('type', '')}, {p.get('cost', '')}){rec}"
                popup.addItemWithTitle_(label)
                popup.lastItem().setRepresentedObject_(pid)
                if pid == current:
                    popup.selectItem_(popup.lastItem())

        # Voices
        voices = settings.get("_available_voices", [])
        if voices and "TTS_VOICE" in self.dropdowns:
            popup = self.dropdowns["TTS_VOICE"]
            popup.removeAllItems()
            current = settings.get("TTS_VOICE", "")
            for v in voices:
                vid = v.get("id", "")
                label = f"{v.get('name', '')} ({v.get('lang', '')}, {v.get('gender', '')})"
                popup.addItemWithTitle_(label)
                popup.lastItem().setRepresentedObject_(vid)
                if vid == current:
                    popup.selectItem_(popup.lastItem())

    def _on_save(self, sender):
        """Collect all field values and POST to API."""
        data = {}

        for key, field in self.fields.items():
            if key in self.dropdowns:
                popup = self.dropdowns[key]
                idx = popup.indexOfSelectedItem()
                if idx >= 0:
                    item = popup.itemAtIndex_(idx)
                    data[key] = item.representedObject() or item.title()
                else:
                    data[key] = ""
            else:
                data[key] = field.stringValue()

        try:
            r = requests.post(
                f"{self.api_url}/api/settings",
                headers=self._headers(),
                json=data,
                timeout=10,
            )
            if r.status_code == 200:
                result = r.json()
                updated = result.get("updated", [])
                deleted = result.get("deleted", [])

                # If DASHBOARD_TOKEN changed, update our local copy
                if "DASHBOARD_TOKEN" in data and data["DASHBOARD_TOKEN"]:
                    self.token = data["DASHBOARD_TOKEN"]

                msg = f"Saved {len(updated)} setting(s)"
                if deleted:
                    msg += f", cleared {len(deleted)} key(s)"
                self._show_status(msg)
            else:
                self._show_status(f"Save failed ({r.status_code})", error=True)
        except Exception as e:
            self._show_status(f"Error: {e}", error=True)

    def _on_reset(self, sender):
        """Reload settings from API, discarding changes."""
        self._load_settings()
        self._show_status("Reset to saved values")

    def _show_status(self, msg, error=False):
        """Show a status message that fades after 4 seconds."""
        self.status_field.setStringValue_(msg)
        if error:
            self.status_field.setTextColor_(self._color(0.9, 0.3, 0.3))
        else:
            self.status_field.setTextColor_(self._color(0.3, 0.8, 0.4))

        # Clear after 4 seconds
        import threading
        def clear():
            import time
            time.sleep(4)
            try:
                self.status_field.setStringValue_("")
            except Exception:
                pass
        threading.Thread(target=clear, daemon=True).start()

    @staticmethod
    def _system_font(size):
        from AppKit import NSFont
        return NSFont.systemFontOfSize_(size)

    @staticmethod
    def _color(r, g, b):
        from AppKit import NSColor
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


_active_window = None  # prevent GC


def open_settings(api_url="http://localhost:5051", token=""):
    """Entry point — call from menubar (runs on main thread)."""
    global _active_window
    _active_window = NativeSettingsWindow(api_url=api_url, token=token)
    _active_window.show()
    # Run as modal — blocks until window is closed, keeps it alive
    from AppKit import NSApplication
    NSApplication.sharedApplication().runModalForWindow_(_active_window.window)
