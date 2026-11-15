"""
Verifies long replies are split so Telegram never raises
``BadRequest: Message is too long`` (the 4096-character per-message limit).

Covers the ``split_telegram_message`` helper and the ``reply_and_log`` send path
used by ``.last`` and every other command.

Run:  python testing/test_message_split.py
"""

import asyncio
import sys
import types
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from system.bot_instance import BotInstance, reply_and_log, split_telegram_message
from system.config import TELEGRAM_MESSAGE_LIMIT

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


def make_history():
    return [
        {"role": "system", "content": "system prompt", "msg_ids": [], "is_command": False},
        {"role": "user", "content": "hello", "msg_ids": [11], "is_command": False},
        {"role": "assistant", "content": "hi", "msg_ids": [], "is_command": False},
    ]


def make_bot(history):
    bot = BotInstance("testbot", "token")
    bot.chat_history = history
    # Never touch the filesystem during the test.
    bot.save_active_context_and_status = lambda: None
    return bot


class Recorder:
    """Fake Update/Application that records sends and returns incrementing ids."""

    def __init__(self):
        self.reply_calls = []  # chunks sent via update.message.reply_text
        self.send_calls = []   # chunks sent via bot.app.bot.send_message
        self._next_id = 500

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def make_update(self):
        recorder = self

        async def reply_text(text, parse_mode=None):
            recorder.reply_calls.append((text, parse_mode))
            return types.SimpleNamespace(message_id=recorder._new_id())

        message = types.SimpleNamespace(message_id=1, reply_text=reply_text)
        return types.SimpleNamespace(
            effective_user=types.SimpleNamespace(id=42),
            effective_chat=types.SimpleNamespace(id=123),
            message=message,
        )

    def make_app(self):
        recorder = self

        async def send_message(chat_id=None, text=None, parse_mode=None):
            recorder.send_calls.append((chat_id, text, parse_mode))
            return types.SimpleNamespace(message_id=recorder._new_id())

        return types.SimpleNamespace(bot=types.SimpleNamespace(send_message=send_message))


def test_split_short_unchanged():
    print("\nsplit: short text is untouched")
    check("short text returns one identical chunk",
          split_telegram_message("hello") == ["hello"])


def test_split_exact_limit():
    print("\nsplit: text at the limit is not split")
    text = "y" * TELEGRAM_MESSAGE_LIMIT
    check("exact-limit text stays one chunk",
          split_telegram_message(text) == [text])


def test_split_respects_limit():
    print("\nsplit: oversized text stays under the limit and is lossless")
    text = ("word " * 3000) + "\n" + ("z" * 10000)
    chunks = split_telegram_message(text)
    check("more than one chunk produced", len(chunks) > 1, str(len(chunks)))
    check("every chunk within the limit",
          all(len(c) <= TELEGRAM_MESSAGE_LIMIT for c in chunks),
          str(max(len(c) for c in chunks)))
    check("joining chunks reproduces the text", "".join(chunks) == text)


def test_split_empty():
    print("\nsplit: empty input")
    check("empty text returns one empty chunk", split_telegram_message("") == [""])


def test_reply_and_log_splits_with_app():
    print("\nreply_and_log: long reply is split across messages")
    recorder = Recorder()
    bot = make_bot(make_history())
    bot.app = recorder.make_app()
    update = recorder.make_update()
    text = "x" * (TELEGRAM_MESSAGE_LIMIT * 2 + 100)

    asyncio.run(reply_and_log(update, text, bot=bot))

    expected_chunks = len(split_telegram_message(text))
    check("first chunk replies to the command",
          len(recorder.reply_calls) == 1, str(len(recorder.reply_calls)))
    check("remaining chunks sent as follow-ups",
          len(recorder.send_calls) == expected_chunks - 1,
          f"{len(recorder.send_calls)} vs {expected_chunks - 1}")
    check("follow-ups use the same chat",
          all(c[0] == 123 for c in recorder.send_calls), str(recorder.send_calls))
    logged = bot.chat_history[-1].get("msg_ids", [])
    check("all sent ids logged to history",
          len(logged) == expected_chunks, str(logged))
    joined = recorder.reply_calls[0][0] + "".join(c[1] for c in recorder.send_calls)
    check("split content round-trips", joined == text)


def test_reply_and_log_fallback_without_app():
    print("\nreply_and_log: falls back to reply_text without an app")
    recorder = Recorder()
    bot = make_bot(make_history())
    bot.app = None
    update = recorder.make_update()
    text = "a" * (TELEGRAM_MESSAGE_LIMIT + 50)

    asyncio.run(reply_and_log(update, text, bot=bot))

    expected_chunks = len(split_telegram_message(text))
    check("all chunks replied to the command",
          len(recorder.reply_calls) == expected_chunks, str(len(recorder.reply_calls)))
    check("no follow-ups via app", recorder.send_calls == [], str(recorder.send_calls))


def test_reply_and_log_short_single_message():
    print("\nreply_and_log: short reply stays a single message")
    recorder = Recorder()
    bot = make_bot(make_history())
    bot.app = recorder.make_app()
    update = recorder.make_update()

    asyncio.run(reply_and_log(update, "short reply", bot=bot))

    check("one reply sent", len(recorder.reply_calls) == 1, str(recorder.reply_calls))
    check("no follow-up messages", recorder.send_calls == [], str(recorder.send_calls))
    check("one id logged", len(bot.chat_history[-1]["msg_ids"]) == 1,
          str(bot.chat_history[-1]["msg_ids"]))


def main():
    test_split_short_unchanged()
    test_split_exact_limit()
    test_split_respects_limit()
    test_split_empty()
    test_reply_and_log_splits_with_app()
    test_reply_and_log_fallback_without_app()
    test_reply_and_log_short_single_message()

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)


