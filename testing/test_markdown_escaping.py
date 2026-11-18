"""
Verifies command replies are converted to valid MarkdownV2 before sending.

Regression test for:

    telegram.error.BadRequest: Can't parse entities: character '(' is
    reserved and must be escaped with the preceding '\'

raised by `.s` (and every other command) because ``reply_and_log`` always
sent with ``parse_mode="MarkdownV2"`` while handlers write loose Markdown
(``**bold**``, raw parentheses, dots, hyphens). LLM replies used the lenient
``Markdown`` mode, which is why only commands failed.

Run:  python testing/test_markdown_escaping.py
"""

import asyncio
import sys
import types
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telegram.error import BadRequest

import system.handlers as handlers_mod
from system.bot_instance import (
    MARKDOWN_V2_RESERVED,
    BotInstance,
    escape_markdown_v2,
    markdown_to_v2,
    reply_and_log,
    reply_transient,
    send_with_fallback,
)

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


def is_valid_markdown_v2(text):
    """Mimics Telegram's MarkdownV2 parser closely enough to catch regressions.

    Returns ``(ok, reason)``. Every reserved character must be escaped, code
    spans must be closed and bold markers must come in pairs.
    """
    index = 0
    length = len(text)
    bold_open = False
    while index < length:
        char = text[index]
        if char == "\\":
            if index + 1 >= length:
                return False, f"dangling backslash at {index}"
            following = text[index + 1]
            if following not in MARKDOWN_V2_RESERVED and following != "\\":
                return False, f"invalid escape \\{following} at {index}"
            index += 2
            continue
        if char == "`":
            closing = text.find("`", index + 1)
            if closing == -1:
                return False, f"unclosed code span at {index}"
            index = closing + 1
            continue
        if char == "*":
            bold_open = not bold_open
            index += 1
            continue
        if char in MARKDOWN_V2_RESERVED:
            return False, f"unescaped {char!r} at {index}"
        index += 1
    if bold_open:
        return False, "unclosed bold span"
    return True, ""


class Recorder:
    """Fake Update/Application that records sends and can reject markup."""

    def __init__(self, reject_parse_modes=()):
        self.attempts = []      # every send attempt, even rejected ones
        self.reply_calls = []   # successful update.message.reply_text calls
        self.send_calls = []    # successful bot.app.bot.send_message calls
        self.deleted = 0
        self.reject_parse_modes = set(reject_parse_modes)
        self._next_id = 500

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def _maybe_reject(self, parse_mode):
        if parse_mode in self.reject_parse_modes:
            raise BadRequest(
                "Can't parse entities: character '(' is reserved and must be "
                "escaped with the preceding '\\'"
            )

    def make_update(self):
        recorder = self

        async def reply_text(text, parse_mode=None):
            recorder.attempts.append((text, parse_mode))
            recorder._maybe_reject(parse_mode)
            recorder.reply_calls.append((text, parse_mode))
            return types.SimpleNamespace(message_id=recorder._new_id())

        async def delete():
            recorder.deleted += 1

        message = types.SimpleNamespace(
            message_id=1, reply_text=reply_text, delete=delete
        )
        return types.SimpleNamespace(
            effective_user=types.SimpleNamespace(id=42),
            effective_chat=types.SimpleNamespace(id=123),
            message=message,
        )

    def make_app(self):
        recorder = self

        async def send_message(chat_id=None, text=None, parse_mode=None):
            recorder.attempts.append((text, parse_mode))
            recorder._maybe_reject(parse_mode)
            recorder.send_calls.append((chat_id, text, parse_mode))
            return types.SimpleNamespace(message_id=recorder._new_id())

        return types.SimpleNamespace(bot=types.SimpleNamespace(send_message=send_message))


def make_bot():
    bot = BotInstance("testbot", "token")
    bot.chat_history = [
        {"role": "system", "content": "system prompt", "msg_ids": [], "is_command": False},
        {"role": "user", "content": "hello", "msg_ids": [11], "is_command": False},
        {"role": "assistant", "content": "hi", "msg_ids": [], "is_command": False},
    ]
    # Never touch the filesystem during the test.
    bot.save_active_context_and_status = lambda: None
    return bot



