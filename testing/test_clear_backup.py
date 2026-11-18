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


class FakeTracker:
    """No-op usage tracker so switching tests never touch real settings files."""

    def is_chat_in_use(self, *args):
        return False

    def is_story_in_use(self, *args):
        return False

    def get_chat_owner(self, *args):
        return ""

    def get_story_owner(self, *args):
        return ""

    def claim_chat(self, *args):
        return None

    def claim_story(self, *args):
        return None


def make_temp_layout(tmp):
    """Creates the chat/story folders a bot reads and writes during a switch."""
    root = Path(tmp)
    layout = {
        "chats": root / "chats" / "backups",
        "context": root / "chats" / "chat_context",
        "config": root / "chats" / "config",
        "personas": root / "chats" / "personas",
        "roles": root / "chats" / "roles",
        "sessions": root / "chats" / "session",
        "stories": root / "stories",
    }
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    return layout


def patch_bot_dirs(layout):
    """Patches every BotInstance data folder plus content helpers to the temp layout."""
    roles_dir = layout["roles"]
    personas_dir = layout["personas"]

    def role_content(name):
        role_file = roles_dir / f"{name}.txt"
        return role_file.read_text(encoding="utf-8") if role_file.exists() else ""

    def persona_content(name):
        persona_file = personas_dir / f"{name}.txt"
        return persona_file.read_text(encoding="utf-8") if persona_file.exists() else ""

    return [
        mock.patch.object(bot_mod, "CHAT_BACKUPS_DIR", layout["chats"]),
        mock.patch.object(bot_mod, "CHAT_CONTEXT_DIR", layout["context"]),
        mock.patch.object(bot_mod, "CHAT_CONFIG_DIR", layout["config"]),
        mock.patch.object(bot_mod, "CHAT_PERSONAS_DIR", layout["personas"]),
        mock.patch.object(bot_mod, "CHAT_ROLES_DIR", layout["roles"]),
        mock.patch.object(bot_mod, "CHAT_SESSIONS_DIR", layout["sessions"]),
        mock.patch.object(bot_mod, "STORIES_DIR", layout["stories"]),
        mock.patch.object(bot_mod, "tracker", FakeTracker()),
        mock.patch.object(bot_mod, "get_role_content", role_content),
        mock.patch.object(bot_mod, "get_persona_content", persona_content),
    ]


def enter_all(patches):
    for patcher in patches:
        patcher.start()


def stop_all(patches):
    for patcher in patches:
        patcher.stop()



def test_persona_switch_keeps_conversation():
    print("\n.persona switch keeps the role conversation")
    with tempfile.TemporaryDirectory() as tmp:
        layout = make_temp_layout(tmp)
        (layout["roles"] / "Test_Role.txt").write_text("You are Test Role.", encoding="utf-8")
        (layout["personas"] / "Test_Persona.txt").write_text("You are Mikey.", encoding="utf-8")

        bot = make_bot(make_history())
        patches = patch_bot_dirs(layout)
        enter_all(patches)
        try:
            asyncio.run(bot.switch_persona("Test_Persona"))
        finally:
            stop_all(patches)

        system_prompt = bot.chat_history[0]["content"] if bot.chat_history else ""
        check("persona switch keeps all messages", len(bot.chat_history) == 3, str(bot.chat_history))
        check("persona text reaches the system prompt",
              "USER PERSONA" in system_prompt and "You are Mikey." in system_prompt,
              system_prompt)
        check("persona switch needs no reset backup",
              backups_in(layout["chats"]) == [], str(backups_in(layout["chats"])))


def test_role_switch_backs_up_conversation():
    print("\n.role switch backs up the outgoing conversation")
    with tempfile.TemporaryDirectory() as tmp:
        layout = make_temp_layout(tmp)
        (layout["roles"] / "Test_Role_A.txt").write_text("You are Role A.", encoding="utf-8")
        (layout["roles"] / "Test_Role_B.txt").write_text("You are Role B.", encoding="utf-8")

        bot = make_bot(make_history())
        bot.current_role = "Test_Role_A"
        patches = patch_bot_dirs(layout)
        enter_all(patches)
        try:
            success, err = asyncio.run(bot.switch_role("Test_Role_B"))
        finally:
            stop_all(patches)

        files = backups_in(layout["chats"])
        transcripts = [f for f in files if f.suffix == ".txt"]
        snapshots = [f for f in files if f.suffix == ".json"]

        check("role switch succeeds", success, str(err))
        check("role switch loads the new role",
              bot.current_role == "Test_Role_B" and len(bot.chat_history) == 1,
              str(bot.chat_history))
        check("role switch writes a backup pair",
              len(transcripts) == 1 and len(snapshots) == 1, str(files))
        check("role switch backup keeps the conversation",
              bool(transcripts) and "hello there" in transcripts[0].read_text(encoding="utf-8"),
              str(transcripts))
        snapshot = json.loads(snapshots[0].read_text(encoding="utf-8")) if snapshots else {}
        check("role switch backup records the reason",
              snapshot.get("backup_reason") == "switch_role:Test_Role_B",
              str(snapshot.get("backup_reason")))
        check("role switch exposes the backup for the reply notice",
              bot.last_backup_path is not None and bot.last_backup_path.suffix == ".txt",
              str(bot.last_backup_path))


