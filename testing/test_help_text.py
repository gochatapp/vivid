"""
Verifies the `.help` command stays in sync with the registered dot-commands.

Checks that every command wired up in ``setup_handlers`` is documented in the
help text, that no phantom commands are listed, and that the voice/TTS section
documents `.voice [n]` speed control and `.nar` voice selection.

Run:  python testing/test_help_text.py
"""

import asyncio
import re
import sys
import types
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import system.handlers as handlers_mod

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def registered_commands():
    """Collects command names from the handlers registered by setup_handlers."""
    handlers = []

    class FakeApp:
        def add_handler(self, handler, *args, **kwargs):
            handlers.append(handler)

    handlers_mod.setup_handlers(FakeApp())

    names = set()
    for handler in handlers:
        for command in getattr(handler, "commands", ()) or ():
            names.add(str(command).lstrip(".").lower())
    return names


def help_text():
    """Runs help_command with mocked IO and returns the message it sent."""
    sent = []

    async def fake_reply_and_log(update, text, is_command=True, bot=None):
        sent.append(text)

    update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=42))
    with mock.patch.object(handlers_mod, "get_telegram_user_id", return_value=42), \
            mock.patch.object(handlers_mod, "reply_and_log", fake_reply_and_log):
        asyncio.run(handlers_mod.help_command(update, None))
    return sent[0] if sent else ""


def documented_commands(text):
    """Extracts the command names shown as `.{command}` in the help text."""
    return {match.lower() for match in re.findall(r"`\.([a-zA-Z]+)", text)}


def main():
    text = help_text()
    documented = documented_commands(text)
    registered = registered_commands()

    print("\n.help coverage")
    check("help text is produced", bool(text), "no message sent")
    check("help text fits a Telegram message", 0 < len(text) < 4096, f"{len(text)} chars")
    check("help text mentions dot commands", bool(documented), str(sorted(documented)))

    missing = sorted(registered - documented)
    check("every registered command is documented", not missing, "missing: " + ", ".join(missing))

    unknown = sorted(documented - registered)
    check("help lists no unregistered commands", not unknown, "unknown: " + ", ".join(unknown))

    print("\n.help voice (TTS) section")
    check("voice toggle documented", "`.voice` / `.voice on|off`" in text)
    check("voice speed documented", "`.voice [1-10]`" in text)
    check("speed mapping explained", "`1` = normal, `2` = 1.2x, `9`+ = 1.9x" in text)
    check("decimal speed documented", "1.5" in text)
    check("nar listing documented", "`.nar` / `.narrator`" in text)
    check("nar selection documented", "`.nar [n|name]`" in text)
    check("nar name example documented", "`.nar sonia`" in text)

    print("\n.help length aliases")
    check("length aliases documented",
          "`.length [N]`" in text and "`.len [N]`" in text and "`.l [N]`" in text)

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
