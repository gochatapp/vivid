"""
BotInstance class - encapsulates state, configuration, and handlers for a single Telegram bot.
Extracted from vivid.py BotInstance class.
"""

import time
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from telegram import Update
from telegram.ext import Application, ContextTypes

from .config import (
    BASE_DIR,
    CHAT_CONTEXT_DIR,
    CHAT_CONFIG_DIR,
    CHAT_SESSIONS_DIR,
    CHAT_ROLES_DIR,
    CHAT_PERSONAS_DIR,
    STORIES_DIR,
    SETTINGS_DIR,
    CONTEXT_DIR,
    ROLE_IMAGES_DIR,
    CONTEXT_LIMIT,
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
        "num_ctx": bot.generation_options.get("num_ctx", 4096),
        "temperature": bot.generation_options.get("temperature", 0.8),
        "top_p": bot.generation_options.get("top_p", 0.9),
        "repeat_penalty": bot.generation_options.get("repeat_penalty", 1.2),
        "repeat_last_n": bot.generation_options.get("repeat_last_n", 64),
        "lazy_mode": bot.lazy_mode,
        "think_mode": bot.think_mode,
        "verbose_mode": bot.verbose_mode,
        "trace_mode": bot.trace_mode,
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
        bot.context_limit = int(data["context_limit"])
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
        self.context_limit = CONTEXT_LIMIT
        self.last_latency = 0.0
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

    def load_role_context_and_status(self, role_name: str) -> bool:
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

        self.chat_history = [system_msg] + loaded_msgs
        return True

    def load_persona_context_and_status(self, persona_name: str) -> bool:
        if not persona_name:
            self.current_persona = ""
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

        ctx_path = self.get_persona_context_path(persona_name)
        loaded_msgs = []
        if ctx_path.exists():
            try:
                text = ctx_path.read_text(encoding='utf-8')
                loaded_msgs = parse_context_text(text)
            except Exception as e:
                print(f"⚠️ Error reading persona context {ctx_path}: {e}")

        sys_content = get_role_content(self.current_role)
        persona_content = get_persona_content(persona_name)
        if persona_content:
            sys_content += f"\n\nUSER PERSONA:\n{persona_content}"
        system_msg = {"role": "system", "content": sys_content, "msg_ids": [], "is_command": False}

        self.chat_history = [system_msg] + loaded_msgs
        return True

    def load_story_context_and_status(self, story_name: str) -> bool:
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
        system_msg = {"role": "system", "content": role_content, "msg_ids": [], "is_command": False}
        self.chat_history = [system_msg] + loaded_msgs
        return True

    async def switch_role(self, new_role: str) -> Tuple[bool, str]:
        if tracker.is_chat_in_use(new_role, self.name):
            owner = tracker.get_chat_owner(new_role)
            return False, f"Role `{new_role}` is currently in use by bot `{owner}`. No two bots can use the same role or chat."

        self.save_current_context()
        self.save_current_status()

        tracker.claim_chat(new_role, self.name)
        self.current_role = new_role

        self.load_role_context_and_status(new_role)
        if self.current_persona:
            self.load_persona_context_and_status(self.current_persona)
        self.save_session("last_session")
        return True, ""

    async def switch_persona(self, new_persona: str) -> Tuple[bool, str]:
        self.save_current_context()
        self.save_current_status()

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

        tracker.claim_story(new_story, self.name)
        self.active_story = new_story
        self.active_characters = []
        self.active_scenes = []

        self.load_story_context_and_status(new_story)
        self.save_session("last_session")
        return True, ""
    
    def save_session(self, name: str = "last_session"):
        filename = f"{self.name}_{name}.json" if name == "last_session" else f"{name}.json"
        file_path = CHAT_SESSIONS_DIR / filename
        data = {
            "metadata": {
                "bot_name": self.name,
                "model": self.current_model,
                "role": self.current_role,
                "persona": self.current_persona,
                "provider": self.current_provider,
                "context_limit": self.context_limit,
                "mode": self.current_mode,
                "active_characters": self.active_characters,
                "active_scenes": self.active_scenes,
                "active_story": self.active_story,
                "num_ctx": self.generation_options.get("num_ctx", 4096),
                "lazy_mode": self.lazy_mode,
                "think_mode": self.think_mode,
                "verbose_mode": self.verbose_mode,
                "trace_mode": self.trace_mode
            },
            "history": self.chat_history
        }
        try:
            temp_path = file_path.with_suffix(".tmp")
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            temp_path.replace(file_path)
        except Exception as e:
            print(f"❌ Error saving session {file_path}: {e}")

    def load_session(self, name: str = "last_session") -> bool:
        clean_name = name.replace(".json", "")
        file_path = (CHAT_SESSIONS_DIR / f"{self.name}_{clean_name}.json").resolve()
        if not file_path.exists():
            file_path = (CHAT_SESSIONS_DIR / f"{clean_name}.json").resolve()

        if file_path.exists():
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "history" in data:
                        self.chat_history = data["history"]
                        meta = data.get("metadata", {})
                        self.current_model = meta.get("model", self.current_model)
                        self.current_role = meta.get("role", self.current_role)
                        self.current_persona = meta.get("persona", self.current_persona)
                        self.current_provider = meta.get("provider", self.current_provider)
                        self.context_limit = meta.get("context_limit", self.context_limit)
                        self.current_mode = meta.get("mode", "chat")
                        self.active_characters = meta.get("active_characters", [])
                        self.active_scenes = meta.get("active_scenes", [])
                        self.active_story = meta.get("active_story", "")
                        self.lazy_mode = meta.get("lazy_mode", False)
                        self.think_mode = meta.get("think_mode", False)
                        self.verbose_mode = meta.get("verbose_mode", False)
                        self.trace_mode = meta.get("trace_mode", False)
                        if "num_ctx" in meta:
                            self.generation_options["num_ctx"] = meta["num_ctx"]
                    else:
                        self.chat_history = data
                    return True
            except Exception as e:
                print(f"❌ Error loading session {file_path}: {e}")
                return False
        return False

    def init_state(self, tracker_instance):
        loaded = self.load_session("last_session")
        if not loaded:
            self.chat_history = [{"role": "system", "content": get_role_content(self.current_role), "msg_ids": [], "is_command": False}]

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
                self.load_story_context_and_status(self.active_story)

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
            self.load_role_context_and_status(self.current_role)
            if self.current_persona:
                self.load_persona_context_and_status(self.current_persona)

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


async def reply_and_log(update: Update, text: str, is_command: bool = True, bot: Optional[BotInstance] = None) -> None:
    """Helper to send a message, capture IDs, and log to history for session persistence."""
    if bot is None:
        bot = get_bot(update=update)

    user_msg_id = update.message.message_id

    # Ensure the user's command is logged if it hasn't been already
    already_logged = any(user_msg_id in m.get("msg_ids", []) for m in bot.chat_history[-2:])
    if not already_logged and bot.chat_history and bot.chat_history[-1]["role"] == "user":
        if user_msg_id not in bot.chat_history[-1].get("msg_ids", []):
            bot.chat_history[-1].setdefault("msg_ids", []).append(user_msg_id)

    sent_msg = await update.message.reply_text(text, parse_mode="Markdown")
    
    if is_command:
        if bot.chat_history and bot.chat_history[-1]["role"] == "assistant":
            bot.chat_history[-1].setdefault("msg_ids", []).append(sent_msg.message_id)
    
    bot.save_active_context_and_status()


async def reply_transient(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, delay: int = 5) -> None:
    """Sends a message that deletes itself after a delay and is NOT logged to history."""
    try:
        # Physically remove the trigger command from the Telegram screen for a clean UI
        try: await update.message.delete()
        except: pass

        sent_msg = await update.message.reply_text(text, parse_mode="Markdown")
        async def _delete():
            await asyncio.sleep(delay)
            try: await sent_msg.delete()
            except: pass
        asyncio.create_task(_delete())
    except Exception as e:
        print(f"⚠️ reply_transient failed: {e}")