def test_escape_reserved():
    print("\nescape_markdown_v2: reserved characters")
    check("parentheses and dots escaped",
          escape_markdown_v2("Status (Bot) v1.2!") == "Status \\(Bot\\) v1\\.2\\!",
          repr(escape_markdown_v2("Status (Bot) v1.2!")))
    check("hyphen run escaped", escape_markdown_v2("---") == "\\-\\-\\-")
    check("backslash escaped", escape_markdown_v2("C:\\dev") == "C:\\\\dev")
    check("plain text untouched", escape_markdown_v2("hello world") == "hello world")
    check("empty text untouched", escape_markdown_v2("") == "")


def test_bold_spans():
    print("\nmarkdown_to_v2: bold spans")
    check("double-star bold becomes MarkdownV2 bold",
          markdown_to_v2("📊 **Status (Bot: Nanami)**") == "📊 *Status \\(Bot: Nanami\\)*",
          repr(markdown_to_v2("📊 **Status (Bot: Nanami)**")))
    check("single-star bold is preserved",
          markdown_to_v2("*Help and roles*") == "*Help and roles*",
          repr(markdown_to_v2("*Help and roles*")))
    check("unpaired double stars stay literal",
          markdown_to_v2("**oops") == "\\*\\*oops",
          repr(markdown_to_v2("**oops")))
    check("empty bold span stays literal",
          markdown_to_v2("****") == "\\*\\*\\*\\*",
          repr(markdown_to_v2("****")))
    check("lone asterisk and equals are escaped",
          markdown_to_v2("2 * 3 = 6") == "2 \\* 3 \\= 6",
          repr(markdown_to_v2("2 * 3 = 6")))


def test_code_spans():
    print("\nmarkdown_to_v2: code spans")
    check("code keeps reserved characters",
          markdown_to_v2("Use `a(b) [c]` now") == "Use `a(b) [c]` now",
          repr(markdown_to_v2("Use `a(b) [c]` now")))
    check("unclosed backtick is escaped",
          markdown_to_v2("oops `here") == "oops \\`here",
          repr(markdown_to_v2("oops `here")))
    check("voice line renders",
          markdown_to_v2("🎙️ Voice: `af_bella` Speed: `1.0x`") == "🎙️ Voice: `af_bella` Speed: `1.0x`",
          repr(markdown_to_v2("🎙️ Voice: `af_bella` Speed: `1.0x`")))


def test_already_escaped_not_doubled():
    print("\nmarkdown_to_v2: pre-escaped text is not escaped twice")
    escaped = escape_markdown_v2("📊 Current context limit: 4096 tokens (~4k)")
    check("escape_markdown_v2 output is unchanged",
          markdown_to_v2(escaped) == escaped, repr(markdown_to_v2(escaped)))

    legacy = handlers_mod.escape_markdown("⚠️ Set to 8k (8192 tokens)")
    check("module escape_markdown output is unchanged",
          markdown_to_v2(legacy) == legacy, repr(markdown_to_v2(legacy)))


def test_status_text_is_valid():
    print("\n.status text: conversion produces valid MarkdownV2")
    status_text = (
        "📊 **Status (Bot: Nanami)**\n---\n"
        "🌐 Provider: `ollama`\n"
        "🧠 Model: `llama3:8b`\n"
        "🎭 Role: `Nanami`\n"
        "👤 Persona: `None`\n"
        "👥 Active Chars: `Nanami, Blaire`\n"
        "🎬 Active Scenes: `None`\n"
        "💾 Context: ~4.0k used of 4k tokens (≈5 msgs, 3.2KB)\n"
        "📏 Length: `~150 words`\n"
        "🧠 LLM Ctx: `4096`"
    )
    converted = markdown_to_v2(status_text)
    valid, reason = is_valid_markdown_v2(converted)
    check("converted status text is valid MarkdownV2", valid, reason)
    check("bold title kept with escaped parentheses",
          "*Status \\(Bot: Nanami\\)*" in converted, converted)
    check("raw parentheses are escaped", "\\(" in converted and "\\)" in converted)