def test_load_saved_chat_backs_up_current():
    print("\n.chat load backs up the current conversation")
    with tempfile.TemporaryDirectory() as tmp:
        layout = make_temp_layout(tmp)
        saved = {
            "metadata": {"bot_name": "testbot", "role": "Saved_Role", "mode": "chat"},
            "history": [
                {"role": "system", "content": "saved system", "msg_ids": [], "is_command": False},
                {"role": "user", "content": "saved line", "msg_ids": [], "is_command": False},
            ],
        }
        (layout["sessions"] / "Saved_Chat.json").write_text(json.dumps(saved), encoding="utf-8")

        bot = make_bot(make_history())
        patches = patch_bot_dirs(layout)
        enter_all(patches)
        try:
            loaded = bot.load_session("Saved_Chat")
        finally:
            stop_all(patches)

        transcripts = [f for f in backups_in(layout["chats"]) if f.suffix == ".txt"]
        check("saved chat loads", loaded and len(bot.chat_history) == 2, str(bot.chat_history))
        check("loading a chat backs up the old one",
              len(transcripts) == 1
              and "hello there" in transcripts[0].read_text(encoding="utf-8"),
              str(transcripts))


def test_stale_context_does_not_clobber_session():
    print("\nstartup keeps the richer last session over an empty context file")
    with tempfile.TemporaryDirectory() as tmp:
        layout = make_temp_layout(tmp)
        (layout["roles"] / "Test_Role.txt").write_text("You are Test Role.", encoding="utf-8")
        (layout["personas"] / "Test_Persona.txt").write_text("You are Mikey.", encoding="utf-8")
        # An earlier reset left the context file empty.
        (layout["context"] / "Test_Role.txt").write_text("", encoding="utf-8")

        session = {
            "metadata": {
                "bot_name": "Chat 2", "role": "Test_Role", "mode": "chat",
                "persona": "Test_Persona",
            },
            "history": [
                {"role": "system", "content": "old system prompt", "msg_ids": [], "is_command": False},
                {"role": "user", "content": "first question", "msg_ids": [1], "is_command": False},
                {"role": "assistant", "content": "first answer", "msg_ids": [2], "is_command": False},
                {"role": "user", "content": "second question", "msg_ids": [3], "is_command": False},
                {"role": "assistant", "content": "second answer", "msg_ids": [4], "is_command": False},
            ],
        }
        (layout["sessions"] / "Chat 2_last_session.json").write_text(
            json.dumps(session), encoding="utf-8")

        bot = BotInstance("Chat 2", "token")
        patches = patch_bot_dirs(layout)
        enter_all(patches)
        try:
            bot.init_state(FakeTracker())
        finally:
            stop_all(patches)

        contents = [m.get("content") for m in bot.chat_history]
        healed = (layout["context"] / "Test_Role.txt").read_text(encoding="utf-8")
        saved = json.loads(
            (layout["sessions"] / "Chat 2_last_session.json").read_text(encoding="utf-8"))

        check("startup restores the session conversation",
              len(bot.chat_history) == 5, str(bot.chat_history))
        check("restored history keeps both exchanges",
              "first question" in contents and "second answer" in contents, str(contents))
        check("startup heals the stale context file",
              "second question" in healed and "second answer" in healed, healed)
        check("startup keeps the refreshed system prompt",
              "You are Mikey." in bot.chat_history[0]["content"]
              and "second answer" not in bot.chat_history[0]["content"],
              bot.chat_history[0]["content"])
        check("startup saves the restored session",
              len(saved.get("history", [])) == 5, str(len(saved.get("history", []))))


def main():
    test_chat_clear_backup()
    test_story_clear_backup()
    test_clean_backs_up_and_deletes()
    test_empty_history_skips_backup()
    test_persona_switch_keeps_conversation()
    test_role_switch_backs_up_conversation()
    test_load_saved_chat_backs_up_current()
    test_stale_context_does_not_clobber_session()

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
