"""Invariant test: every env var read by the application must be present in
SETTINGS_SCHEMA so it can be edited from the web dashboard.

How it works:
  1. Parse every call to env("KEY"), os.environ.get("KEY"), os.environ["KEY"],
     and os.getenv("KEY") across the files listed in SCAN_FILES.
  2. Assert each discovered key is in server.SETTINGS_SCHEMA — unless it is in
     the ALLOWLIST below.

Allowlist criteria (document any addition here):
  - None currently: all env vars read by the code are in SETTINGS_SCHEMA.
"""

import re
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import server  # noqa: E402

# ─── Files to scan ────────────────────────────────────────────────────────────
SCAN_FILES = [
    PROJECT_ROOT / "server.py",
    PROJECT_ROOT / "provider_registry.py",
    PROJECT_ROOT / "native_settings.py",
    PROJECT_ROOT / "local_voice.py",
    PROJECT_ROOT / "agents" / "__init__.py",
    PROJECT_ROOT / "agents" / "base.py",
    PROJECT_ROOT / "agents" / "hermes_gateway.py",
    PROJECT_ROOT / "agents" / "openai_compat.py",
    PROJECT_ROOT / "agents" / "noop.py",
]

# ─── Allowlist ────────────────────────────────────────────────────────────────
# Keys that are intentionally NOT in SETTINGS_SCHEMA because they are
# infrastructure/runtime vars that should not be edited via the UI.
# Add entries here only with a documented reason.
# No entries: all env vars read by the code are covered by SETTINGS_SCHEMA.
ALLOWLIST: set[str] = set()

# ─── Regex patterns ───────────────────────────────────────────────────────────
# Matches: env("KEY"), os.environ.get("KEY"), os.environ["KEY"], os.getenv("KEY")
# Also handles single-quoted variants.
_PATTERN = re.compile(
    r"""(?:env|os\.environ\.get|os\.getenv)\s*\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""
    r"""|os\.environ\s*\[\s*['"]([A-Z_][A-Z0-9_]*)['"]"""
)


def _extract_env_keys(path: Path) -> set[str]:
    """Return all env var names read in *path*."""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    keys: set[str] = set()
    for m in _PATTERN.finditer(text):
        key = m.group(1) or m.group(2)
        if key:
            keys.add(key)
    return keys


def test_all_env_vars_in_schema():
    """Every env var read in application code must be editable from the UI."""
    all_keys: set[str] = set()
    for f in SCAN_FILES:
        all_keys |= _extract_env_keys(f)

    schema_keys = set(server.SETTINGS_SCHEMA.keys())
    missing = all_keys - schema_keys - ALLOWLIST

    assert not missing, (
        "The following env vars are READ by the code but NOT present in "
        "server.SETTINGS_SCHEMA (add them to the schema or to the ALLOWLIST "
        "with a documented reason):\n  "
        + "\n  ".join(sorted(missing))
    )


def test_allowlist_has_no_stale_entries():
    """Every key in the ALLOWLIST must NOT be in SETTINGS_SCHEMA.

    This catches the case where someone adds a key to the schema and forgets
    to remove it from the allowlist.
    """
    schema_keys = set(server.SETTINGS_SCHEMA.keys())
    stale = ALLOWLIST & schema_keys
    assert not stale, (
        "These ALLOWLIST entries are now in SETTINGS_SCHEMA — remove them "
        "from the allowlist:\n  " + "\n  ".join(sorted(stale))
    )
