#!/usr/bin/env python3
"""
Test suite for multi-bot architecture, context formatting, tracker locking, and .status persistence.
"""

import sys
import os
import json
import asyncio
import tempfile
import shutil
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from vivid import (
    format_context_text,
    parse_context_text,
    get_bot_status_dict,
    apply_status_dict,
    BotUsageTracker,
    BotInstance,
    CHAT_CONTEXT_DIR,
    CHAT_CONFIG_DIR,
    CHAT_ROLES_DIR,
    STORIES_DIR,
    SETTINGS_DIR,
)

SAMPLE_USER_ASSISTANT_TEXT = """user -----------
hi clara what is it you do

assistant -----------
I smooth a drafting pencil between my fingers and meet your gaze, the skyline stretching behind me. "I'm an architect," I say, voice precise but not cold. "I design spaces that solve problems and, when I'm lucky, make people feel something they can't quite name. My studio handles everything from urban planning to private residences—structure, light, the quiet language of materials. It's demanding, exacting work, but I love giving a building a pulse." I tilt my head, studying you with hazel eyes. "Cities are just stories waiting to be occupied, and I like making sure the next chapter stands up."

user -----------
hey you there

assistant -----------
I glance up from the drafting table, pencil still poised between my fingers. My hazel eyes settle on you with quiet recognition.

"Hello," I say, setting the pencil down. "I'm here. What can I help you with?"

The studio around me hums softly—ventilation, the distant whir of the model printer. I fold my hands loosely on the table, giving you my full attention without crowding the moment. There's something in your open, friendly posture that invites a matching ease.

"Are you here about a project," I ask, "or just passing through?"
"""

def test_context_format_and_parse():
    print("\n--- Test 1: Context Formatting and Parsing ---")
    messages = parse_context_text(SAMPLE_USER_ASSISTANT_TEXT)
    assert len(messages) == 4, f"Expected 4 messages, got {len(messages)}"
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hi clara what is it you do"
    assert messages[1]["role"] == "assistant"
    assert "I'm an architect" in messages[1]["content"]
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "hey you there"
    assert messages[3]["role"] == "assistant"
    assert "I glance up from the drafting table" in messages[3]["content"]

    # Re-format and check round-trip match
    reformatted = format_context_text(messages)
    assert reformatted.strip() == SAMPLE_USER_ASSISTANT_TEXT.strip(), "Round-trip formatting mismatch!"
    
    # Test filtering of command and system messages
    mixed_history = [
        {"role": "system", "content": "You are Clara", "msg_ids": [], "is_command": False},
        {"role": "user", "content": ".role 1", "msg_ids": [], "is_command": True},
        {"role": "user", "content": "Hello Clara", "msg_ids": [], "is_command": False},
        {"role": "assistant", "content": "Hello there!", "msg_ids": [], "is_command": False},
    ]
    formatted_mixed = format_context_text(mixed_history)
    parsed_mixed = parse_context_text(formatted_mixed)
    assert len(parsed_mixed) == 2, f"Expected 2 non-command messages, got {len(parsed_mixed)}"
    assert parsed_mixed[0]["content"] == "Hello Clara"
    assert parsed_mixed[1]["content"] == "Hello there!"
    print("✅ Context formatting & parsing passed!")


