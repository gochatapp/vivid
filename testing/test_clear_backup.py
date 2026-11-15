"""
Verifies `.clear` / `.clean` back up the active chat or story before wiping it.

Chat backups land in `chats/backups/`; story backups land in
`<story>/backups/`. Each backup is a timestamped `.txt` transcript plus a
`.json` session snapshot.

Run:  python testing/test_clear_backup.py
"""

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import system.bot_instance as bot_mod
import system.handlers as handlers_mod
from system.bot_instance import BotInstance

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


def make_history(role="Test_Role"):
    return [
        {"role": "system", "content": "system prompt", "msg_ids": [], "is_command": False},
        {"role": "user", "content": "hello there", "msg_ids": [11], "is_command": False},
        {"role": "assistant", "content": "hi there", "msg_ids": [12], "is_command": False},
    ]


def make_bot(history):
    bot = BotInstance("testbot", "token")
    bot.current_role = "Test_Role"
    bot.chat_history = history
    bot.save_active_context_and_status = lambda: None
    return bot


def make_update(text):
    reply = types.SimpleNamespace(message_id=999)

    async def reply_text(*args, **kwargs):
        return reply

    async def delete():
        return None

    message = types.SimpleNamespace(
        text=text,
        message_id=1,
        reply_text=reply_text,
        delete=delete,
    )
    return types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=42),
        effective_chat=types.SimpleNamespace(id=123),
        message=message,
    )


def make_context():
    deleted = []

    async def delete_message(chat_id=None, message_id=None):
        deleted.append(message_id)

    return types.SimpleNamespace(
        args=[],
        bot=types.SimpleNamespace(delete_message=delete_message),
    ), deleted


def run_chat_action(bot, text, chats_dir, stories_dir):
    """Runs chat_actions with temp backup paths and captures the transient reply."""
    update = make_update(text)
    context, deleted = make_context()
    notices = []

    async def fake_reply_transient(update, context, reply_text, delay=5):
        notices.append(reply_text)

    with mock.patch.object(bot_mod, "CHAT_BACKUPS_DIR", chats_dir), \
            mock.patch.object(bot_mod, "STORIES_DIR", stories_dir), \
            mock.patch.object(handlers_mod, "get_telegram_user_id", return_value=42), \
            mock.patch.object(handlers_mod, "get_bot", return_value=bot), \
            mock.patch.object(handlers_mod, "reply_transient", fake_reply_transient):
        asyncio.run(handlers_mod.chat_actions(update, context))

    return notices, deleted


def backups_in(path):
    if not path.exists():
        return []
    return sorted(path.glob("*"))


def test_chat_clear_backup():
    print("\n.clear chat backup")
    with tempfile.TemporaryDirectory() as tmp:
        chats_dir = Path(tmp) / "chats" / "backups"
        stories_dir = Path(tmp) / "stories"
        bot = make_bot(make_history())

        notices, deleted = run_chat_action(bot, ".clear", chats_dir, stories_dir)

        files = backups_in(chats_dir)
        transcripts = [f for f in files if f.suffix == ".txt"]
        snapshots = [f for f in files if f.suffix == ".json"]

        check("chat backup directory is created", chats_dir.exists(), str(chats_dir))
        check("one transcript written", len(transcripts) == 1, str(files))
        check("one snapshot written", len(snapshots) == 1, str(files))
        check("backup name includes role",
              bool(transcripts) and transcripts[0].name.startswith("Test_Role_"),
              str(transcripts))

        transcript = transcripts[0].read_text(encoding="utf-8") if transcripts else ""
        check("transcript keeps user text", "hello there" in transcript, transcript)
        check("transcript keeps assistant text", "hi there" in transcript, transcript)

        snapshot = json.loads(snapshots[0].read_text(encoding="utf-8")) if snapshots else {}
        check("snapshot keeps metadata role",
              snapshot.get("metadata", {}).get("role") == "Test_Role",
              str(snapshot.get("metadata")))
        check("snapshot keeps full history", len(snapshot.get("history", [])) == 3,
              str(len(snapshot.get("history", []))))

        check("history reset to system prompt", len(bot.chat_history) == 1
              and bot.chat_history[0]["role"] == "system", str(bot.chat_history))
        check("notice names the backup",
              bool(transcripts) and f"`{transcripts[0].name}`" in notices[-1], str(notices))
        check(".clear does not delete Telegram messages", deleted == [], str(deleted))


def test_story_clear_backup():
    print("\n.clear story backup")
    with tempfile.TemporaryDirectory() as tmp:
        chats_dir = Path(tmp) / "chats" / "backups"
        stories_dir = Path(tmp) / "stories"
        bot = make_bot(make_history())
        bot.current_mode = "story"
        bot.active_story = "Test_Story"

        notices, deleted = run_chat_action(bot, ".clear", chats_dir, stories_dir)

        story_backup_dir = stories_dir / "Test_Story" / "backups"
        files = backups_in(story_backup_dir)

        check("story backup directory is created", story_backup_dir.exists(), str(story_backup_dir))
        check("story backup uses the story folder", not backups_in(chats_dir), str(backups_in(chats_dir)))
        check("one story transcript written",
              len([f for f in files if f.suffix == ".txt"]) == 1, str(files))
        check("one story snapshot written",
              len([f for f in files if f.suffix == ".json"]) == 1, str(files))
        check("story backup name includes story",
              all(f.name.startswith("Test_Story_") for f in files), str(files))
        check("story history reset", len(bot.chat_history) == 1, str(bot.chat_history))
        check("notice names the story backup",
              bool(notices) and "Test_Story_" in notices[-1], str(notices))


def test_clean_backs_up_and_deletes():
    print("\n.clean backup and UI cleanup")
    with tempfile.TemporaryDirectory() as tmp:
        chats_dir = Path(tmp) / "chats" / "backups"
        stories_dir = Path(tmp) / "stories"
        bot = make_bot(make_history())

        notices, deleted = run_chat_action(bot, ".clean", chats_dir, stories_dir)

        check(".clean writes a backup", len(backups_in(chats_dir)) == 2, str(backups_in(chats_dir)))
        check(".clean deletes both Telegram messages", deleted == [12, 11], str(deleted))
        check(".clean resets history", len(bot.chat_history) == 1, str(bot.chat_history))


def test_empty_history_skips_backup():
    print("\n.clear with nothing to back up")
    with tempfile.TemporaryDirectory() as tmp:
        chats_dir = Path(tmp) / "chats" / "backups"
        stories_dir = Path(tmp) / "stories"
        bot = make_bot([{"role": "system", "content": "system prompt",
                         "msg_ids": [], "is_command": False}])

        notices, deleted = run_chat_action(bot, ".clear", chats_dir, stories_dir)

        check("no backup for empty history", backups_in(chats_dir) == [], str(backups_in(chats_dir)))
        check("notice stays short", bool(notices) and "Backup:" not in notices[-1], str(notices))


def main():
    test_chat_clear_backup()
    test_story_clear_backup()
    test_clean_backs_up_and_deletes()
    test_empty_history_skips_backup()

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