def test_help_text_is_valid():
    print("\n.help text: conversion produces valid MarkdownV2")
    sent = []

    async def fake_reply_and_log(update, text, is_command=True, bot=None):
        sent.append(text)

    update = types.SimpleNamespace(effective_user=types.SimpleNamespace(id=42))
    with mock.patch.object(handlers_mod, "get_telegram_user_id", return_value=42), \
            mock.patch.object(handlers_mod, "reply_and_log", fake_reply_and_log):
        asyncio.run(handlers_mod.help_command(update, None))

    help_text = sent[0] if sent else ""
    converted = markdown_to_v2(help_text)
    valid, reason = is_valid_markdown_v2(converted)
    check("help text is produced", bool(help_text), "no message sent")
    check("converted help text is valid MarkdownV2", valid, reason)
    check("help section titles stay bold",
          "*Help and roles*" in converted, converted[:160])


def test_reply_and_log_converts():
    print("\nreply_and_log: converts text and sends MarkdownV2")
    recorder = Recorder()
    bot = make_bot()
    bot.app = recorder.make_app()
    update = recorder.make_update()

    asyncio.run(reply_and_log(update, "📊 **Status (Bot: testbot)**", bot=bot))

    check("one reply sent", len(recorder.reply_calls) == 1, str(recorder.reply_calls))
    sent_text, parse_mode = recorder.reply_calls[0]
    check("parse mode is MarkdownV2", parse_mode == "MarkdownV2", str(parse_mode))
    check("text is escaped", sent_text == "📊 *Status \\(Bot: testbot\\)*", repr(sent_text))
    check("message id logged",
          bot.chat_history[-1]["msg_ids"] == [501],
          str(bot.chat_history[-1]["msg_ids"]))


def test_reply_and_log_falls_back_to_plain_text():
    print("\nreply_and_log: falls back to plain text when markup is rejected")
    recorder = Recorder(reject_parse_modes={"MarkdownV2"})
    bot = make_bot()
    bot.app = recorder.make_app()
    update = recorder.make_update()

    asyncio.run(reply_and_log(update, "Status (Bot)", bot=bot))

    check("two send attempts", len(recorder.attempts) == 2, str(recorder.attempts))
    check("first attempt uses MarkdownV2", recorder.attempts[0][1] == "MarkdownV2")
    check("retry disables parse mode", recorder.attempts[1][1] is None,
          str(recorder.attempts[1]))
    check("reply is delivered", len(recorder.reply_calls) == 1, str(recorder.reply_calls))


def test_reply_transient_converts():
    print("\nreply_transient: converts text and sends MarkdownV2")
    recorder = Recorder()
    update = recorder.make_update()

    with mock.patch("system.bot_instance.asyncio.create_task") as create_task:
        create_task.side_effect = lambda coro: coro.close()
        asyncio.run(reply_transient(
            update, None, "✅ Context limit set to 4096 tokens (4k)."
        ))

    check("trigger message deleted", recorder.deleted == 1, str(recorder.deleted))
    check("one reply sent", len(recorder.reply_calls) == 1, str(recorder.reply_calls))
    sent_text, parse_mode = recorder.reply_calls[0]
    check("parse mode is MarkdownV2", parse_mode == "MarkdownV2", str(parse_mode))
    check("text is escaped",
          sent_text == "✅ Context limit set to 4096 tokens \\(4k\\)\\.",
          repr(sent_text))


def test_send_with_fallback_propagates_other_errors():
    print("\nsend_with_fallback: unrelated errors are not swallowed")

    async def failing_send(text, parse_mode=None):
        raise BadRequest("Chat not found")

    try:
        asyncio.run(send_with_fallback(failing_send, "hi"))
    except BadRequest as error:
        check("error propagates", "Chat not found" in str(error), str(error))
    else:
        check("error propagates", False, "no error raised")


def main():
    test_escape_reserved()
    test_bold_spans()
    test_code_spans()
    test_already_escaped_not_doubled()
    test_status_text_is_valid()
    test_help_text_is_valid()
    test_reply_and_log_converts()
    test_reply_and_log_falls_back_to_plain_text()
    test_reply_transient_converts()
    test_send_with_fallback_propagates_other_errors()

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