def test_usage_tracker():
    print("\n--- Test 2: Bot Usage Tracker Locking ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker_file = Path(tmpdir) / "assignments.json"
        t = BotUsageTracker(tracker_file)

        # Bot 1 claims chat Clara
        assert t.claim_chat("Clara", "bot1") is True
        assert t.is_chat_in_use("Clara", "bot1") is False  # bot1 is the owner
        assert t.is_chat_in_use("Clara", "bot2") is True   # in use for bot2
        assert t.get_chat_owner("Clara") == "bot1"

        # Bot 2 tries to claim chat Clara -> blocked
        assert t.claim_chat("Clara", "bot2") is False

        # Bot 1 switches to chat Elena -> Clara should be released
        assert t.claim_chat("Elena", "bot1") is True
        assert t.is_chat_in_use("Clara", "bot2") is False
        assert t.is_chat_in_use("Elena", "bot2") is True

        # Now Bot 2 can claim Clara
        assert t.claim_chat("Clara", "bot2") is True
        assert t.get_chat_owner("Clara") == "bot2"

        # Story tracking
        assert t.claim_story("CyberpunkCity", "bot1") is True
        assert t.is_story_in_use("CyberpunkCity", "bot2") is True
        assert t.claim_story("CyberpunkCity", "bot2") is False

        # Release all for bot1
        t.release_all("bot1")
        assert t.is_chat_in_use("Elena", "bot2") is False
        assert t.is_story_in_use("CyberpunkCity", "bot2") is False

        # Persistence check
        t.claim_chat("Julianna", "bot1")
        t.claim_story("HauntedManor", "bot2")
        t2 = BotUsageTracker(tracker_file)
        t2.load()
        assert t2.get_chat_owner("Julianna") == "bot1"
        assert t2.get_story_owner("HauntedManor") == "bot2"

    print("✅ BotUsageTracker locking and persistence passed!")


async def test_bot_instance_role_and_story_switching():
    print("\n--- Test 3: BotInstance Role & Story Switching with Context and .status ---")
    # Setup test role files
    role_a = "Test_Architect_Clara"
    role_b = "Test_Detective_Sarah"
    role_a_file = CHAT_ROLES_DIR / f"{role_a}.txt"
    role_b_file = CHAT_ROLES_DIR / f"{role_b}.txt"

    story_a = "Test_Story_Alpha"
    story_b = "Test_Story_Beta"
    story_a_dir = STORIES_DIR / story_a
    story_b_dir = STORIES_DIR / story_b

    try:
        role_a_file.write_text("You are Clara, an architect.", encoding='utf-8')
        role_b_file.write_text("You are Sarah, a detective.", encoding='utf-8')
        story_a_dir.mkdir(parents=True, exist_ok=True)
        story_b_dir.mkdir(parents=True, exist_ok=True)

        bot1 = BotInstance("testbot1", "fake-token-1")
        bot2 = BotInstance("testbot2", "fake-token-2")

        # Bot 1 switches to role A
        success, err = await bot1.switch_role(role_a)
        assert success is True, f"Failed switch to role_a: {err}"
        assert bot1.current_role == role_a

        # Bot 2 tries to switch to role A -> should fail
        success, err = await bot2.switch_role(role_a)
        assert success is False
        assert "is currently in use by bot `testbot1`" in err

        # Bot 1 has conversation in role A
        bot1.chat_history.append({"role": "user", "content": "hi clara what is it you do", "msg_ids": [], "is_command": False})
        bot1.chat_history.append({"role": "assistant", "content": "I am an architect.", "msg_ids": [], "is_command": False})
        bot1.generation_options["temperature"] = 0.65
        bot1.current_model = "test-model-custom"

        # Bot 1 switches to role B
        success, err = await bot1.switch_role(role_b)
        assert success is True
        assert bot1.current_role == role_b

        # Verify role A context was saved to chat_context/Test_Architect_Clara.txt
        ctx_a_path = CHAT_CONTEXT_DIR / f"{role_a}.txt"
        assert ctx_a_path.exists(), f"Expected {ctx_a_path} to exist"
        content = ctx_a_path.read_text(encoding='utf-8')
        assert "user -----------" in content
        assert "hi clara what is it you do" in content
        assert "assistant -----------" in content
        assert "I am an architect." in content

        # Verify role A .status was saved to config/Test_Architect_Clara.status
        st_a_path = CHAT_CONFIG_DIR / f"{role_a}.status"
        assert st_a_path.exists(), f"Expected {st_a_path} to exist"
        status_data = json.loads(st_a_path.read_text(encoding='utf-8'))
        assert status_data["role"] == role_a
        assert status_data["model"] == "test-model-custom"
        assert status_data["temperature"] == 0.65

        # Now Bot 2 switches to role A! It should successfully reload role A's context and status
        success, err = await bot2.switch_role(role_a)
        assert success is True, f"Bot 2 failed to switch to released role_a: {err}"
        assert bot2.current_role == role_a
        assert bot2.current_model == "test-model-custom"
        assert bot2.generation_options["temperature"] == 0.65
        # Verify history reloaded
        assert len(bot2.chat_history) == 3  # system + user + assistant
        assert bot2.chat_history[1]["content"] == "hi clara what is it you do"
        assert bot2.chat_history[2]["content"] == "I am an architect."

        # Story mode switching test
        bot1.current_mode = "story"
        bot2.current_mode = "story"

        success, err = await bot1.switch_story(story_a)
        assert success is True
        assert bot1.active_story == story_a

        # Bot 2 tries to switch to story A -> should fail
        success, err = await bot2.switch_story(story_a)
        assert success is False
        assert "is currently in use by bot `testbot1`" in err

        # Bot 1 has conversation in story A
        bot1.chat_history.append({"role": "user", "content": "What is happening in the city?", "msg_ids": [], "is_command": False})
        bot1.chat_history.append({"role": "assistant", "content": "The rain falls heavily on the neon streets.", "msg_ids": [], "is_command": False})

        # Bot 1 switches to story B
        success, err = await bot1.switch_story(story_b)
        assert success is True

        # Verify story A context was saved under stories/Test_Story_Alpha/story_context/context.txt
        story_ctx = story_a_dir / "story_context" / "context.txt"
        assert story_ctx.exists(), f"Expected {story_ctx} to exist"
        st_text = story_ctx.read_text(encoding='utf-8')
        assert "user -----------" in st_text
        assert "What is happening in the city?" in st_text
        assert "The rain falls heavily on the neon streets." in st_text

        # Verify story A .status was saved under stories/Test_Story_Alpha/.status
        story_st = story_a_dir / ".status"
        assert story_st.exists(), f"Expected {story_st} to exist"
        st_json = json.loads(story_st.read_text(encoding='utf-8'))
        assert st_json["active_story"] == story_a

        # Bot 2 switches to story A and reloads context
        success, err = await bot2.switch_story(story_a)
        assert success is True
        assert bot2.active_story == story_a
        assert len(bot2.chat_history) == 3
        assert bot2.chat_history[1]["content"] == "What is happening in the city?"
        assert bot2.chat_history[2]["content"] == "The rain falls heavily on the neon streets."

        print("✅ BotInstance role/story switching, context reload, and .status persistence passed!")

    finally:
        # Cleanup test artifacts
        if role_a_file.exists(): role_a_file.unlink()
        if role_b_file.exists(): role_b_file.unlink()
        (CHAT_CONTEXT_DIR / f"{role_a}.txt").unlink(missing_ok=True)
        (CHAT_CONTEXT_DIR / f"{role_b}.txt").unlink(missing_ok=True)
        (CHAT_CONFIG_DIR / f"{role_a}.status").unlink(missing_ok=True)
        (CHAT_CONFIG_DIR / f"{role_b}.status").unlink(missing_ok=True)
        if story_a_dir.exists(): shutil.rmtree(story_a_dir, ignore_errors=True)
        if story_b_dir.exists(): shutil.rmtree(story_b_dir, ignore_errors=True)
        (SETTINGS_DIR / "bot_assignments.json").unlink(missing_ok=True)


def test_status_dict_serialization():
    print("\n--- Test 4: Status Dict Serialization and Application ---")
    bot = BotInstance("bot1", "token1")
    bot.current_model = "custom-model:latest"
    bot.current_role = "Doctor"
    bot.current_mode = "story"
    bot.active_story = "HospitalDrama"
    bot.active_characters = ["Dr. Smith", "Nurse Kelly"]
    bot.active_scenes = ["Emergency Room"]
    bot.generation_options["temperature"] = 0.5
    bot.generation_options["num_ctx"] = 8192
    bot.lazy_mode = True
    bot.think_mode = True
    bot.verbose_mode = True
    bot.trace_mode = True

    status_data = get_bot_status_dict(bot)
    assert status_data["model"] == "custom-model:latest"
    assert status_data["role"] == "Doctor"
    assert status_data["mode"] == "story"
    assert status_data["active_story"] == "HospitalDrama"
    assert status_data["active_characters"] == ["Dr. Smith", "Nurse Kelly"]
    assert status_data["temperature"] == 0.5
    assert status_data["num_ctx"] == 8192
    assert status_data["lazy_mode"] is True
    assert status_data["think_mode"] is True

    # Apply to a fresh bot
    bot2 = BotInstance("bot2", "token2")
    apply_status_dict(bot2, status_data)
    assert bot2.current_model == "custom-model:latest"
    assert bot2.current_role == "Doctor"
    assert bot2.current_mode == "story"
    assert bot2.active_story == "HospitalDrama"
    assert bot2.active_characters == ["Dr. Smith", "Nurse Kelly"]
    assert bot2.generation_options["temperature"] == 0.5
    assert bot2.generation_options["num_ctx"] == 8192
    assert bot2.lazy_mode is True
    assert bot2.think_mode is True
    print("✅ Status dict serialization & application passed!")


def test_init_state_conflict_resolution():
    print("\n--- Test 5: Bot init_state Startup Conflict Resolution ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        tracker_file = Path(tmpdir) / "assignments.json"
        t = BotUsageTracker(tracker_file)

        # Bot 1 starts with default Assistant
        bot1 = BotInstance("bot1", "token1")
        bot1.init_state(t)
        assert bot1.current_role != ""
        role1 = bot1.current_role
        assert t.is_chat_in_use(role1, "bot1") is False
        assert t.is_chat_in_use(role1, "bot2") is True

        # Bot 2 starts with same initial role preference
        bot2 = BotInstance("bot2", "token2")
        bot2.current_role = role1
        bot2.init_state(t)
        # Bot 2 must have been auto-assigned to a different role!
        assert bot2.current_role != role1
        assert t.get_chat_owner(bot2.current_role) == "bot2"
        assert t.get_chat_owner(role1) == "bot1"
        print(f"✅ Bot 1 got '{role1}' and Bot 2 automatically resolved to '{bot2.current_role}'!")


async def main_async():
    test_context_format_and_parse()
    test_usage_tracker()
    test_status_dict_serialization()
    test_init_state_conflict_resolution()
    await test_bot_instance_role_and_story_switching()
    print("\n🎉 ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(main_async())
