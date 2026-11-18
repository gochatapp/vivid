"""
BotInstance class - encapsulates state, configuration, and handlers for a single Telegram bot.
Extracted from vivid.py BotInstance class.
"""

import asyncio
import time
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, ContextTypes

from .config import (
    BASE_DIR,
    CONFIG,
    CHAT_CONTEXT_DIR,
    CHAT_CONFIG_DIR,
    CHAT_BACKUPS_DIR,
    CHAT_SESSIONS_DIR,
    CHAT_ROLES_DIR,
    CHAT_PERSONAS_DIR,
    STORIES_DIR,
    SETTINGS_DIR,
    CONTEXT_DIR,
    ROLE_IMAGES_DIR,
    CONTEXT_LIMIT,
    TELEGRAM_MESSAGE_LIMIT,
    GENERATION_OPTIONS,
    AI_PROVIDERS,
    providers,
    provider_display_info,
    BOT_INSTANCES,
    BOT_INSTANCES_BY_ID,
    BOT_INSTANCES_BY_TOKEN,
    get_current_default_bot,
    get_bot,
)
from .tracker import tracker
from .content import (
    get_role_content,
    get_persona_content,
    get_story_characters_dir,
    get_story_elements_dir,
    get_character_content,
    get_element_content,
    get_scene_content,
)
from .voices import DEFAULT_SPEED, clamp_speed, default_voice_name


def format_context_text(history: List[Dict[str, Any]]) -> str:
    blocks = []
    for msg in history:
        role = msg.get("role")
        if role in ("user", "assistant") and not msg.get("is_command"):
            content = msg.get("content", "").strip()
            if content:
                blocks.append(f"{role} -----------\n{content}")
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def parse_context_text(text: str) -> List[Dict[str, Any]]:
    pattern = re.compile(r'^(user|assistant)\s*-{3,}\s*$', re.MULTILINE | re.IGNORECASE)
    matches = list(pattern.finditer(text))
    messages = []
    for i, match in enumerate(matches):
        role = match.group(1).lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            messages.append({
                "role": role,
                "content": content,
                "msg_ids": [],
                "is_command": False
            })
    return messages


def get_bot_status_dict(bot: 'BotInstance') -> Dict[str, Any]:
    return {
        "model": bot.current_model,
        "provider": bot.current_provider,
        "role": bot.current_role,
        "mode": bot.current_mode,
        "active_story": bot.active_story,
        "active_characters": bot.active_characters,
        "active_scenes": bot.active_scenes,
        "context_limit": bot.context_limit,
        "response_length": bot.response_length,
        "num_ctx": bot.generation_options.get("num_ctx", 4096),
        "temperature": bot.generation_options.get("temperature", 0.8),
        "top_p": bot.generation_options.get("top_p", 0.9),
        "repeat_penalty": bot.generation_options.get("repeat_penalty", 1.2),
        "repeat_last_n": bot.generation_options.get("repeat_last_n", 64),
        "lazy_mode": bot.lazy_mode,
        "think_mode": bot.think_mode,
        "verbose_mode": bot.verbose_mode,
        "trace_mode": bot.trace_mode,
        "voice_mode": bot.voice_mode,
        "voice_name": bot.voice_name,
        "voice_speed": bot.voice_speed,
        "total_messages_sent": bot.total_messages_sent
    }


def apply_status_dict(bot: 'BotInstance', data: Dict[str, Any]):
    if not isinstance(data, dict):
        return
    if "model" in data:
        bot.current_model = data["model"]
    if "provider" in data and data["provider"] in providers:
        bot.current_provider = data["provider"]
    if "role" in data:
        bot.current_role = data["role"]
    if "mode" in data:
        bot.current_mode = data["mode"]
    if "active_story" in data:
        bot.active_story = data["active_story"]
    if "active_characters" in data and isinstance(data["active_characters"], list):
        bot.active_characters = list(data["active_characters"])
    if "active_scenes" in data and isinstance(data["active_scenes"], list):
        bot.active_scenes = list(data["active_scenes"])
    if "context_limit" in data:
        loaded_limit = int(data["context_limit"])
        # Safeguard: old status files stored message counts (e.g., 50).
        # If value is unreasonably small for tokens, treat as legacy and use default.
        if loaded_limit < 256:
            print(f"⚠️ Legacy context_limit ({loaded_limit}) detected; using default {CONTEXT_LIMIT} tokens")
            bot.context_limit = CONTEXT_LIMIT
        else:
            bot.context_limit = loaded_limit
    if "response_length" in data:
        bot.response_length = int(data["response_length"]) if data["response_length"] is not None else None
    if "num_ctx" in data:
        bot.generation_options["num_ctx"] = int(data["num_ctx"])
    if "temperature" in data:
        bot.generation_options["temperature"] = float(data["temperature"])
    if "top_p" in data:
        bot.generation_options["top_p"] = float(data["top_p"])
    if "repeat_penalty" in data:
        bot.generation_options["repeat_penalty"] = float(data["repeat_penalty"])
    if "repeat_last_n" in data:
        bot.generation_options["repeat_last_n"] = int(data["repeat_last_n"])
    if "lazy_mode" in data:
        bot.lazy_mode = bool(data["lazy_mode"])
    if "think_mode" in data:
        bot.think_mode = bool(data["think_mode"])
    if "verbose_mode" in data:
        bot.verbose_mode = bool(data["verbose_mode"])
    if "trace_mode" in data:
        bot.trace_mode = bool(data["trace_mode"])
    if "voice_mode" in data:
        bot.voice_mode = bool(data["voice_mode"])
    if "voice_name" in data:
        bot.voice_name = str(data["voice_name"])
    if "voice_speed" in data:
        bot.voice_speed = clamp_speed(data["voice_speed"])


