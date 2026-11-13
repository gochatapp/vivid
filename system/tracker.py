"""
Bot usage tracker - prevents multiple bots from using the same chat/role/story.
Extracted from vivid.py BotUsageTracker class.
"""

import json
from pathlib import Path
from typing import Dict, Optional

from .config import SETTINGS_DIR


class BotUsageTracker:
    """Tracks active chats/roles and stories across bots so no two bots use the same one."""
    
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.active_chats: Dict[str, str] = {}    # chat_or_role_name -> bot_name
        self.active_stories: Dict[str, str] = {}  # story_name -> bot_name

    def is_chat_in_use(self, chat_name: str, requesting_bot: str) -> bool:
        owner = self.active_chats.get(chat_name)
        return bool(owner and owner != requesting_bot)

    def get_chat_owner(self, chat_name: str) -> Optional[str]:
        return self.active_chats.get(chat_name)

    def claim_chat(self, chat_name: str, bot_name: str) -> bool:
        owner = self.active_chats.get(chat_name)
        if owner and owner != bot_name:
            return False
        # Release any previously claimed chat for this bot
        to_remove = [k for k, v in self.active_chats.items() if v == bot_name]
        for k in to_remove:
            del self.active_chats[k]
        self.active_chats[chat_name] = bot_name
        self.save()
        return True

    def release_chat(self, bot_name: str):
        to_remove = [k for k, v in self.active_chats.items() if v == bot_name]
        for k in to_remove:
            del self.active_chats[k]
        self.save()

    def is_story_in_use(self, story_name: str, requesting_bot: str) -> bool:
        owner = self.active_stories.get(story_name)
        return bool(owner and owner != requesting_bot)

    def get_story_owner(self, story_name: str) -> Optional[str]:
        return self.active_stories.get(story_name)

    def claim_story(self, story_name: str, bot_name: str) -> bool:
        owner = self.active_stories.get(story_name)
        if owner and owner != bot_name:
            return False
        # Release any previously claimed story for this bot
        to_remove = [k for k, v in self.active_stories.items() if v == bot_name]
        for k in to_remove:
            del self.active_stories[k]
        self.active_stories[story_name] = bot_name
        self.save()
        return True

    def release_story(self, bot_name: str):
        to_remove = [k for k, v in self.active_stories.items() if v == bot_name]
        for k in to_remove:
            del self.active_stories[k]
        self.save()

    def release_all(self, bot_name: str):
        self.release_chat(bot_name)
        self.release_story(bot_name)

    def save(self):
        try:
            data = {
                "active_chats": self.active_chats,
                "active_stories": self.active_stories
            }
            temp_file = self.filepath.with_suffix(".tmp")
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            temp_file.replace(self.filepath)
        except Exception as e:
            print(f"⚠️ Failed to save bot tracker: {e}")

    def load(self):
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.active_chats = data.get("active_chats", {})
                    self.active_stories = data.get("active_stories", {})
            except Exception as e:
                print(f"⚠️ Failed to load bot tracker: {e}")
                self.active_chats = {}
                self.active_stories = {}


# Global tracker instance
tracker = BotUsageTracker(SETTINGS_DIR / "bot_assignments.json")