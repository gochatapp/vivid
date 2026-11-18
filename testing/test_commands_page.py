"""
Verifies web/commands.html stays in sync with the `.help` command list.

Checks that every command documented by ``.help`` appears on the commands page,
that the page does not advertise stale commands, and that the page keeps the
same category headings as the help text.

Run:  python testing/test_commands_page.py
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

PAGE = ROOT / "web" / "commands.html"

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


def page_commands(html):
    """Extracts the command names shown in the page's .cmd-trigger labels."""
    names = set()
    triggers = re.findall(r'<div class="cmd-trigger">(.*?)</div>', html, flags=re.S)
    for trigger in triggers:
        names.update(match.lower() for match in re.findall(r"\.([a-zA-Z]+)", trigger))
    return names


def served_page():
    """Serves the web app in-process and returns the /commands response."""
    from aiohttp.test_utils import TestClient, TestServer

    from system.web_server import create_web_app

    async def get_page():
        app = create_web_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/commands")
            body = await resp.text()
            return resp.status, resp.headers.get("Content-Type", ""), body

    return asyncio.run(get_page())


def main():
    text = help_text()
    documented = documented_commands(text)
    html = PAGE.read_text(encoding="utf-8-sig") if PAGE.exists() else ""
    page = page_commands(html)

    print("\ncommands page")
    check("page exists", PAGE.exists(), str(PAGE))
    check("page lists commands", bool(page), "no .cmd-trigger entries found")

    missing = sorted(documented - page)
    check("every .help command is on the page", not missing,
          "missing: " + ", ".join(missing))

    stale = sorted(page - documented)
    check("page lists no stale commands", not stale,
          "stale: " + ", ".join(stale))

    print("\ncommands page sections")
    for heading in (
        "Help &amp; Roles",
        "Personas",
        "Providers &amp; Models",
        "Stories",
        "Chats &amp; History",
        "Settings &amp; Status",
        "Voice (text to speech)",
    ):
        check(f"section '{heading}' present", heading in html)

    print("\nGET /commands")
    status, content_type, body = served_page()
    check("/commands 200", status == 200, f"got {status}")
    check("/commands is html", "text/html" in content_type, content_type)
    check("/commands uses the static stylesheet", "/static/styles.css" in body)

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