class BotInstance:
    """Encapsulates state, configuration, and handlers for a single Telegram bot."""
    
    def __init__(self, name: str, token: str):
        self.name = name
        self.token = token
        self.app: Optional[Application] = None
        self.current_provider = AI_PROVIDERS[0] if AI_PROVIDERS else "none"
        self.current_model = "llama3.2:3b"
        self.cached_models: List[str] = []
        self.current_role = "Assistant"
        self.current_persona = ""
        self.active_characters: List[str] = []
        self.active_scenes: List[str] = []
        self.active_story = ""
        self.current_mode = "chat"
        self.think_mode = False
        self.verbose_mode = False
        self.trace_mode = False
        self.lazy_mode = False
        self.voice_mode = False
        self.voice_name = default_voice_name()
        self.voice_speed = DEFAULT_SPEED
        self.context_limit = CONTEXT_LIMIT
        self.response_length: Optional[int] = None
        self.last_latency = 0.0
        self.last_backup_path: Optional[Path] = None
        self.total_messages_sent = 0
        self.generation_options = {
            "repeat_penalty": 1.2,
            "top_p": 0.9,
            "temperature": 0.8,
            "repeat_last_n": 64,
            "num_ctx": GENERATION_OPTIONS.get("num_ctx", 4096)
        }
        self.chat_history: List[Dict[str, Any]] = []
        self.filtered_models: List[str] = []

    def get_role_context_path(self, role_name: Optional[str] = None) -> Path:
        r = role_name or self.current_role
        return CHAT_CONTEXT_DIR / f"{r}.txt"

    def get_role_status_path(self, role_name: Optional[str] = None) -> Path:
        r = role_name or self.current_role
        return CHAT_CONFIG_DIR / f"{r}.status"

    def get_persona_context_path(self, persona_name: Optional[str] = None) -> Path:
        p = persona_name or self.current_persona
        return CHAT_CONTEXT_DIR / f"persona_{p}.txt"

    def get_persona_status_path(self, persona_name: Optional[str] = None) -> Path:
        p = persona_name or self.current_persona
        return CHAT_CONFIG_DIR / f"persona_{p}.status"

    def get_story_context_path(self, story_name: Optional[str] = None) -> Optional[Path]:
        s = story_name or self.active_story
        if not s:
            return None
        return STORIES_DIR / s / "story_context" / "context.txt"

    def get_story_status_path(self, story_name: Optional[str] = None) -> Optional[Path]:
        s = story_name or self.active_story
        if not s:
            return None
        return STORIES_DIR / s / ".status"

    def get_backup_dir(self) -> Path:
        """Returns the backup folder for the active story, or for chats."""
        if self.current_mode == "story" and self.active_story:
            return STORIES_DIR / self.active_story / "backups"
        return CHAT_BACKUPS_DIR

    def history_message_count(self, history: Optional[List[Dict[str, Any]]] = None) -> int:
        """Counts real conversation turns (user/assistant) in a history list."""
        target = self.chat_history if history is None else history
        return sum(1 for msg in target if msg.get("role") in ("user", "assistant"))

    @staticmethod
    def _history_signature(history: List[Dict[str, Any]]) -> List[Tuple[Any, Any, bool]]:
        """Comparable fingerprint of a history list, used to detect real changes."""
        return [
            (msg.get("role"), msg.get("content"), bool(msg.get("is_command")))
            for msg in history
        ]

    def set_history(self, new_history: List[Dict[str, Any]], *, reason: str = "",
                    backup: bool = True) -> Optional[Path]:
        """Replaces the in-memory history, preserving the old chat in backups.

        Any replacement that would discard a live conversation is backed up
        first, so switching roles, stories, personas, modes, or loading a saved
        chat can never silently destroy messages. Returns the backup transcript
        path when a backup was written.
        """
        new_history = list(new_history or [])
        backup_path = None
        if (backup
                and self.history_message_count() > 0
                and self._history_signature(self.chat_history) != self._history_signature(new_history)):
            backup_path = self.backup_current_history(reason=reason)
        self.chat_history = new_history
        return backup_path

    def backup_current_history(self, reason: str = "") -> Optional[Path]:
        """Saves a timestamped transcript and JSON snapshot of the conversation.

        Chat mode backs up to `chats/backups`; story mode backs up to the active
        story's `backups` folder. Returns the transcript path, or None when
        there is no conversation to back up or the backup could not be written.
        """
        self.last_backup_path = None
        if self.history_message_count() == 0:
            return None

        backup_dir = self.get_backup_dir()
        subject = self.active_story if self.current_mode == "story" and self.active_story else self.current_role
        subject = subject or "chat"

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)

            stamp = time.strftime("%Y%m%d_%H%M%S")
            base_name = f"{subject}_{stamp}"
            counter = 1
            while ((backup_dir / f"{base_name}.txt").exists()
                   or (backup_dir / f"{base_name}.json").exists()):
                counter += 1
                base_name = f"{subject}_{stamp}_{counter}"

            transcript_path = backup_dir / f"{base_name}.txt"
            snapshot_path = backup_dir / f"{base_name}.json"

            snapshot = self.get_session_data()
            if reason:
                snapshot["backup_reason"] = reason

            transcript_path.write_text(format_context_text(self.chat_history), encoding="utf-8")
            with open(snapshot_path, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=4, ensure_ascii=False)

            print(f"💾 Backed up conversation to {transcript_path}")
            self.last_backup_path = transcript_path
            return transcript_path
        except Exception as e:
            print(f"❌ Error backing up conversation to {backup_dir}: {e}")
            return None

    def save_current_context(self):
        if self.current_mode == "chat" and self.current_role:
            ctx_path = self.get_role_context_path()
            text = format_context_text(self.chat_history)
            try:
                temp_path = ctx_path.with_suffix(".tmp")
                temp_path.write_text(text, encoding='utf-8')
                temp_path.replace(ctx_path)
            except Exception as e:
                print(f"❌ Error saving role context to {ctx_path}: {e}")
        elif self.current_mode == "story" and self.active_story:
            ctx_path = self.get_story_context_path()
            if ctx_path:
                ctx_path.parent.mkdir(parents=True, exist_ok=True)
                text = format_context_text(self.chat_history)
                try:
                    temp_path = ctx_path.with_suffix(".tmp")
                    temp_path.write_text(text, encoding='utf-8')
                    temp_path.replace(ctx_path)
                except Exception as e:
                    print(f"❌ Error saving story context to {ctx_path}: {e}")

    def save_current_status(self):
        status_data = get_bot_status_dict(self)
        if self.current_mode == "chat" and self.current_role:
            st_path = self.get_role_status_path()
            try:
                temp_path = st_path.with_suffix(".tmp")
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
                temp_path.replace(st_path)
                dot_status = CHAT_CONFIG_DIR / ".status"
                with open(dot_status, 'w', encoding='utf-8') as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Error saving role status to {st_path}: {e}")
        elif self.current_mode == "story" and self.active_story:
            st_path = self.get_story_status_path()
            if st_path:
                st_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    temp_path = st_path.with_suffix(".tmp")
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        json.dump(status_data, f, indent=4, ensure_ascii=False)
                    temp_path.replace(st_path)
                    cfg_dir = STORIES_DIR / self.active_story / "config"
                    if cfg_dir.exists():
                        cfg_dot_status = cfg_dir / ".status"
                        with open(cfg_dot_status, 'w', encoding='utf-8') as f:
                            json.dump(status_data, f, indent=4, ensure_ascii=False)
                except Exception as e:
                    print(f"❌ Error saving story status to {st_path}: {e}")

    def save_active_context_and_status(self):
        self.save_current_context()
        self.save_current_status()
        self.save_session("last_session")

    def load_role_context_and_status(self, role_name: str, *, backup: bool = True) -> bool:
        st_path = self.get_role_status_path(role_name)
        if not st_path.exists():
            st_path = CHAT_CONFIG_DIR / ".status"
        if st_path.exists():
            try:
                with open(st_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    apply_status_dict(self, data)
            except Exception as e:
                print(f"⚠️ Error reading role status {st_path}: {e}")

        self.current_role = role_name
        self.current_mode = "chat"

        sys_content = get_role_content(role_name)
        system_msg = {"role": "system", "content": sys_content, "msg_ids": [], "is_command": False}

        ctx_path = self.get_role_context_path(role_name)
        loaded_msgs = []
        if ctx_path.exists():
            try:
                text = ctx_path.read_text(encoding='utf-8')
                loaded_msgs = parse_context_text(text)
            except Exception as e:
                print(f"⚠️ Error reading role context {ctx_path}: {e}")

        self.set_history([system_msg] + loaded_msgs, reason=f"role:{role_name}", backup=backup)
        return True

    def build_system_message(self) -> Dict[str, Any]:
        """Builds the system prompt for the current role plus active persona."""
        sys_content = get_role_content(self.current_role)
        if self.current_persona:
            persona_content = get_persona_content(self.current_persona)
            if persona_content:
                sys_content += f"\n\nUSER PERSONA:\n{persona_content}"
        return {"role": "system", "content": sys_content, "msg_ids": [], "is_command": False}

    def refresh_system_prompt(self):
        """Updates the system prompt without touching the conversation history."""
        system_msg = self.build_system_message()
        if self.chat_history and self.chat_history[0].get("role") == "system":
            self.chat_history[0] = system_msg
        else:
            self.chat_history.insert(0, system_msg)

    def load_persona_context_and_status(self, persona_name: str, *, backup: bool = True) -> bool:
        """Applies persona status and overlays the persona on the system prompt.

        A persona is a *user* overlay, so switching personas must never replace
        the role's conversation. Persona context is only used as a fallback when
        there is no live conversation in memory.
        """
        if not persona_name:
            self.current_persona = ""
            self.refresh_system_prompt()
            return True

        st_path = self.get_persona_status_path(persona_name)
        if st_path.exists():
            try:
                with open(st_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    apply_status_dict(self, data)
            except Exception as e:
                print(f"⚠️ Error reading persona status {st_path}: {e}")

        self.current_persona = persona_name
        system_msg = self.build_system_message()

        if self.history_message_count() == 0:
            ctx_path = self.get_persona_context_path(persona_name)
            loaded_msgs = []
            if ctx_path.exists():
                try:
                    text = ctx_path.read_text(encoding='utf-8')
                    loaded_msgs = parse_context_text(text)
                except Exception as e:
                    print(f"⚠️ Error reading persona context {ctx_path}: {e}")
            self.set_history([system_msg] + loaded_msgs,
                             reason=f"persona:{persona_name}", backup=backup)
        else:
            self.refresh_system_prompt()
        return True

    def load_story_context_and_status(self, story_name: str, *, backup: bool = True) -> bool:
        st_path = self.get_story_status_path(story_name)
        if st_path and st_path.exists():
            try:
                with open(st_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    apply_status_dict(self, data)
            except Exception as e:
                print(f"⚠️ Error reading story status {st_path}: {e}")
        else:
            cfg_st = STORIES_DIR / story_name / "config" / ".status"
            if cfg_st.exists():
                try:
                    with open(cfg_st, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        apply_status_dict(self, data)
                except Exception as e:
                    print(f"⚠️ Error reading story status {cfg_st}: {e}")

        self.active_story = story_name
        self.current_mode = "story"

        ctx_path = self.get_story_context_path(story_name)
        loaded_msgs = []
        backstory = ""
        if ctx_path and ctx_path.exists():
            try:
                text = ctx_path.read_text(encoding='utf-8')
                loaded_msgs = parse_context_text(text)
                pattern = re.compile(r'^(user|assistant)\s*-{3,}\s*$', re.MULTILINE | re.IGNORECASE)
                m = pattern.search(text)
                if m:
                    backstory = text[:m.start()].strip()
                elif not loaded_msgs:
                    backstory = text.strip()
            except Exception as e:
                print(f"⚠️ Error reading story context {ctx_path}: {e}")

        role_content = get_role_content(self.current_role)
        if backstory:
            role_content += f"\n\nSTORY BACKSTORY / PREMISE:\n{backstory}"
        if self.current_persona:
            persona_content = get_persona_content(self.current_persona)
            if persona_content:
                role_content += f"\n\nUSER PERSONA:\n{persona_content}"
        system_msg = {"role": "system", "content": role_content, "msg_ids": [], "is_command": False}
        self.set_history([system_msg] + loaded_msgs, reason=f"story:{story_name}", backup=backup)
        return True

    async def switch_role(self, new_role: str) -> Tuple[bool, str]:
        if tracker.is_chat_in_use(new_role, self.name):
            owner = tracker.get_chat_owner(new_role)
            return False, f"Role `{new_role}` is currently in use by bot `{owner}`. No two bots can use the same role or chat."

        self.save_current_context()
        self.save_current_status()
        self.last_backup_path = None

        # Keep the outgoing conversation safe before the role changes state.
        same_subject = new_role == self.current_role and self.current_mode == "chat"
        if not same_subject:
            self.backup_current_history(reason=f"switch_role:{new_role}")

        tracker.claim_chat(new_role, self.name)
        self.current_role = new_role

        self.load_role_context_and_status(new_role, backup=same_subject)
        if self.current_persona:
            self.load_persona_context_and_status(self.current_persona, backup=False)
        self.save_session("last_session")
        return True, ""

    async def switch_persona(self, new_persona: str) -> Tuple[bool, str]:
        self.save_current_context()
        self.save_current_status()
        self.last_backup_path = None

        self.current_persona = new_persona

        self.load_persona_context_and_status(new_persona)
        self.save_session("last_session")
        return True, ""

    async def switch_story(self, new_story: str) -> Tuple[bool, str]:
        if tracker.is_story_in_use(new_story, self.name):
            owner = tracker.get_story_owner(new_story)
            return False, f"Story `{new_story}` is currently in use by bot `{owner}`. No two bots can use the same story."

        self.save_current_context()
        self.save_current_status()
        self.last_backup_path = None

        # Keep the outgoing conversation safe before the story changes state.
        same_subject = new_story == self.active_story and self.current_mode == "story"
        if not same_subject:
            self.backup_current_history(reason=f"switch_story:{new_story}")

        tracker.claim_story(new_story, self.name)
        self.active_story = new_story
        self.active_characters = []
        self.active_scenes = []

        self.load_story_context_and_status(new_story, backup=same_subject)
        self.save_session("last_session")
        return True, ""
    
    def get_session_data(self) -> Dict[str, Any]:
        """Builds the metadata/history payload used for session saves and backups."""
        return {
            "metadata": {
                "bot_name": self.name,
                "model": self.current_model,
                "role": self.current_role,
                "persona": self.current_persona,
                "provider": self.current_provider,
                "context_limit": self.context_limit,
                "response_length": self.response_length,
                "mode": self.current_mode,
                "active_characters": self.active_characters,
                "active_scenes": self.active_scenes,
                "active_story": self.active_story,
                "num_ctx": self.generation_options.get("num_ctx", 4096),
                "lazy_mode": self.lazy_mode,
                "think_mode": self.think_mode,
                "verbose_mode": self.verbose_mode,
                "trace_mode": self.trace_mode,
                "voice_mode": self.voice_mode,
                "voice_name": self.voice_name,
                "voice_speed": self.voice_speed
            },
            "history": self.chat_history
        }

    def save_session(self, name: str = "last_session"):
        filename = f"{self.name}_{name}.json" if name == "last_session" else f"{name}.json"
        file_path = CHAT_SESSIONS_DIR / filename
        data = self.get_session_data()
        try:
            temp_path = file_path.with_suffix(".tmp")
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            temp_path.replace(file_path)
        except Exception as e:
            print(f"❌ Error saving session {file_path}: {e}")

    def load_session(self, name: str = "last_session", *, backup: bool = True) -> bool:
        self.last_backup_path = None
        clean_name = name.replace(".json", "")
        file_path = (CHAT_SESSIONS_DIR / f"{self.name}_{clean_name}.json").resolve()
        if not file_path.exists():
            file_path = (CHAT_SESSIONS_DIR / f"{clean_name}.json").resolve()

        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and "history" in data:
                    self.set_history(data["history"], reason=f"session:{clean_name}", backup=backup)
                    meta = data.get("metadata", {})
                    self.current_model = meta.get("model", self.current_model)
                    self.current_role = meta.get("role", self.current_role)
                    self.current_persona = meta.get("persona", self.current_persona)
                    self.current_provider = meta.get("provider", self.current_provider)
                    self.context_limit = meta.get("context_limit", self.context_limit)
                    self.response_length = meta.get("response_length", None)
                    self.current_mode = meta.get("mode", "chat")
                    self.active_characters = meta.get("active_characters", [])
                    self.active_scenes = meta.get("active_scenes", [])
                    self.active_story = meta.get("active_story", "")
                    self.lazy_mode = meta.get("lazy_mode", False)
                    self.think_mode = meta.get("think_mode", False)
                    self.verbose_mode = meta.get("verbose_mode", False)
                    self.trace_mode = meta.get("trace_mode", False)
                    self.voice_mode = meta.get("voice_mode", False)
                    self.voice_name = meta.get("voice_name", self.voice_name)
                    if "voice_speed" in meta:
                        self.voice_speed = clamp_speed(meta["voice_speed"])
                    if "num_ctx" in meta:
                        self.generation_options["num_ctx"] = meta["num_ctx"]
                else:
                    self.set_history(data, reason=f"session:{clean_name}", backup=backup)
                return True
            except Exception as e:
                print(f"❌ Error loading session {file_path}: {e}")
                return False
        return False

    def _prefer_session_history(self, session_history: List[Dict[str, Any]], session_loaded: bool,
                                session_mode: str, session_role: str, session_story: str) -> bool:
        """Keeps a richer saved session from being clobbered by a stale context file.

        Startup used to reload role/story context over the freshly loaded session.
        When those context files were empty or older than the session, the live
        conversation was silently discarded. The session is the newest snapshot,
        so it wins whenever it belongs to the same subject and holds more turns.
        """
        if not session_loaded or self.history_message_count(session_history) == 0:
            return False

        same_subject = (
            self.current_mode == "chat"
            and session_mode == "chat"
            and session_role == self.current_role
        ) or (
            self.current_mode == "story"
            and session_mode == "story"
            and bool(session_story)
            and session_story == self.active_story
        )
        if not same_subject:
            return False
        if self.history_message_count(session_history) <= self.history_message_count():
            return False

        # Preserve the (smaller) context conversation before preferring the session.
        self.backup_current_history(reason="startup_context")

        # Keep the freshly built system prompt (role + persona + story premise) and
        # graft the session's conversation onto it, so prompt fixes survive restarts.
        restored = list(session_history)
        if self.chat_history and self.chat_history[0].get("role") == "system":
            fresh_system = self.chat_history[0]
            if restored and restored[0].get("role") == "system":
                restored[0] = fresh_system
            else:
                restored.insert(0, fresh_system)
        self.chat_history = restored
        self.save_current_context()
        print(f"♻️ [{self.name}] Restored {self.history_message_count()} messages from the last session "
              f"(context file was stale).")
        return True

    def init_state(self, tracker_instance):
        loaded = self.load_session("last_session")
        session_history = list(self.chat_history)
        session_mode = self.current_mode
        session_role = self.current_role
        session_story = self.active_story

        if not loaded:
            self.set_history(
                [{"role": "system", "content": get_role_content(self.current_role),
                  "msg_ids": [], "is_command": False}],
                reason="startup", backup=False
            )

        if self.current_mode == "story" and self.active_story:
            if tracker_instance.is_story_in_use(self.active_story, self.name):
                available_stories = [d.name for d in sorted(STORIES_DIR.iterdir()) if d.is_dir() and not tracker_instance.is_story_in_use(d.name, self.name)]
                if available_stories:
                    self.active_story = available_stories[0]
                    print(f"[{self.name}] Preferred story in use. Auto-assigned to '{self.active_story}'")
                else:
                    self.current_mode = "chat"
                    self.active_story = ""
            if self.active_story:
                tracker_instance.claim_story(self.active_story, self.name)
                same_story = session_mode == "story" and session_story == self.active_story
                self.load_story_context_and_status(self.active_story, backup=not same_story)
                self._prefer_session_history(session_history, loaded, session_mode,
                                             session_role, session_story)

        if self.current_mode == "chat":
            if tracker_instance.is_chat_in_use(self.current_role, self.name):
                role_files = sorted(list(CHAT_ROLES_DIR.glob("*.txt")))
                assigned = False
                for rf in role_files:
                    if not tracker_instance.is_chat_in_use(rf.stem, self.name):
                        print(f"[{self.name}] Preferred role '{self.current_role}' in use. Auto-assigned to '{rf.stem}'")
                        self.current_role = rf.stem
                        assigned = True
                        break
                if not assigned:
                    self.current_role = f"Assistant_{self.name}"
            tracker_instance.claim_chat(self.current_role, self.name)
            same_role = session_mode == "chat" and session_role == self.current_role
            self.load_role_context_and_status(self.current_role, backup=not same_role)
            if self.current_persona:
                self.load_persona_context_and_status(self.current_persona, backup=False)
            self._prefer_session_history(session_history, loaded, session_mode,
                                         session_role, session_story)

        # Keep the session in sync with the resolved startup state.
        self.save_session("last_session")

# Utility functions (originally standalone in vivid.py, now module-level in bot_instance)

def save_to_json(name: str, bot: Optional[BotInstance] = None) -> None:
    """Saves the current session history to JSON."""
    if bot is None:
        bot = get_current_default_bot()
    bot.save_session(name)


def load_from_json(name: str, bot: Optional[BotInstance] = None) -> bool:
    """Loads a session history from JSON."""
    if bot is None:
        bot = get_current_default_bot()
    return bot.load_session(name)


async def generate_response(messages: List[Dict[str, Any]], options: Optional[Dict[str, Any]] = None, bot: Optional[BotInstance] = None) -> str:
    """Centralized helper to call the appropriate LLM provider."""
    if bot is None:
        bot = get_current_default_bot()

    if options is None:
        options = bot.generation_options

    provider = providers.get(bot.current_provider)
    if not provider:
        raise ValueError(f"Provider '{bot.current_provider}' not found")

    call_opts = {**options}
    if bot.think_mode:
        call_opts["think"] = True

    try:
        response = await provider.chat(
            model=bot.current_model,
            messages=messages,
            options=call_opts
        )
        return response
    except Exception as e:
        return f"Error generating response: {e}"


def split_telegram_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    """Split *text* into chunks no longer than *limit* characters.

    Splits on line boundaries where possible and hard-splits any single line
    that is longer than *limit*. Line endings are kept, so joining the returned
    chunks reproduces the original text exactly. Always returns at least one
    chunk (an empty string for empty input).
    """
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        # Hard-split a single overlong line so every piece fits in a chunk.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]

        if len(current) + len(line) <= limit:
            current += line
        else:
            if current:
                chunks.append(current)
            current = line

    if current or not chunks:
        chunks.append(current)
    return chunks


# Characters the MarkdownV2 parser reserves. Every one of them has to be
# escaped with a preceding backslash or Telegram rejects the whole message
# with "BadRequest: Can't parse entities".
MARKDOWN_V2_RESERVED = frozenset('_*[]()~`>#+-=|{}.!')
_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")


def escape_markdown_v2(text: str) -> str:
    """Escapes every MarkdownV2 reserved character so *text* is shown literally."""
    if not text:
        return text
    return "".join(
        f"\\{char}" if char in MARKDOWN_V2_RESERVED or char == "\\" else char
        for char in text
    )


def _find_occurrences(text: str, needle: str) -> List[int]:
    """Returns every (non-overlapping) start index of *needle* in *text*."""
    positions: List[int] = []
    start = text.find(needle)
    while start != -1:
        positions.append(start)
        start = text.find(needle, start + len(needle))
    return positions


def _convert_markdown_line(line: str, stash) -> str:
    """Escapes one line, keeping code spans, existing escapes and bold spans."""
    if not line:
        return line

    body_parts: List[str] = []
    index = 0
    length = len(line)
    while index < length:
        char = line[index]
        if char == "\\" and index + 1 < length:
            following = line[index + 1]
            if following in MARKDOWN_V2_RESERVED or following == "\\":
                body_parts.append(stash("\\" + following))
            else:
                # A literal backslash that is not an escape sequence.
                body_parts.append(stash("\\\\"))
                body_parts.append(stash(following))
            index += 2
            continue
        if char == "`":
            closing = line.find("`", index + 1)
            if closing == -1:
                body_parts.append(stash("\\`"))
                index += 1
                continue
            code = line[index + 1:closing]
            code = code.replace("\\", "\\\\").replace("`", "\\`")
            body_parts.append(stash(f"`{code}`"))
            index = closing + 1
            continue
        body_parts.append(char)
        index += 1

    body = "".join(body_parts)

    # Prefer **bold** spans; otherwise fall back to the *bold* style used in
    # the help text. Unpaired asterisks stay literal so nothing is swallowed.
    doubles = _find_occurrences(body, "**")
    if doubles and len(doubles) % 2 == 0:
        marker_length = 2
        positions = doubles
    elif not doubles:
        marker_length = 1
        positions = _find_occurrences(body, "*")
        if len(positions) % 2 != 0:
            positions = []
    else:
        marker_length = 1
        positions = []

    replacements = {}
    skipped = set()
    for open_pos, close_pos in zip(positions[0::2], positions[1::2]):
        if close_pos - open_pos <= marker_length:
            continue  # Empty span such as "****" - keep the asterisks literal.
        replacements[open_pos] = "*"
        replacements[close_pos] = "*"
        skipped.update(range(open_pos, open_pos + marker_length))
        skipped.update(range(close_pos, close_pos + marker_length))

    converted: List[str] = []
    for position, char in enumerate(body):
        if position in replacements:
            converted.append("*")
        elif position in skipped:
            continue
        elif char in MARKDOWN_V2_RESERVED:
            converted.append("\\" + char)
        else:
            converted.append(char)
    return "".join(converted)


def markdown_to_v2(text: str) -> str:
    """Converts the light Markdown used by command replies into valid MarkdownV2.

    ``**bold**`` (and ``*bold*``) become MarkdownV2 bold, backtick spans stay
    code spans, already-escaped characters are preserved and every other
    reserved character is escaped. Handlers can therefore keep writing
    ``**bold**`` and plain punctuation without Telegram answering with
    ``BadRequest: Can't parse entities``.
    """
    if not text:
        return text

    placeholders: List[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return f"\x00{len(placeholders) - 1}\x00"

    converted = "\n".join(
        _convert_markdown_line(line, stash) for line in text.split("\n")
    )
    return _PLACEHOLDER_RE.sub(lambda match: placeholders[int(match.group(1))], converted)


async def send_with_fallback(send, text: str, parse_mode: Optional[str] = "MarkdownV2"):
    """Sends *text* via *send*, retrying as plain text if the markup is invalid.

    ``send`` must be an async callable accepting ``text`` and ``parse_mode``
    keyword arguments (e.g. ``update.message.reply_text``).
    """
    try:
        return await send(text, parse_mode=parse_mode)
    except BadRequest as error:
        reason = str(error).lower()
        if "entit" not in reason and "parse" not in reason:
            raise
        print(f"⚠️ Telegram rejected {parse_mode} markup, sending plain text: {error}")
        return await send(text, parse_mode=None)


async def reply_and_log(update: Update, text: str, is_command: bool = True, bot: Optional[BotInstance] = None) -> None:
    """Helper to send a message, capture IDs, and log to history for session persistence.

    *text* may use the same light Markdown as the handlers (``**bold**``,
    ``*bold*``, backtick code spans); it is converted to valid MarkdownV2
    before sending so Telegram never answers with
    ``BadRequest: Can't parse entities``.

    Messages longer than ``TELEGRAM_MESSAGE_LIMIT`` are split into multiple
    Telegram messages so the API's "Message is too long" error is avoided.
    """
    if bot is None:
        bot = get_bot(update=update)

    user_msg_id = update.message.message_id

    # Ensure the user's command is logged if it hasn't been already
    already_logged = any(user_msg_id in m.get("msg_ids", []) for m in bot.chat_history[-2:])
    if not already_logged and bot.chat_history and bot.chat_history[-1]["role"] == "user":
        if user_msg_id not in bot.chat_history[-1].get("msg_ids", []):
            bot.chat_history[-1].setdefault("msg_ids", []).append(user_msg_id)

    chunks = split_telegram_message(markdown_to_v2(text))
    sent_msg_ids: List[int] = []
    chat_id = update.effective_chat.id if update.effective_chat else None
    app = getattr(bot, "app", None)

    # Send the first chunk as a reply to the triggering message so the normal
    # reply thread is preserved; send any extra chunks as plain messages.
    for index, chunk in enumerate(chunks):
        if index == 0 or app is None or chat_id is None:
            send = update.message.reply_text
        else:
            async def send(text, parse_mode=None, _chat_id=chat_id):
                return await app.bot.send_message(
                    chat_id=_chat_id, text=text, parse_mode=parse_mode
                )
        sent_msg = await send_with_fallback(send, chunk, "MarkdownV2")
        sent_msg_ids.append(sent_msg.message_id)
    
    if is_command:
        if bot.chat_history and bot.chat_history[-1]["role"] == "assistant":
            bot.chat_history[-1].setdefault("msg_ids", []).extend(sent_msg_ids)
    
    bot.save_active_context_and_status()


async def reply_transient(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, delay: int = 5) -> None:
    """Sends a message that deletes itself after a delay and is NOT logged to history."""
    try:
        # Physically remove the trigger command from the Telegram screen for a clean UI
        try: await update.message.delete()
        except: pass

        sent_msg = await send_with_fallback(
            update.message.reply_text, markdown_to_v2(text), "MarkdownV2"
        )
        async def _delete():
            await asyncio.sleep(delay)
            try: await sent_msg.delete()
            except: pass
        asyncio.create_task(_delete())
    except Exception as e:
        print(f"⚠️ reply_transient failed: {e}")