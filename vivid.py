"""
Telegram Bot for Interactive LLM Roleplay and Conversation Management.

Supports multiple LLM providers: Ollama, OpenAI-compatible (OpenRouter, llama.cpp, etc.), Gemini.
Features include dynamic role switching, conversation saving/loading,
context summarization (recap), and an 'Edit & Resend' workflow.
"""

import time
import os
import json
import sys
import asyncio
import yaml
import urllib.request
import urllib.parse
import zipfile
import tempfile
import shutil
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncIterator
from telegram import Update
from telegram.ext import Application, PrefixHandler, MessageHandler, filters, ContextTypes


def escape_markdown(text: str) -> str:
    """Escape special Markdown characters for Telegram's MarkdownV2 parser."""
    # Telegram MarkdownV2 special characters: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{c}' if c in escape_chars else c for c in text)


def format_role_display(role_name: str) -> str:
    """Format role name for display: remove everything after '!', replace '_' with spaces, capitalize each word."""
    # Remove everything after '!' (including the '!')
    if '!' in role_name:
        role_name = role_name.split('!')[0]
    # Replace underscores with spaces
    role_name = role_name.replace('_', ' ')
    # Capitalize first letter of each word
    return role_name.title()


def format_story_display(story_name: str) -> str:
    """Format story name for display: remove everything after '!', replace '_' with spaces, capitalize each word."""
    # Remove everything after '!' (including the '!')
    if '!' in story_name:
        story_name = story_name.split('!')[0]
    # Replace underscores with spaces
    story_name = story_name.replace('_', ' ')
    # Capitalize first letter of each word
    return story_name.title()

# Provider implementations
from ollama import AsyncClient as OllamaAsyncClient

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


# =============================================================================
# PROVIDER ABSTRACTION LAYER
# =============================================================================

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.description = config.get("description", name)
        self.show_on_provider_list = config.get("show_on_provider_list", True)
    
    @abstractmethod
    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        """Send a chat completion request and return the response text."""
        pass
    
    @abstractmethod
    async def list_models(self) -> List[str]:
        """List available models. Returns list of model names."""
        pass
    
    @abstractmethod
    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        """Pull/download a model. Returns async iterator of progress dicts."""
        pass
    
    @abstractmethod
    async def delete_model(self, model_name: str) -> None:
        """Delete a model."""
        pass
    
    @abstractmethod
    def supports_model_management(self) -> bool:
        """Whether this provider supports model pull/delete operations."""
        pass
    
    def get_display_name(self) -> str:
        return self.name


class OllamaProvider(LLMProvider):
    """Ollama provider using the official ollama-python client."""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        url = config.get("url")
        api_key = config.get("api_key")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self.client = OllamaAsyncClient(host=url, headers=headers, timeout=None)
    
    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        ollama_options = {**options}
        if "think" in ollama_options:
            think = ollama_options.pop("think")
            if think:
                ollama_options["think"] = True
        res = await self.client.chat(model=model, messages=messages, options=ollama_options)
        return res.message.content
    
    async def list_models(self) -> List[str]:
        resp = await self.client.list()
        models = getattr(resp, 'models', [])
        return [m.model for m in models]
    
    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        async for part in await self.client.pull(model=model_name, stream=True):
            yield part
    
    async def delete_model(self, model_name: str) -> None:
        await self.client.delete(model=model_name)
    
    def supports_model_management(self) -> bool:
        return True


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible provider (OpenRouter, llama.cpp --api-server, Together AI, etc.)."""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        if AsyncOpenAI is None:
            raise ImportError("openai package not installed. Run: pip install openai")
        
        url = config.get("url")
        api_key = config.get("api_key", "not-needed")
        extra_headers = config.get("extra_headers", {})
        # Allow custom models endpoint (e.g., llama.cpp serves models at /models not /v1/models)
        self.models_url = config.get("models_url")
        
        self.client = AsyncOpenAI(
            base_url=url,
            api_key=api_key,
            default_headers=extra_headers if extra_headers else None,
            timeout=None
        )
        self.model_aliases = config.get("model_aliases", {})
    
    def _resolve_model(self, model: str) -> str:
        return self.model_aliases.get(model, model)
    
    def _convert_options(self, options: Dict[str, Any]) -> Dict[str, Any]:
        openai_options = {}
        if "temperature" in options:
            openai_options["temperature"] = options["temperature"]
        if "top_p" in options:
            openai_options["top_p"] = options["top_p"]
        if "num_ctx" in options:
            openai_options["max_tokens"] = options["num_ctx"]
        if "repeat_penalty" in options:
            openai_options["frequency_penalty"] = options["repeat_penalty"] - 1.0
        return openai_options
    
    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        model = self._resolve_model(model)
        openai_options = self._convert_options(options)
        
        if options.get("think"):
            openai_options["reasoning_effort"] = "high"
        
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            **openai_options
        )
        return response.choices[0].message.content or ""
    
    async def list_models(self) -> List[str]:
        # Prefer the standard OpenAI-compatible endpoint (/v1/models).
        try:
            models = await self.client.models.list()
            model_ids = [m.id for m in models.data]
            if model_ids:
                return model_ids
        except Exception:
            pass

        # Try a configured custom endpoint if the standard endpoint is unavailable.
        if self.models_url:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    headers = {}
                    api_key = self.config.get("api_key")
                    if api_key and api_key != "not-needed":
                        headers["Authorization"] = f"Bearer {api_key}"
                    extra_headers = self.config.get("extra_headers", {})
                    headers.update(extra_headers)
                    async with session.get(self.models_url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            model_ids = [
                                model.get("id")
                                for model in data.get("data", [])
                                if model.get("id")
                            ]
                            if model_ids:
                                return model_ids
            except Exception:
                pass
        
        # Fallback: try /models endpoint (llama.cpp compatibility)
        # Derive base URL without /v1 suffix
        base_url = self.config.get("url", "")
        if base_url.endswith("/v1"):
            fallback_url = base_url[:-3] + "/models"
        else:
            fallback_url = base_url.rstrip("/") + "/models"
        
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                headers = {}
                api_key = self.config.get("api_key")
                if api_key and api_key != "not-needed":
                    headers["Authorization"] = f"Bearer {api_key}"
                extra_headers = self.config.get("extra_headers", {})
                headers.update(extra_headers)
                async with session.get(fallback_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m.get("id") for m in data.get("data", []) if m.get("id")]
        except Exception:
            pass
        
        # Final fallback: return static models from config
        return self.config.get("models", [])
    
    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        yield {"status": "error", "error": "Model pulling not supported for OpenAI-compatible providers"}
    
    async def delete_model(self, model_name: str) -> None:
        raise NotImplementedError("Model deletion not supported for OpenAI-compatible providers")
    
    def supports_model_management(self) -> bool:
        return False


class GeminiProvider(LLMProvider):
    """Google Gemini provider."""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        if genai is None or types is None:
            raise ImportError("google-genai package not installed. Run: pip install google-genai")
        
        api_key = config.get("api_key")
        if not api_key:
            raise ValueError("Gemini provider requires 'api_key' in config")
        
        self.client = genai.Client(api_key=api_key)
        self.model_name = config.get("model", "gemini-1.5-flash")
    
    async def chat(self, model: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
        system_instruction = None
        contents = []
        
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
        
        config = types.GenerateContentConfig(
            temperature=options.get("temperature", 0.8),
            top_p=options.get("top_p", 0.9),
            max_output_tokens=options.get("num_ctx", 4096),
            system_instruction=system_instruction,
        )
        
        response = await self.client.aio.models.generate_content_async(
            model=model,
            contents=contents,
            config=config
        )
        return response.text or ""
    
    async def list_models(self) -> List[str]:
        try:
            models = []
            for m in self.client.models.list():
                if "generateContent" in m.supported_generation_methods:
                    models.append(m.name.replace("models/", ""))
            return models
        except Exception:
            return [self.model_name]
    
    async def pull_model(self, model_name: str) -> AsyncIterator[Dict[str, Any]]:
        yield {"status": "error", "error": "Model pulling not supported for Gemini"}
    
    async def delete_model(self, model_name: str) -> None:
        raise NotImplementedError("Model deletion not supported for Gemini")
    
    def supports_model_management(self) -> bool:
        return False


def create_provider(name: str, config: Dict[str, Any]) -> LLMProvider:
    """Factory function to create provider instances."""
    provider_type = config.get("type", "ollama")
    
    if provider_type == "ollama":
        return OllamaProvider(name, config)
    elif provider_type == "openai_compatible":
        return OpenAICompatibleProvider(name, config)
    elif provider_type == "gemini":
        return GeminiProvider(name, config)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")


# =============================================================================
# INITIALIZATION
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"

if not CONFIG_PATH.exists():
    print(f"❌ Error: {CONFIG_PATH} not found.")
    exit(1)

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    CONFIG = yaml.safe_load(f)

# Optional paths override
PATHS_CONFIG_PATH = BASE_DIR / "config.paths.yaml"
if PATHS_CONFIG_PATH.exists():
    try:
        with open(PATHS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            CONFIG.update(yaml.safe_load(f))
    except Exception as e:
        print(f"⚠️ Warning: Failed to load {PATHS_CONFIG_PATH}: {e}")

LAST_LATENCY = 0
TOTAL_MESSAGES_SENT = 0
TELEGRAM_USER_ID = int(CONFIG.get("TELEGRAM_USER_ID", 0))

CONTEXT_LIMIT = 50  # Number of recent messages to send

# Suggested default params for the bot
GENERATION_OPTIONS = {
    "repeat_penalty": 1.2,
    "top_p": 0.9,
    "temperature": 0.8,
    "repeat_last_n": 64,
    "num_ctx": CONFIG.get("OLLAMA_NUM_CTX", 4096)
}

TELEGRAM_TOKENS = CONFIG.get("TELEGRAM_TOKENS", {})
VIVID_PREFIX = "bot"

suffix = "_WINDOWS" if os.name == 'nt' else "_LINUX"

# =============================================================================
# NEW chats_stories DIRECTORY LAYOUT
# =============================================================================
# Base directory for all chats and stories
CHATS_STORIES_DIR = BASE_DIR / "chats_stories"

# Chats section (one-on-one roles, chat histories, backups)
CHATS_DIR = CHATS_STORIES_DIR / "chats"
CHAT_PERSONAS_DIR = CHATS_DIR / "personas"
CHAT_ROLES_DIR = CHATS_DIR / "roles"  # Both .txt and .jpg files go here
CHAT_CONTEXT_DIR = CHATS_DIR / "chat_context"
CHAT_CONFIG_DIR = CHATS_DIR / "config"
CHAT_BACKUPS_DIR = CHATS_DIR / "backups"
CHAT_SESSIONS_DIR = CHATS_DIR / "session"

# Stories section (multi-character narrative containers)
STORIES_DIR = CHATS_STORIES_DIR / "stories"

# Legacy directory aliases (pointing to new structure for backward compatibility)
ROLES_DIR = CHAT_ROLES_DIR
ROLE_IMAGES_DIR = CHAT_ROLES_DIR  # Images stored alongside .txt files
CONTEXT_DIR = CHAT_CONTEXT_DIR
SETTINGS_DIR = BASE_DIR / "settings"

# Per-story directories (resolved dynamically based on ACTIVE_STORY)
# CHARACTERS_DIR -> STORIES_DIR / ACTIVE_STORY / "characters"
# SCENES_DIR -> STORIES_DIR / ACTIVE_STORY / "storyelements"

# Ensure directories exist
for d in [CHATS_DIR, CHAT_PERSONAS_DIR, CHAT_ROLES_DIR, CHAT_CONTEXT_DIR, CHAT_CONFIG_DIR, CHAT_BACKUPS_DIR, CHAT_SESSIONS_DIR, STORIES_DIR, SETTINGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# --- PROVIDERS ---
# Load providers from new PROVIDERS config, with backward compatibility for OLLAMA_LOCAL/OLLAMA_ONLINE
providers_config = CONFIG.get("PROVIDERS", [])

# Backward compatibility: convert old OLLAMA_LOCAL/OLLAMA_ONLINE to new format
if not providers_config:
    OLLAMA_LOCAL = CONFIG.get("OLLAMA_LOCAL", [])
    OLLAMA_ONLINE = CONFIG.get("OLLAMA_ONLINE", [])
    
    for i, cfg in enumerate(OLLAMA_LOCAL):
        is_dict = isinstance(cfg, dict)
        url = cfg.get("url") if is_dict else cfg
        display = cfg.get("description", url) if is_dict else url
        show = cfg.get("show_on_provider_list", True) if is_dict else True
        providers_config.append({
            "name": f"local-{i}",
            "type": "ollama",
            "url": url,
            "description": display,
            "show_on_provider_list": show
        })
    
    for i, cfg in enumerate(OLLAMA_ONLINE):
        is_dict = isinstance(cfg, dict)
        url = cfg.get("url", "https://ollama.com") if is_dict else cfg
        display = cfg.get("description", url) if is_dict else url
        api_key = cfg.get("api_key") if is_dict else None
        show = cfg.get("show_on_provider_list", True) if is_dict else True
        provider_cfg = {
            "name": f"online-{i}",
            "type": "ollama",
            "url": url,
            "description": display,
            "show_on_provider_list": show
        }
        if api_key:
            provider_cfg["api_key"] = api_key
        providers_config.append(provider_cfg)

# Create provider instances
providers = {}
provider_display_info = {}
AI_PROVIDERS = []

for cfg in providers_config:
    name = cfg.get("name")
    if not name:
        continue
    
    try:
        provider = create_provider(name, cfg)
        providers[name] = provider
        
        if provider.show_on_provider_list:
            AI_PROVIDERS.append(name)
            provider_display_info[name] = provider.get_display_name()
    except Exception as e:
        print(f"⚠️ Failed to initialize provider '{name}': {e}")

# Backward compatibility: ollama_clients dict for old code
ollama_clients = {}
for name, provider in providers.items():
    if isinstance(provider, OllamaProvider):
        ollama_clients[name] = provider.client

# =============================================================================
# CONTEXT SERIALIZATION & TRACKING
# =============================================================================

def format_context_text(history: List[Dict[str, Any]]) -> str:
    """
    Formats history into the specified format:
    user -----------
    content

    assistant -----------
    content
    """
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
    """
    Parses text in role ----------- format back into chat_history entries.
    """
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
    """Extracts status and configuration from a BotInstance for saving to .status."""
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
    """Applies loaded status dict to a BotInstance."""
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


tracker = BotUsageTracker(SETTINGS_DIR / "bot_assignments.json")


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
        self.current_persona = ""  # User's persona (from chats/personas/)
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
            "num_ctx": CONFIG.get("OLLAMA_NUM_CTX", 4096)
        }
        self.chat_history: List[Dict[str, Any]] = []

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
        """Saves current context to chat_context (if in chat mode) or story_context (if in story mode)."""
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
        """Saves configuration, model, role, and options to .status."""
        status_data = get_bot_status_dict(self)
        if self.current_mode == "chat" and self.current_role:
            st_path = self.get_role_status_path()
            try:
                temp_path = st_path.with_suffix(".tmp")
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
                temp_path.replace(st_path)
                # Also write CHAT_CONFIG_DIR / ".status"
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
                    # Also update story's config/.status if config dir exists
                    cfg_dir = STORIES_DIR / self.active_story / "config"
                    if cfg_dir.exists():
                        cfg_dot_status = cfg_dir / ".status"
                        with open(cfg_dot_status, 'w', encoding='utf-8') as f:
                            json.dump(status_data, f, indent=4, ensure_ascii=False)
                except Exception as e:
                    print(f"❌ Error saving story status to {st_path}: {e}")

    def save_active_context_and_status(self):
        """Saves context, status, and session snapshot."""
        self.save_current_context()
        self.save_current_status()
        self.save_session("last_session")

    def load_role_context_and_status(self, role_name: str) -> bool:
        """Loads previous context and status for a role."""
        # 1. Load status if present
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

        # 2. Reconstruct system prompt
        sys_content = get_role_content(role_name)
        system_msg = {"role": "system", "content": sys_content, "msg_ids": [], "is_command": False}

        # 3. Load previous context if present
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
        """Loads previous context and status for a persona."""
        if not persona_name:
            self.current_persona = ""
            return True

        # 1. Load status if present
        st_path = self.get_persona_status_path(persona_name)
        if st_path.exists():
            try:
                with open(st_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    apply_status_dict(self, data)
            except Exception as e:
                print(f"⚠️ Error reading persona status {st_path}: {e}")

        self.current_persona = persona_name

        # 2. Load previous context if present
        ctx_path = self.get_persona_context_path(persona_name)
        loaded_msgs = []
        if ctx_path.exists():
            try:
                text = ctx_path.read_text(encoding='utf-8')
                loaded_msgs = parse_context_text(text)
            except Exception as e:
                print(f"⚠️ Error reading persona context {ctx_path}: {e}")

        # 3. Reconstruct system prompt with persona content appended
        sys_content = get_role_content(self.current_role)
        persona_content = get_persona_content(persona_name)
        if persona_content:
            sys_content += f"\n\nUSER PERSONA:\n{persona_content}"
        system_msg = {"role": "system", "content": sys_content, "msg_ids": [], "is_command": False}

        self.chat_history = [system_msg] + loaded_msgs
        return True

    def load_story_context_and_status(self, story_name: str) -> bool:
        """Loads previous context and status for a story."""
        # 1. Load status if present
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

        # 2. Load context
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

    async def switch_role(self, new_role: str) -> tuple[bool, str]:
        """Switches to a new role, saving current context and reloading target's context."""
        global tracker
        if tracker.is_chat_in_use(new_role, self.name):
            owner = tracker.get_chat_owner(new_role)
            return False, f"Role `{format_role_display(new_role)}` is currently in use by bot `{owner}`. No two bots can use the same role or chat."
        
        # Save current context and status
        self.save_current_context()
        self.save_current_status()
        
        # Switch in tracker
        tracker.claim_chat(new_role, self.name)
        self.current_role = new_role
        
        # Load target context and status
        self.load_role_context_and_status(new_role)
        # Re-apply persona if one is active
        if self.current_persona:
            self.load_persona_context_and_status(self.current_persona)
        self.save_session("last_session")
        return True, ""

    async def switch_persona(self, new_persona: str) -> tuple[bool, str]:
        """Switches to a new persona, saving current context and reloading target's context."""
        global tracker
        # Personas don't have tracker conflicts - they're user-specific
        
        # Save current context and status
        self.save_current_context()
        self.save_current_status()
        
        self.current_persona = new_persona
        
        # Load target context and status
        self.load_persona_context_and_status(new_persona)
        self.save_session("last_session")
        return True, ""

    async def switch_story(self, new_story: str) -> tuple[bool, str]:
        """Switches to a new story, saving current context and reloading target's context."""
        global tracker
        if tracker.is_story_in_use(new_story, self.name):
            owner = tracker.get_story_owner(new_story)
            return False, f"Story `{new_story}` is currently in use by bot `{owner}`. No two bots can use the same story."
        
        # Save current context and status
        self.save_current_context()
        self.save_current_status()
        
        # Switch in tracker
        tracker.claim_story(new_story, self.name)
        self.active_story = new_story
        self.active_characters = []
        self.active_scenes = []
        
        # Load target context and status
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

    def init_state(self, tracker_instance: BotUsageTracker):
        """Initializes bot state on startup, resolving conflicts and loading context."""
        loaded = self.load_session("last_session")
        if not loaded:
            self.chat_history = [{"role": "system", "content": get_role_content(self.current_role), "msg_ids": [], "is_command": False}]

        # Check and resolve tracker conflicts for stories
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

        # Check and resolve tracker conflicts for chats/roles
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
            # Load role context first, then overlay persona if present
            self.load_role_context_and_status(self.current_role)
            if self.current_persona:
                self.load_persona_context_and_status(self.current_persona)


# Multi-bot registry
BOT_INSTANCES: Dict[str, BotInstance] = {}
BOT_INSTANCES_BY_ID: Dict[int, BotInstance] = {}
BOT_INSTANCES_BY_TOKEN: Dict[str, BotInstance] = {}

def get_current_default_bot() -> BotInstance:
    """Returns the primary active bot instance or a fallback."""
    if BOT_INSTANCES:
        return next(iter(BOT_INSTANCES.values()))
    dummy = BotInstance("bot", "")
    return dummy

def get_bot(context: Optional[ContextTypes.DEFAULT_TYPE] = None, update: Optional[Update] = None) -> BotInstance:
    """Resolves the BotInstance corresponding to the current request."""
    if context and hasattr(context, 'bot_data') and context.bot_data and "bot_instance" in context.bot_data:
        return context.bot_data["bot_instance"]
    if update:
        bot_obj = update.get_bot()
        if bot_obj:
            if bot_obj.id in BOT_INSTANCES_BY_ID:
                return BOT_INSTANCES_BY_ID[bot_obj.id]
            if bot_obj.token in BOT_INSTANCES_BY_TOKEN:
                return BOT_INSTANCES_BY_TOKEN[bot_obj.token]
    return get_current_default_bot()


# --- BACKWARD COMPATIBLE GLOBAL PROXIES ---
CURRENT_PROVIDER = AI_PROVIDERS[0] if AI_PROVIDERS else "none"
CURRENT_MODEL = "llama3.2:3b"
CACHED_MODELS = []
CURRENT_ROLE = "Assistant"
ACTIVE_CHARACTERS = []
ACTIVE_SCENES = []
ACTIVE_STORY = ""
CURRENT_MODE = "chat"
THINK_MODE = False
VERBOSE_MODE = False
TRACE_MODE = False
LAZY_MODE = False
chat_history = []


def save_to_json(name, bot: Optional[BotInstance] = None):
    """Saves the current session history to JSON."""
    if bot is None:
        bot = get_current_default_bot()
    bot.save_session(name)


def load_from_json(name, bot: Optional[BotInstance] = None):
    """Loads a session history from JSON."""
    if bot is None:
        bot = get_current_default_bot()
    return bot.load_session(name)


async def generate_response(messages, options=None, bot: Optional[BotInstance] = None):
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
    
    return await provider.chat(model=bot.current_model, messages=messages, options=call_opts)


async def reply_and_log(update: Update, text: str, is_command=True, bot: Optional[BotInstance] = None):
    """Helper to send a message, capture IDs, and log to history for session persistence."""
    if bot is None:
        bot = get_bot(update=update)

    user_msg_id = update.message.message_id

    # Ensure the user's command is logged if it hasn't been already
    already_logged = any(user_msg_id in m.get("msg_ids", []) for m in bot.chat_history[-2:])
    if not already_logged:
        bot.chat_history.append({
            "role": "user", "content": update.message.text,
            "msg_ids": [user_msg_id], "is_command": True, "incoming": True
        })
    
    sent_ids = []
    for i in range(0, len(text), 4000):
        sent_msg = await update.message.reply_text(text[i:i+4000], parse_mode='Markdown')
        sent_ids.append(sent_msg.message_id)
    
    bot.chat_history.append({
        "role": "assistant", "content": text, 
        "msg_ids": sent_ids, "is_command": is_command
    })
    bot.save_active_context_and_status()


async def reply_transient(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, delay: int = 5):
    """Sends a message that deletes itself after a delay and is NOT logged to history."""
    try:
        # Physically remove the trigger command from the Telegram screen for a clean UI
        try: await update.message.delete()
        except: pass
        
        sent_msg = await update.message.reply_text(text, parse_mode='Markdown')
        async def _delete():
            await asyncio.sleep(delay)
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=sent_msg.message_id)
            except:
                pass
        asyncio.create_task(_delete())
    except Exception as e:
        print(f"⚠️ Failed to send transient reply: {e}")


def get_role_content(role_name):
    """
    Reads the persona/role definition from a .txt file.
    Returns a fallback string if the role file is missing.
    Supports both local files and Gist-backed files (name!gist_id.txt).
    """
    role_file = ROLES_DIR / f"{role_name}.txt"
    if role_file.exists():
        content = role_file.read_text(encoding='utf-8').strip()
        if not content: return "You are a helpful AI assistant."
        return content
    return "You are a helpful AI assistant."


def get_persona_content(persona_name: str) -> str:
    """
    Reads the persona definition from a .txt file in the personas directory.
    Returns empty string if the persona file is missing.
    """
    if not persona_name:
        return ""
    persona_file = CHAT_PERSONAS_DIR / f"{persona_name}.txt"
    if persona_file.exists():
        content = persona_file.read_text(encoding='utf-8').strip()
        return content
    return ""


def get_story_characters_dir(active_story: str = ""):
    """Returns the characters directory for the active story."""
    story = active_story or getattr(get_current_default_bot(), 'active_story', "")
    if story:
        return STORIES_DIR / story / "characters"
    return None


def get_story_elements_dir(active_story: str = ""):
    """Returns the story elements directory for the active story."""
    story = active_story or getattr(get_current_default_bot(), 'active_story', "")
    if story:
        return STORIES_DIR / story / "storyelements"
    return None


def get_character_content(char_name, active_story: str = ""):
    """
    Reads the character description from a .txt file in the active story's characters directory.
    """
    chars_dir = get_story_characters_dir(active_story)
    if not chars_dir or not chars_dir.exists():
        return ""
    char_file = chars_dir / f"{char_name}.txt"
    if char_file.exists():
        return char_file.read_text(encoding='utf-8').strip()
    return ""


def get_element_content(element_name, active_story: str = ""):
    """
    Reads the story element description from a .txt file
    in the active story's storyelements directory.
    """
    elements_dir = get_story_elements_dir(active_story)
    if not elements_dir or not elements_dir.exists():
        return ""
    element_file = elements_dir / f"{element_name}.txt"
    if element_file.exists():
        return element_file.read_text(encoding='utf-8').strip()
    return ""


def get_scene_content(scene_name: str) -> str:
    """Reads legacy scene description from scenes directory."""
    scene_file = BASE_DIR / "scenes" / f"{scene_name}.txt"
    if scene_file.exists():
        return scene_file.read_text(encoding='utf-8').strip()
    return ""

def get_character_content(char_name):
    """
    Reads the character description from a .txt file in the active story's characters directory.
    Only works in story mode with an active story selected.
    """
    chars_dir = get_story_characters_dir()
    if not chars_dir or not chars_dir.exists():
        return ""
    char_file = chars_dir / f"{char_name}.txt"
    if char_file.exists():
        return char_file.read_text(encoding='utf-8').strip()
    return ""

def get_element_content(element_name):
    """
    Reads the story element (location, item, etc.) description from a .txt file
    in the active story's storyelements directory.
    Only works in story mode with an active story selected.
    """
    elements_dir = get_story_elements_dir()
    if not elements_dir or not elements_dir.exists():
        return ""
    element_file = elements_dir / f"{element_name}.txt"
    if element_file.exists():
        return element_file.read_text(encoding='utf-8').strip()
    return ""

async def h_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias for help_command."""
    await help_command(update, context) 

async def c_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await chat_actions(update, context)


# --- COMMANDS ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the available bot commands to the user."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        print(f"🚫 Help command rejected: user {update.effective_user.id} != config {TELEGRAM_USER_ID}")
        return

    help_text = (
        "🤖 *Vivid commands*\n"
        "Commands start with a dot. Use the shown arguments where needed.\n\n"
        "*Help and roles*\n"
        "• `.help` / `.h` - Show this command list\n"
        "• `.role` / `.r` - List roles; `.role N` switches role\n"
        "• `.role i` / `.role image` / `.r i` / `.r image` - List all roles with their images\n"
        "• `.role edit N` - Show role text; `.role save NAME TEXT` saves it\n"
        "• `.reload` - Reload current role; `.image` - Show its image\n"
        "• `.rs [N]` / `.rolesummary [N]` - Summarize the current role\n"
        "• `.roles` - List all available roles (chat mode only)\n\n"
        "*Personas (user identity)*\n"
        "• `.persona` / `.per` - List personas; `.persona N` switches persona\n"
        "• `.persona i` / `.persona image` - List all personas with their images\n"
        "• `.persona clear` - Clear current persona\n\n"
        
        "*Providers and models*\n"
        "• `.provider` / `.p` - List providers; `.provider N` switches provider\n"
        "• `.model` / `.m` - List models; `.model N` switches model\n"
        "• `.model pull NAME` - Pull model (Ollama only)\n"
        "• `.model rm N` - Remove listed model (Ollama only)\n"
        "• `.model loaded` - Select a model loaded in RAM (Ollama only)\n"
        "• `.model test` - Check model access (Ollama only)\n"
        "• `.mf [n|next|prev]` / `.modelsfiltered` - Browse accessible models\n"
        "• `.think` - Toggle think mode\n"
        "• `.llmctx [Nk]` / `.mc` / `.mcontext` - Show/set context size, e.g. `8k`\n\n"
        "*Stories*\n"
        "• `.story select` - List stories; `.story select NAME|N` activates one\n"
        "• `.story save NAME` / `.store save NAME` - Save assistant story text\n"
        "• `.stories` / `.st` - List stories (story mode only); `.st [N]` selects story\n"
        "• `.stories image` / `.stories i` - Show story thumbnails\n"
        "• `.char` - List characters; `.char [N|NAME] [on|off]` toggles one\n"
        "• `.char all off` - Deactivate all characters\n"
        "• `.char edit N`; `.char save NAME TEXT`\n"
        "• `.element` / `.el` - List story elements\n"
        "• `.element [N|NAME] [on|off]`; `.element all off`\n"
        "• `.element edit N`; `.element save NAME TEXT`\n"
        "• `.install GIST_ID_OR_URL` - Install a role or story from a GitHub Gist\n\n"
        "*Chats and history*\n"
        "• `.chat` / `.chats` / `.c` - List saved chats\n"
        "• `.chat save NAME`; `.chat load NAME`\n"
        "• `.clear` - Clear conversation memory\n"
        "• `.clean` / `.cl` - Clear memory and remove chat messages\n"
        "• `.mode [chat|story]` / `.mo` - Show or change mode\n"
        f"• `.recap [N]` - Summarize the last {CONTEXT_LIMIT} messages, with optional prompt N\n"
        "• `.ask QUESTION` - Ask about conversation history\n"
        "• `.last [N]` / `.l` - Show recent messages (3 by default)\n"
        "• `.del` - Delete last exchange; `.resend` - Retry last user message\n"
        "• `.context [NUMBER]` - Show/set history context limit\n\n"
        "*Settings and status*\n"
        "• `.status` / `.s` - Show current settings\n"
        "• `.verbose` / `.v` - Toggle response details\n"
        "• `.trace [on|off]` - Toggle request tracing\n"
        "• `.lazy` / `.lz` - Toggle commands without the dot"
    )
    await reply_and_log(update, help_text)

async def p_command(update: Update, context: ContextTypes.DEFAULT_TYPE):  
    await provider_command(update, context)  

async def provider_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main entry point for .provider command. 
    Lists providers if no args, otherwise switches provider.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await list_providers(update, context)
    else:
        await switch_provider(update, context)

async def m_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await model_command(update, context)


async def list_providers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all configured providers."""
    bot = get_bot(context, update)
    text = f"🌐 **AI Providers (Bot: {bot.name})**\n\n"
    idx = 1
    
    for p in AI_PROVIDERS:
        active = "✅" if p == bot.current_provider else ""
        desc = provider_display_info.get(p, p)
        text += f"{idx}. `{desc}` {active}\n"
        idx += 1
    await reply_and_log(update, text, bot=bot)

async def switch_provider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches the active provider for this bot based on list index."""
    bot = get_bot(context, update)
    try:
        index = int(context.args[0]) - 1
        bot.current_provider = AI_PROVIDERS[index]
        bot.save_active_context_and_status()
        display = provider_display_info.get(bot.current_provider, bot.current_provider)
        await reply_transient(update, context, f"🚀 Using: `{display}`")
    except:
        await reply_and_log(update, "❌ Usage: `.provider [n]`", bot=bot)

async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lists models available on the currently selected provider for this bot.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    provider = providers.get(bot.current_provider)
    if not provider:
        provider_name = escape_markdown(bot.current_provider)
        await reply_and_log(update, f"⚠️ Provider '{provider_name}' not found.", bot=bot)
        return

    try:
        models = await provider.list_models()
        if not models:
            await reply_and_log(update, "📭 No models found.", bot=bot)
            return

        bot.cached_models = models
        models.sort()

        model_list = []
        for i, m in enumerate(models):
            active = " ✅" if m == bot.current_model else ""
            model_list.append(f"{i+1}) `{m}`{active}")
        
        display = provider.get_display_name()
        text = f"🤖 **`{display}` Models (Bot: {bot.name}):**\n" + "\n".join(model_list)
        await reply_and_log(update, text, bot=bot)
    except Exception as e:
        display = provider.get_display_name()
        await reply_and_log(update, f"❌ Failed to reach {display}: {e}", bot=bot)

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main entry point for .model command.
    Lists models if no args, otherwise switches model by index or sub-command.
    """
    if not context.args:
        await list_models(update, context)
    else:
        await switch_model(update, context)

async def delete_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes a model from the provider (Ollama only)."""
    bot = get_bot(context, update)
    provider = providers.get(bot.current_provider)
    if not provider or not provider.supports_model_management():
        await reply_and_log(update, "❌ Model deletion only supported for Ollama providers.", bot=bot)
        return
    try:
        idx = int(context.args[1]) - 1
        if not bot.cached_models:
            bot.cached_models = await provider.list_models()
            bot.cached_models.sort()
        target = bot.cached_models[idx]
        await provider.delete_model(target)
        bot.cached_models = []
        await reply_and_log(update, f"🗑️ Model `{target}` deleted.", bot=bot)
    except Exception as e:
        await reply_and_log(update, f"❌ Failed to delete model: {e}", bot=bot)

async def switch_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles model switching and management sub-commands:
    - 'loaded': Syncs current_model with whatever is in the provider's RAM (Ollama only).
    - 'pull': Triggers an async model download (Ollama only).
    - 'rm': Deletes a model from the provider (Ollama only).
    """
    bot = get_bot(context, update)
    provider = providers.get(bot.current_provider)
    if not provider:
        provider_name = escape_markdown(bot.current_provider)
        await reply_and_log(update, f"⚠️ Provider '{provider_name}' not found.", bot=bot)
        return

    sub_cmd = context.args[0].lower()

    # --- TEST MODELS (.model test) ---
    if sub_cmd == "test":
        await test_provider_models(update, context)
        return

    # --- A. SYNC WITH RAM (Ollama only) ---
    if sub_cmd == "loaded":
        if not provider.supports_model_management():
            await reply_and_log(update, "❌ 'loaded' command only supported for Ollama providers.", bot=bot)
            return
        try:
            if isinstance(provider, OllamaProvider):
                resp = await provider.client.ps()
                if hasattr(resp, 'models') and resp.models:
                    bot.current_model = resp.models[0].model
                    bot.save_active_context_and_status()
                    await reply_transient(update, context, f"🔄 **Syncing:** Now using `{bot.current_model}`.")
                else:
                    display = provider.get_display_name()
                    await reply_and_log(update, f"ℹ️ No models loaded in RAM on `{display}`.", bot=bot)
            else:
                await reply_and_log(update, "❌ 'loaded' command only supported for Ollama providers.", bot=bot)
        except Exception as e:
            display = provider.get_display_name()
            await reply_and_log(update, f"❌ Failed to check {display}: {e}", bot=bot)
        return

    # --- B. PULL MODEL (.model pull name) ---
    if sub_cmd == "pull" and len(context.args) > 1:
        await pull_model(update, context)
        return

    # --- C. REMOVE MODEL (.model rm 2) ---
    if sub_cmd == "rm" and len(context.args) > 1:
        await delete_model_command(update, context)
        return

    # --- D. SWITCH BY NUMBER (.model 3) ---
    try:
        index = int(sub_cmd) - 1
        if not bot.cached_models:
            models = await provider.list_models()
            models.sort()
            bot.cached_models = models
        
        bot.current_model = bot.cached_models[index]
        bot.save_active_context_and_status()
        await reply_transient(update, context, f"🧠 Switched to: `{bot.current_model}`")
    except:
        await reply_and_log(update, "❌ Invalid command or index. Use `.model` for options.", bot=bot)

async def pull_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asynchronously pulls a model to the provider (Ollama only)."""
    bot = get_bot(context, update)
    provider = providers.get(bot.current_provider)
    if not provider:
        provider_name = escape_markdown(bot.current_provider)
        await reply_and_log(update, f"❌ Provider '{provider_name}' not found.", bot=bot)
        return

    if not provider.supports_model_management():
        await reply_and_log(update, "❌ Model pulling only supported for Ollama providers.", bot=bot)
        return

    model_name = context.args[1]
    status_msg = await update.message.reply_text(f"📥 Starting pull for `{model_name}`...")
    
    try:
        async for part in provider.pull_model(model_name):
            if 'percentage' in part:
                progress = part['percentage']
                if progress % 10 == 0:
                    await status_msg.edit_text(f"📥 Pulling `{model_name}`: {progress}%")
        
        await status_msg.edit_text(f"✅ Successfully pulled `{model_name}`!")
        bot.cached_models = []
    except Exception as e:
        await status_msg.edit_text(f"❌ Pull failed: {e}")

async def reload_role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Reloads the current role's system prompt from disk without clearing
    user/assistant history (unless history is empty).
    Also reapplies the current persona if one is active.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    new_content = get_role_content(bot.current_role)
    
    # Re-apply persona if active
    if bot.current_persona:
        persona_content = get_persona_content(bot.current_persona)
        if persona_content:
            new_content += f"\n\nUSER PERSONA:\n{persona_content}"

    if bot.chat_history:
        bot.chat_history[0] = {"role": "system", "content": new_content, "msg_ids": [], "is_command": False}
        display_name = format_role_display(bot.current_role)
        await reply_transient(update, context, f"🔄 Role `{display_name}` reloaded.")
    else:
        bot.chat_history = [{"role": "system", "content": new_content, "msg_ids": [], "is_command": False}]
        display_name = format_role_display(bot.current_role)
        await reply_transient(update, context, f"✨ Role `{display_name}` reloaded.")

async def r_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
   """Alias for role_command."""
   await role_command(update, context) 

async def role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lists available roles or switches to a new one.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    role_files = sorted(list(ROLES_DIR.glob("*.txt")))
    
    if not context.args:
        text = f"🎭 **Available Roles (Bot: {bot.name}):**\n"
        for i, f in enumerate(role_files):
            r_stem = f.stem
            is_active = (r_stem == bot.current_role)
            owner = tracker.get_chat_owner(r_stem)
            if is_active:
                indicator = "✅"
            elif owner and owner != bot.name:
                indicator = f"🔒 (in use by {owner})"
            else:
                indicator = ""
            display_name = format_role_display(r_stem)
            text += f"{i+1}. `{display_name}` {indicator}\n"
        await reply_and_log(update, text, bot=bot)
        return

    # Handle .role i - list all roles with their images (same as .role image)
    if context.args[0].lower() in ["i", "image"]:
        await list_roles_with_images(update, context)
        return

    # Handle .role save [name] [text]
    if len(context.args) >= 3 and context.args[0].lower() == "save":
        target = context.args[1]
        content = " ".join(context.args[2:])
        (ROLES_DIR / f"{target}.txt").write_text(content, encoding='utf-8')
        await reply_and_log(update, f"💾 Role `{target}` saved.", bot=bot)
        return

    # Handle .role edit [n]
    if len(context.args) >= 2 and context.args[0].lower() == "edit":
        try:
            idx = int(context.args[1]) - 1
            selected_file = role_files[idx]
            name = selected_file.stem
            content = selected_file.read_text(encoding='utf-8').strip()
            text = f".role save {name} {content}"
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i+4000])
            return
        except Exception:
            await reply_and_log(update, "❌ Use `.role edit [number]`", bot=bot)
            return

    try:
        idx = int(context.args[0]) - 1
        selected_file = role_files[idx]
        target_role = selected_file.stem

        success, err = await bot.switch_role(target_role)
        if not success:
            await reply_and_log(update, f"❌ {err}", bot=bot)
            return

        display_name = format_role_display(bot.current_role)
        await reply_transient(update, context, f"✅ Switched to `{display_name}`.")
        await show_role_image(update, context, bot.current_role)
    except:
        await reply_and_log(update, "❌ Use `.role [number]`", bot=bot)


async def show_role_image(update: Update, context: ContextTypes.DEFAULT_TYPE, role_name: str):
    """Display the image associated with a role, if it exists."""
    bot = get_bot(context, update)
    for ext in [".jpg", ".jpeg", ".png"]:
        img_path = ROLE_IMAGES_DIR / f"{role_name}{ext}"
        if img_path.exists():
            try:
                with open(img_path, 'rb') as photo:
                    await update.message.reply_photo(photo=photo)
                return
            except Exception as e:
                print(f"⚠️ Failed to send role image: {e}")
    display_name = format_role_display(role_name)


async def show_persona_image(update: Update, context: ContextTypes.DEFAULT_TYPE, persona_name: str):
    """Display the image associated with a persona, if it exists."""
    bot = get_bot(context, update)
    for ext in [".jpg", ".jpeg", ".png"]:
        img_path = CHAT_PERSONAS_DIR / f"{persona_name}{ext}"
        if img_path.exists():
            try:
                with open(img_path, 'rb') as photo:
                    await update.message.reply_photo(photo=photo)
                return
            except Exception as e:
                print(f"⚠️ Failed to send persona image: {e}")
    # No image found
    return


async def list_personas_with_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all personas with their images - name then image for each."""
    bot = get_bot(context, update)
    persona_files = sorted(list(CHAT_PERSONAS_DIR.glob("*.txt")))
    
    if not persona_files:
        await reply_and_log(update, "📭 No personas found in chats/personas/", bot=bot)
        return
    
    await reply_and_log(update, f"👤 **Available Personas (Bot: {bot.name}):**", bot=bot)
    
    for i, f in enumerate(persona_files):
        p_stem = f.stem
        is_active = (p_stem == bot.current_persona)
        display_name = format_role_display(p_stem)
        indicator = "✅" if is_active else ""
        
        # Send the persona name/number
        await reply_and_log(update, f"{i+1}. `{display_name}` {indicator}", bot=bot)
        
        # Send the image immediately after
        await show_persona_image(update, context, p_stem)


async def persona_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lists available personas or switches to a new one.
    Usage:
      .persona              - List all personas
      .persona [number]     - Switch to persona by number
      .persona i / image    - List personas with images
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    persona_files = sorted(list(CHAT_PERSONAS_DIR.glob("*.txt")))

    if not context.args:
        text = f"👤 **Available Personas (Bot: {bot.name}):**\n"
        for i, f in enumerate(persona_files):
            p_stem = f.stem
            is_active = (p_stem == bot.current_persona)
            display_name = format_role_display(p_stem)
            indicator = "✅" if is_active else ""
            text += f"{i+1}. `{display_name}` {indicator}\n"
        if not persona_files:
            text += "\nNo personas found. Create .txt files in chats/personas/"
        await reply_and_log(update, text, bot=bot)
        return

    # Handle .persona i / image - show personas with images
    if context.args[0].lower() in ["i", "image"]:
        await list_personas_with_images(update, context)
        return

    # Handle .persona clear - clear current persona
    if context.args[0].lower() in ["clear", "none", "reset"]:
        success, err = await bot.switch_persona("")
        if success:
            await reply_transient(update, context, "✅ Persona cleared.")
        else:
            await reply_and_log(update, f"❌ {err}", bot=bot)
        return

    try:
        idx = int(context.args[0]) - 1
        selected_file = persona_files[idx]
        target_persona = selected_file.stem

        success, err = await bot.switch_persona(target_persona)
        if not success:
            await reply_and_log(update, f"❌ {err}", bot=bot)
            return

        display_name = format_role_display(bot.current_persona)
        await reply_transient(update, context, f"✅ Switched to persona `{display_name}`.")
        await show_persona_image(update, context, bot.current_persona)
    except:
        await reply_and_log(update, "❌ Use `.persona [number]`, `.persona i`, or `.persona clear`", bot=bot)
    await reply_and_log(update, f"🖼️ No image found for role `{display_name}`.", bot=bot)


async def list_roles_with_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available roles with their images (each role name shown with its image as caption)."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    role_files = sorted(list(ROLES_DIR.glob("*.txt")))
    
    if not role_files:
        await reply_and_log(update, "📭 No roles found.", bot=bot)
        return

    # Send each role with its image as caption (no separate text list)
    for i, f in enumerate(role_files):
        display_name = format_role_display(f.stem)
        active = "✅ " if f.stem == bot.current_role else ""
        caption = f"{i+1}. {active}`{display_name}`"
        
        # Check for image
        image_found = False
        for ext in [".jpg", ".jpeg", ".png", ".webp"]:
            img_path = ROLE_IMAGES_DIR / f"{f.stem}{ext}"
            if img_path.exists():
                try:
                    with open(img_path, 'rb') as photo:
                        await update.message.reply_photo(photo=photo, caption=caption)
                    image_found = True
                    break
                except Exception as e:
                    print(f"⚠️ Failed to send role image for {f.stem}: {e}")
        
        if not image_found:
            await reply_and_log(update, f"🖼️ {caption} — *No image*", bot=bot)


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the current role's image.
    Usage: .image
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    
    bot = get_bot(context, update)
    await show_role_image(update, context, bot.current_role)

async def chat_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles chat-related actions: .clear, .chat save, and .chat load.
    Providing no arguments lists saved chats.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    first_word = update.message.text.lower().split()[0] if update.message.text else ""
    is_clear = first_word in [".clear", "clear"]
    is_clean = first_word in [".clean", ".cl", "clean", "cl"]

    if is_clear or is_clean:
        if is_clean:
            chat_id = update.effective_chat.id
            # Physically delete messages from Telegram UI (Bottom-to-Top)
            for msg in reversed(bot.chat_history[1:]):
                for mid in reversed(msg.get("msg_ids", [])):
                    try: await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                    except: pass

        # Reset history to only the system prompt
        if bot.chat_history and bot.chat_history[0]["role"] == "system":
            bot.chat_history = [bot.chat_history[0]]
        else:
            bot.chat_history = [{"role": "system", "content": get_role_content(bot.current_role), "msg_ids": [], "is_command": False}]

        bot.save_active_context_and_status()

        notice = "🧹 Chat and UI cleaned." if is_clean else "✨ Memory cleared."
        await reply_transient(update, context, notice)
        return

    if not context.args:
        files = sorted(CHAT_SESSIONS_DIR.glob("*.json"))
        if not files:
            await update.message.reply_text("📂 No saved chats found.")
            return
        
        chat_names = [f.stem for f in files if "last_session" not in f.name]
        text = f"📂 **Saved Chats on Disk (Bot: {bot.name}):**\n" + "\n".join([f"• `{name}`" for name in chat_names])
        await reply_and_log(update, text, bot=bot)
        return

    cmd = context.args[0].lower()
    if cmd == "save" and len(context.args) > 1:
        target_name = context.args[1]
        bot.save_session(target_name)
        ctx_file = CHAT_CONTEXT_DIR / f"{target_name}.txt"
        ctx_file.write_text(format_context_text(bot.chat_history), encoding='utf-8')
        st_file = CHAT_CONFIG_DIR / f"{target_name}.status"
        with open(st_file, 'w', encoding='utf-8') as f:
            json.dump(get_bot_status_dict(bot), f, indent=4, ensure_ascii=False)
        await reply_and_log(update, f"💾 Saved as `{target_name}`", bot=bot)
    elif cmd == "load" and len(context.args) > 1:
        target_name = context.args[1]
        if tracker.is_chat_in_use(target_name, bot.name):
            owner = tracker.get_chat_owner(target_name)
            await reply_and_log(update, f"❌ Chat `{target_name}` is currently in use by bot `{owner}`. No two bots can use the same role or chat.", bot=bot)
            return

        bot.save_current_context()
        bot.save_current_status()

        loaded = bot.load_session(target_name)
        if not loaded:
            ctx_file = CHAT_CONTEXT_DIR / f"{target_name}.txt"
            if ctx_file.exists():
                bot.current_role = target_name
                bot.load_role_context_and_status(target_name)
                loaded = True

        if loaded:
            tracker.claim_chat(target_name, bot.name)
            bot.save_session("last_session")
            await reply_and_log(update, f"📂 Loaded `{target_name}`", bot=bot)
            
            recent = bot.chat_history[1:][-5:]
            if recent:
                await reply_and_log(update, "📜 **Recent History Preview:**", bot=bot)
                for msg in recent:
                    icon = "👤" if msg.get('role') == 'user' else "🤖"
                    text = f"{icon} {msg.get('content', '')}"
                    await reply_and_log(update, text, is_command=True, bot=bot)
        else:
            await reply_and_log(update, f"❌ Could not load chat `{target_name}`", bot=bot)

async def resend_last_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Finds the most recent user message in history, removes it, and
    re-triggers the handle_message flow. Useful if the bot failed or
    if settings were changed.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if len(bot.chat_history) < 2:
        await reply_and_log(update, "❌ No message history to resend.", bot=bot)
        return

    last_user_msg = None
    # Look backwards to find the most recent 'user' entry
    for i in range(len(bot.chat_history) - 1, -1, -1):
        if bot.chat_history[i]["role"] == "user":
            last_user_msg = bot.chat_history[i]["content"]
            # Remove it so handle_message doesn't create a duplicate in history
            msg_to_resend = bot.chat_history.pop(i)
            # Also remove and physically delete the bot's following response if it exists
            if i < len(bot.chat_history) and bot.chat_history[i]["role"] == "assistant":
                bot_msg = bot.chat_history.pop(i)
                for mid in bot_msg.get("msg_ids", []):
                    try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
                    except: pass
            break
            
    if last_user_msg:
        bot.save_session("last_session")
        await handle_message(update, context, incoming_text=last_user_msg)
    else:
        await reply_and_log(update, "❌ Could not find a user message to resend.", bot=bot)

async def toggle_lazy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles 'Lazy Mode' allowing commands to work without the leading dot."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    bot.lazy_mode = not bot.lazy_mode
    bot.save_active_context_and_status()
    status = "ON 😴 (Commands work without dots)" if bot.lazy_mode else "OFF ⚡ (Dots required)"
    await reply_and_log(update, f"Lazy Mode: {status}", bot=bot)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, incoming_text=None):
    """
    The primary message handler for LLM interactions.
    Constructs the payload (System Prompt + History Slicing), selects the
    correct provider client, and streams the response back to Telegram.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    start_time = time.time()
    
    # Use the provided text (for resends) or the actual message text
    prompt = incoming_text if incoming_text else update.message.text

    if not prompt:
        return

    # --- LAZY MODE COMMAND CHECK ---
    if bot.lazy_mode and not prompt.startswith("."):
        parts = prompt.split()
        if parts:
            cmd_name = parts[0].lower()
            if cmd_name in LAZY_COMMAND_MAP:
                # Simulate PrefixHandler args and execute the mapped function
                context.args = parts[1:]
                await LAZY_COMMAND_MAP[cmd_name](update, context)
                return # Stop here; do not send to LLM

    if not bot.chat_history:
        bot.chat_history = [{"role": "system", "content": get_role_content(bot.current_role), "msg_ids": [], "is_command": False}]
    
    # Build the dynamic system prompt including active characters
    role_content = get_role_content(bot.current_role)
    char_blocks = []
    for char_name in bot.active_characters:
        content = get_character_content(char_name, bot.active_story)
        if content:
            char_blocks.append(f"--- CHARACTER: {char_name} ---\n{content}")

    scene_blocks = []
    for scene_name in bot.active_scenes:
        # In story mode with active story, use story elements; otherwise use legacy scenes
        if bot.current_mode == "story" and bot.active_story:
            content = get_element_content(scene_name, bot.active_story)
        else:
            content = get_scene_content(scene_name)
        if content:
            scene_blocks.append(f"--- SCENE/LOCATION: {scene_name} ---\n{content}")
    
    full_system_prompt = role_content
    if char_blocks:
        full_system_prompt += "\n\nACTIVE CHARACTERS IN SCENE:\n" + "\n\n".join(char_blocks)
    
    if scene_blocks:
        full_system_prompt += "\n\nCURRENT SCENE SETTING:\n" + "\n\n".join(scene_blocks)

    # Track ID for the user's incoming prompt
    user_entry = {"role": "user", "content": prompt, "is_command": False}
    if not incoming_text: 
        user_entry["msg_ids"] = [update.message.message_id]
    bot.chat_history.append(user_entry)
    bot.save_active_context_and_status()
    
    history_subset = bot.chat_history[1:][-bot.context_limit:]

    if bot.current_mode == "story":
        # Filter: Only narrative assistant responses (not command outputs)
        narrative_assistants = [m for m in history_subset[:-1] if m["role"] == "assistant" and not m.get("is_command")]
        payload = [{"role": "system", "content": full_system_prompt}] + narrative_assistants + [history_subset[-1]]
    else:
        # Chat mode: AI sees history, but we still exclude meta-command logs
        clean_history = [m for m in history_subset if not m.get("is_command")]
        payload = [{"role": "system", "content": full_system_prompt}] + clean_history

    # Sanitize payload: LLMs only want 'role' and 'content'
    api_payload = [{"role": m["role"], "content": m["content"]} for m in payload]

    if bot.trace_mode:
        try:
            trace_file = CONTEXT_DIR / f"{bot.name}_last_payload.json"
            with open(trace_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Trace failed: {e}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Use centralized helper
        reply = await generate_response(api_payload, bot=bot)

        if bot.trace_mode:
            try:
                resp_file = CONTEXT_DIR / f"{bot.name}_last_response.json"
                with open(resp_file, 'w', encoding='utf-8') as f:
                    json.dump({"response": reply, "provider": bot.current_provider, "model": bot.current_model}, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Response Trace failed: {e}")

        bot.last_latency = round(time.time() - start_time, 2)
        bot.total_messages_sent += 1

        sent_ids = []
        for i in range(0, len(reply), 4000):
            sent_msg = await update.message.reply_text(reply[i:i+4000], parse_mode='Markdown')
            sent_ids.append(sent_msg.message_id)

        bot.chat_history.append({"role": "assistant", "content": reply, "msg_ids": sent_ids, "is_command": False})
        bot.save_active_context_and_status()

        if bot.verbose_mode:
            minutes = int(bot.last_latency // 60)
            seconds = int(bot.last_latency % 60)
            latency_str = f"{minutes}m{seconds}s" if minutes > 0 else f"{seconds}s"
            history_json = json.dumps(bot.chat_history)
            size_kb = int(len(history_json.encode('utf-8')) / 1024)
            v_text = f"Status: {latency_str}, {len(payload)}msg, {size_kb}kb"
            await update.message.reply_text(v_text, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        
async def toggle_think(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles 'Think Mode' for Ollama models (adds <think> logic to options)."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    bot.think_mode = not bot.think_mode
    bot.save_active_context_and_status()
    await reply_transient(update, context, f"Think Mode: {'ON 🧠' if bot.think_mode else 'OFF ⚡'}")

async def llmcontext_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets or shows the LLM context window (num_ctx). Requires 'k' suffix."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if not context.args:
        ctx_val = bot.generation_options.get("num_ctx", 4096)
        await reply_and_log(update, f"🧠 Model Context Window (num_ctx): `{ctx_val}`", bot=bot)
        return

    val = context.args[0].lower()
    if not val.endswith("k"):
        await reply_and_log(update, "❌ Error: Context must be specified with 'k' (e.g., 8k, 32k, 128k).", bot=bot)
        return

    try:
        num = int(val[:-1]) * 1024
        bot.generation_options["num_ctx"] = num
        bot.save_active_context_and_status()
        await reply_transient(update, context, f"✅ Model context window set to `{num}` ({val}).")
    except ValueError:
        await reply_and_log(update, "❌ Error: Invalid number format. Use e.g., 8k.", bot=bot)

async def toggle_verbose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the display of metadata (latency, size) after each message."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    bot.verbose_mode = not bot.verbose_mode
    bot.save_active_context_and_status()
    await reply_transient(update, context, f"Verbose Mode: {'ON ✅' if bot.verbose_mode else 'OFF ❌'}")

async def toggle_trace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles 'Trace Mode' to write LLM payloads to the context folder."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if context.args:
        arg = context.args[0].lower()
        if arg == "on":
            bot.trace_mode = True
        elif arg == "off":
            bot.trace_mode = False
    else:
        bot.trace_mode = not bot.trace_mode
        
    bot.save_active_context_and_status()
    await reply_transient(update, context, f"Trace Mode: {'ON 📝' if bot.trace_mode else 'OFF ❌'}")

async def s_command(update: Update, context: ContextTypes.DEFAULT_TYPE):   
    await status_command(update, context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays current configuration and session statistics."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    
    bot = get_bot(context, update)
    history_json = json.dumps(bot.chat_history)
    size_kb = round(len(history_json.encode('utf-8')) / 1024, 1)
    display = provider_display_info.get(bot.current_provider, bot.current_provider)
    display_name = format_role_display(bot.current_role)
    persona_display = format_role_display(bot.current_persona) if bot.current_persona else "None"
    status_text = (
        f"📊 **Status (Bot: {bot.name})**\n---\n"
        f"🌐 Provider: `{display}`\n"
        f"🧠 Model: `{bot.current_model}`\n"
        f"🎭 Role: `{display_name}`\n"
        f"👤 Persona: `{persona_display}`\n"
        f"📖 Mode: `{bot.current_mode}`\n"
        f"📝 Trace Mode: `{'ON' if bot.trace_mode else 'OFF'}`\n"
        f"😴 Lazy Mode: `{'ON' if bot.lazy_mode else 'OFF'}`\n"
        f"👥 Active Chars: `{', '.join(bot.active_characters) if bot.active_characters else 'None'}`\n"
        f"🎬 Active Scenes: `{', '.join(bot.active_scenes) if bot.active_scenes else 'None'}`\n"
        f"💾 Context: `{len(bot.chat_history)}` msgs `{size_kb}KB` max `{bot.context_limit}`\n"
        f"🧠 LLM Ctx: `{bot.generation_options['num_ctx']}`"
    )
    await reply_and_log(update, status_text, bot=bot)

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches between chat mode (full history) and story mode (assistant only history)."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if not context.args:
        await reply_and_log(update, f"📖 Current Mode: `{bot.current_mode}`\nUsage: `.mode [chat/story]`", bot=bot)
        return

    new_mode = context.args[0].lower()
    if new_mode in ["chat", "story"]:
        bot.save_current_context()
        bot.save_current_status()
        bot.current_mode = new_mode
        if new_mode == "chat":
            bot.load_role_context_and_status(bot.current_role)
            if bot.current_persona:
                bot.load_persona_context_and_status(bot.current_persona)
        elif new_mode == "story" and bot.active_story:
            bot.load_story_context_and_status(bot.active_story)
        bot.save_session("last_session")
        await reply_transient(update, context, f"✅ Mode switched to `{bot.current_mode}`.")
    else:
        await reply_and_log(update, "❌ Invalid mode. Use `.mode chat` or `.mode story`.", bot=bot)

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Asks a question about the conversation history.
    Does not include roles, characters, or scenes.
    The question and result are not saved to context.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if not context.args:
        await reply_and_log(update, "❌ Usage: `.ask [question]`", bot=bot)
        return

    question = " ".join(context.args)
    history_subset = bot.chat_history[1:][-bot.context_limit:]
    payload = [{"role": "system", "content": "You are a helpful assistant. Use the conversation history provided to answer the user's question accurately. Do not adopt any persona."}]
    payload.extend(history_subset)
    payload.append({"role": "user", "content": question})

    api_payload = [{"role": m["role"], "content": m["content"]} for m in payload]

    if bot.trace_mode:
        try:
            trace_file = CONTEXT_DIR / f"{bot.name}_ask_payload.json"
            with open(trace_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Trace failed: {e}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        reply = await generate_response(api_payload, bot=bot)

        if bot.trace_mode:
            try:
                resp_file = CONTEXT_DIR / f"{bot.name}_ask_response.json"
                with open(resp_file, 'w', encoding='utf-8') as f:
                    json.dump({"response": reply, "provider": bot.current_provider, "model": bot.current_model}, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Response Trace failed: {e}")

        header = f"❓ **Ask:** {question}\n\n💡 **Answer:**\n"
        full_response = header + reply
        await reply_and_log(update, full_response, is_command=True, bot=bot)
    except Exception as e:
        await reply_and_log(update, f"❌ Ask failed: {e}", bot=bot)

async def delete_last_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Removes the last assistant response and the last user message
    from the active context.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    
    bot = get_bot(context, update)
    if len(bot.chat_history) < 2:
        await reply_and_log(update, "❌ Not enough messages to delete.", bot=bot)
        return

    chat_id = update.effective_chat.id
    for _ in range(2):
        if len(bot.chat_history) > 1:
            msg = bot.chat_history.pop()
            for mid in msg.get("msg_ids", []):
                try: await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                except: pass

    bot.save_active_context_and_status()
    await reply_and_log(update, "🗑️ Deleted last entries from history and chat.", bot=bot)

async def set_context_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets how many history messages are sent to the LLM per prompt."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if not context.args:
        await reply_and_log(update, f"📊 Current context limit: `{bot.context_limit}` messages", bot=bot)
        return
    
    try:
        new_limit = int(context.args[0])
        if new_limit < 1:
            await reply_and_log(update, "❌ Context limit must be at least 1.", bot=bot)
            return
        bot.context_limit = new_limit
        bot.save_active_context_and_status()
        await reply_transient(update, context, f"✅ Context limit changed to `{bot.context_limit}` messages.")
    except ValueError:
        await reply_and_log(update, "❌ Usage: `.context [number]`", bot=bot)

async def role_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates a summary of the current role based on instructions in roles_recap.json."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    index = 0
    if context.args:
        try:
            index = int(context.args[0]) - 1
        except ValueError:
            await reply_and_log(update, "❌ Usage: `.rs [n]` where n is a number.", bot=bot)
            return

    recap_file = SETTINGS_DIR / "roles_recap.json"
    if not recap_file.exists():
        await reply_and_log(update, f"❌ `roles_recap.json` not found in `{SETTINGS_DIR}`", bot=bot)
        return

    try:
        with open(recap_file, 'r', encoding='utf-8') as f:
            instructions = json.load(f)
        
        if not (0 <= index < len(instructions)):
            await reply_and_log(update, f"❌ Index {index + 1} out of range. (Available: 1-{len(instructions)})", bot=bot)
            return
        
        instruction_text = instructions[index]
    except json.JSONDecodeError as e:
        await reply_and_log(update, f"❌ Error parsing `roles_recap.json`: {e}", bot=bot)
        return

    role_content = get_role_content(bot.current_role)
    prompt = f"{instruction_text}\n\nROLE PROFILE CONTENT:\n{role_content}"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        summary = await generate_response([{"role": "user", "content": prompt}], bot=bot)
        display_name = format_role_display(bot.current_role)
        await reply_and_log(update, f"🎭 **Role Summary: {display_name}**\n\n{summary}", bot=bot)
    except Exception as e:
        await reply_and_log(update, f"❌ Role summary failed: {e}", bot=bot)

async def story_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles story commands.
    Subcommands:
    - save [name]
    - select [name]
    - image / i : list stories with their images
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if not context.args:
        await reply_and_log(update, "❌ Usage: `.story save [name]` or `.story select [name]`", bot=bot)
        return

    subcommand = context.args[0].lower()

    # New subcommand to list story images
    if subcommand in ["image", "i"]:
        await list_story_images(update, context)
        return

    if subcommand == "save":
        if len(context.args) < 2:
            await reply_and_log(update, "❌ Usage: `.story save [name]`", bot=bot)
            return
        story_name = context.args[1]
        assistant_content = [msg['content'] for msg in bot.chat_history if msg.get('role') == 'assistant']
        if not assistant_content:
            await reply_and_log(update, "ℹ️ No assistant content found to extract.", bot=bot)
            return
        file_path = STORIES_DIR / f"{story_name}.txt"
        try:
            file_path.write_text("\n\n".join(assistant_content), encoding='utf-8')
            await reply_and_log(update, f"✅ Story extracted to `{file_path.name}`", bot=bot)
        except Exception as e:
            await reply_and_log(update, f"❌ Failed to save story: {e}", bot=bot)
        return
    elif subcommand == "select":
        if len(context.args) == 1:
            story_dirs = sorted([d for d in STORIES_DIR.iterdir() if d.is_dir()])
            if not story_dirs:
                await reply_and_log(update, "📂 No story containers found in `chats_stories/stories/`.", bot=bot)
                return
            text = f"📖 **Available Stories (Bot: {bot.name}):**\n---\n"
            for i, story_dir in enumerate(story_dirs):
                s_name = story_dir.name
                current_marker = " ← CURRENT" if s_name == bot.active_story else ""
                owner = tracker.get_story_owner(s_name)
                if owner and owner != bot.name:
                    current_marker = f" 🔒 (in use by {owner})"
                text += f"{i+1}. `{s_name}`{current_marker}\n"
            text += "\nUse `.story select <number/name>` to activate a story."
            await reply_and_log(update, text, bot=bot)
            return
        arg = context.args[1]
        story_dirs = sorted([d for d in STORIES_DIR.iterdir() if d.is_dir()])
        available_stories = [d.name for d in story_dirs]
        target_story = None
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(available_stories):
                target_story = available_stories[idx]
        elif arg in available_stories:
            target_story = arg
        if not target_story:
            await reply_and_log(update, "❌ Story not found.", bot=bot)
            return
        success, err = await bot.switch_story(target_story)
        if not success:
            await reply_and_log(update, f"❌ {err}", bot=bot)
            return
        await reply_and_log(update, f"📖 Active story set to: `{bot.active_story}`", bot=bot)
        return
    else:
        await reply_and_log(update, "❌ Unknown subcommand. Use `.story save [name]` or `.story select [name]`", bot=bot)
        return
    """
    Story management: save story extracts or select active story container.
    Usage: .story save [name]  - extracts assistant messages to story file
           .story select        - lists available stories
           .story select <name> - activates a story container
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if not context.args:
        await reply_and_log(update, "❌ Usage: `.story save [name]` or `.story select [name]`", bot=bot)
        return

    subcommand = context.args[0].lower()
    
    if subcommand == "save":
        if len(context.args) < 2:
            await reply_and_log(update, "❌ Usage: `.story save [name]`", bot=bot)
            return
        
        story_name = context.args[1]
        assistant_content = [msg['content'] for msg in bot.chat_history if msg.get('role') == 'assistant']

        if not assistant_content:
            await reply_and_log(update, "ℹ️ No assistant content found to extract.", bot=bot)
            return

        file_path = STORIES_DIR / f"{story_name}.txt"
        try:
            file_path.write_text("\n\n".join(assistant_content), encoding='utf-8')
            await reply_and_log(update, f"✅ Story extracted to `{file_path.name}`", bot=bot)
        except Exception as e:
            await reply_and_log(update, f"❌ Failed to save story: {e}", bot=bot)
        return
    
    elif subcommand == "select":
        if len(context.args) == 1:
            story_dirs = sorted([d for d in STORIES_DIR.iterdir() if d.is_dir()])
            if not story_dirs:
                await reply_and_log(update, "📂 No story containers found in `chats_stories/stories/`.", bot=bot)
                return
            
            text = f"📖 **Available Stories (Bot: {bot.name}):**\n---\n"
            for i, story_dir in enumerate(story_dirs):
                s_name = story_dir.name
                current_marker = " ← CURRENT" if s_name == bot.active_story else ""
                owner = tracker.get_story_owner(s_name)
                if owner and owner != bot.name:
                    current_marker = f" 🔒 (in use by {owner})"
                text += f"{i+1}. `{s_name}`{current_marker}\n"
            text += "\nUse `.story select <number/name>` to activate a story."
            await reply_and_log(update, text, bot=bot)
            return
        
        arg = context.args[1]
        story_dirs = sorted([d for d in STORIES_DIR.iterdir() if d.is_dir()])
        available_stories = [d.name for d in story_dirs]
        
        target_story = None
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(available_stories):
                target_story = available_stories[idx]
        elif arg in available_stories:
            target_story = arg
        
        if not target_story:
            await reply_and_log(update, "❌ Story not found.", bot=bot)
            return

        success, err = await bot.switch_story(target_story)
        if not success:
            await reply_and_log(update, f"❌ {err}", bot=bot)
            return

        await reply_and_log(update, f"📖 Active story set to: `{bot.active_story}`", bot=bot)
        return
    
    else:
        await reply_and_log(update, "❌ Unknown subcommand. Use `.story save [name]` or `.story select [name]`", bot=bot)

async def roles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists available roles. Only works in chat mode."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if bot.current_mode != "chat":
        await reply_and_log(update, "❌ `.roles` only works in chat mode. Use `.mode chat` to switch.", bot=bot)
        return

    role_files = sorted(list(ROLES_DIR.glob("*.txt")))
    
    if not role_files:
        await reply_and_log(update, "📭 No roles found.", bot=bot)
        return

    text = f"🎭 **Available Roles (Bot: {bot.name}):**\n---\n"
    for i, f in enumerate(role_files):
        r_stem = f.stem
        is_active = (r_stem == bot.current_role)
        owner = tracker.get_chat_owner(r_stem)
        if is_active:
            indicator = " ✅"
        elif owner and owner != bot.name:
            indicator = f" 🔒 (in use by {owner})"
        else:
            indicator = ""
        display_name = format_role_display(r_stem)
        text += f"{i+1}. `{display_name}`{indicator}\n"
    
    text += "\nUse `.role [number/name]` to switch roles."
    await reply_and_log(update, text, bot=bot)


async def stories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists available stories with thumbnails. Only works in story mode."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if bot.current_mode != "story":
        await reply_and_log(update, "❌ `.stories` only works in story mode. Use `.mode story` to switch.", bot=bot)
        return

    # Handle subcommands: image, i
    if context.args and context.args[0].lower() in ["image", "i"]:
        await list_story_images(update, context)
        return

    story_dirs = sorted([d for d in STORIES_DIR.iterdir() if d.is_dir()])
    
    if not story_dirs:
        await reply_and_log(update, "📂 No story containers found in `chats_stories/stories/`.", bot=bot)
        return

    text = f"📖 **Available Stories (Bot: {bot.name}):**\n---\n"
    for i, story_dir in enumerate(story_dirs):
        s_name = story_dir.name
        display_name = format_story_display(s_name)
        current_marker = " ← CURRENT" if s_name == bot.active_story else ""
        owner = tracker.get_story_owner(s_name)
        if owner and owner != bot.name:
            current_marker = f" 🔒 (in use by {owner})"
        text += f"{i+1}. `{display_name}`{current_marker}\n"
    
    text += "\nUse `.st [number]` or `.story select [number/name]` to activate a story."
    text += "\nUse `.stories image` or `.stories i` to view story thumbnails."
    await reply_and_log(update, text, bot=bot)


async def st_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alias for stories command. With a number argument, selects the story."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if bot.current_mode != "story":
        await reply_and_log(update, "❌ `.st` only works in story mode. Use `.mode story` to switch.", bot=bot)
        return

    # If no args, delegate to stories_command to list stories
    if not context.args:
        await stories_command(update, context)
        return

    # If first arg is "image" or "i", show thumbnails
    if context.args[0].lower() in ["image", "i"]:
        await list_story_images(update, context)
        return

    # Otherwise, try to select story by number or name
    story_dirs = sorted([d for d in STORIES_DIR.iterdir() if d.is_dir()])
    available_stories = [d.name for d in story_dirs]
    
    if not available_stories:
        await reply_and_log(update, "📂 No story containers found in `chats_stories/stories/`.", bot=bot)
        return

    arg = context.args[0]
    target_story = None
    
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(available_stories):
            target_story = available_stories[idx]
    elif arg in available_stories:
        target_story = arg

    if not target_story:
        await reply_and_log(update, "❌ Story not found.", bot=bot)
        return

    success, err = await bot.switch_story(target_story)
    if not success:
        await reply_and_log(update, f"❌ {err}", bot=bot)
        return

    await reply_and_log(update, f"📖 Active story set to: `{bot.active_story}`", bot=bot)


async def list_story_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available stories with their images (each story shown with its image as caption)."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    story_dirs = sorted([d for d in STORIES_DIR.iterdir() if d.is_dir()])
    
    if not story_dirs:
        await reply_and_log(update, "📭 No stories found.", bot=bot)
        return

    # Send each story with its image as caption
    for i, story_dir in enumerate(story_dirs):
        s_name = story_dir.name
        display_name = format_story_display(s_name)
        current_marker = "✅ " if s_name == bot.active_story else ""
        caption = f"{i+1}. {current_marker}`{display_name}`"
        
        # Check for image in story directory (thumb.png/jpg/webp is the standard story thumbnail)
        image_found = False
        for filename in ["thumb"]:
            for ext in [".png", ".jpg", ".jpeg", ".webp"]:
                img_path = story_dir / f"{filename}{ext}"
                if img_path.exists():
                    try:
                        with open(img_path, 'rb') as photo:
                            await update.message.reply_photo(photo=photo, caption=caption)
                        image_found = True
                        break
                    except Exception as e:
                        print(f"⚠️ Failed to send story image for {s_name}: {e}")
            if image_found:
                break
        
        # Also check for image in a common images subdirectory
        if not image_found:
            images_dir = story_dir / "images"
            if images_dir.exists():
                for ext in [".jpg", ".jpeg", ".png", ".webp"]:
                    for img_file in images_dir.glob(f"*{ext}"):
                        try:
                            with open(img_file, 'rb') as photo:
                                await update.message.reply_photo(photo=photo, caption=caption)
                            image_found = True
                            break
                        except Exception as e:
                            print(f"⚠️ Failed to send story image for {s_name}: {e}")
                if image_found:
                    break
        
        if not image_found:
            await reply_and_log(update, f"🖼️ {caption} — *No image*", bot=bot)


async def recap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Generates a summary of the current conversation history using a 
    pre-defined prompt instruction from recap.json.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if len(bot.chat_history) < 2:
        await reply_and_log(update, "❌ No conversation history to recap.", bot=bot)
        return

    history_to_recap = bot.chat_history[1:][-bot.context_limit:]
    
    index = 0
    if context.args:
        try:
            index = int(context.args[0]) - 1
        except ValueError:
            await reply_and_log(update, "❌ Usage: `.recap [n]` where n is a number.", bot=bot)
            return

    recap_file = SETTINGS_DIR / "recap.json"
    if not recap_file.exists():
        await reply_and_log(update, f"❌ `recap.json` not found in `{SETTINGS_DIR}`", bot=bot)
        return

    try:
        with open(recap_file, 'r', encoding='utf-8') as f:
            instructions = json.load(f)
        
        if not (0 <= index < len(instructions)):
            await reply_and_log(update, f"❌ Index {index + 1} out of range. (Available: 1-{len(instructions)})", bot=bot)
            return
        
        instruction_text = instructions[index]
    except Exception as e:
        await reply_and_log(update, f"❌ Error reading `recap.json`: {e}", bot=bot)
        return

    formatted_history = "\n".join([f"{msg['role'].upper()}: {msg['content']}" for msg in history_to_recap])
    recap_prompt = f"{instruction_text}\n\n{formatted_history}"

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        summary = await generate_response([{"role": "user", "content": recap_prompt}], bot=bot)
        text = f"📝 **Conversation Recap (Last {len(history_to_recap)} msgs):**\n\n{summary}"
        await reply_and_log(update, text, bot=bot)
    except Exception as e:
        await reply_and_log(update, f"❌ Recap failed: {e}", bot=bot)

def extract_gist_id(input_str: str) -> str:
    """
    Extract gist ID from various input formats:
    - Plain gist ID: 30dd3afbbcbf2c42eb504f9361b41860
    - Full URL: https://gist.github.com/psychomate/30dd3afbbcbf2c42eb504f9361b41860
    - Full URL without username: https://gist.github.com/30dd3afbbcbf2c42eb504f9361b41860
    - Raw URL: https://gist.githubusercontent.com/psychomate/30dd3afbbcbf2c42eb504f9361b41860/raw/...
    """
    import re
    # Pattern to match gist ID (32+ hex characters)
    gist_id_pattern = r'([a-f0-9]{32,})'
    
    # Try to extract from URL first
    match = re.search(gist_id_pattern, input_str)
    if match:
        return match.group(1)
    
    # Fallback: return as-is (might be just the ID)
    return input_str.strip()


async def install_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Installs a role or story from a GitHub Gist using the GitHub API.
    Usage: .install <gist_id_or_url>
    Detects GOCHATAPPROLETAG_* for roles and GOCHATAPPSTORYTAG_* for stories.
    Accepts full gist URLs or just the gist ID.
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    if not context.args:
        await reply_and_log(update, "❌ Usage: `.install <gist_id_or_url>`")
        return

    # Join all args in case URL was split (though Telegram usually keeps it as one arg)
    input_str = " ".join(context.args).strip()
    gist_id = extract_gist_id(input_str)
    
    # Get GitHub token from config for higher rate limits (5000/hr vs 60/hr)
    github_token = CONFIG.get("GITHUB_TOKEN")
    
    status_msg = await update.message.reply_text(f"⬇️ Fetching gist `{gist_id}` via GitHub API...")
    
    # Build headers with optional auth
    api_headers = {"User-Agent": "Vivid-Chat-Bot"}
    if github_token and github_token != "your-github-token":
        api_headers["Authorization"] = f"token {github_token}"

    try:
        # Fetch gist metadata from GitHub API
        api_url = f"https://api.github.com/gists/{gist_id}"
        req = urllib.request.Request(api_url, headers=api_headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            gist_data = json.load(response)
        
        files = gist_data.get("files", {})
        if not files:
            await status_msg.edit_text("❌ No files found in gist.")
            return
        
        # Detect role or story tag
        role_tag_filename = None
        story_tag_filename = None
        for filename in files:
            if "GOCHATAPPROLETAG_" in filename:
                role_tag_filename = filename
            elif "GOCHATAPPSTORYTAG_" in filename:
                story_tag_filename = filename
        
        if not role_tag_filename and not story_tag_filename:
            await status_msg.edit_text("❌ No recognized tag found (GOCHATAPPROLETAG_* or GOCHATAPPSTORYTAG_*)")
            return
        
        # Download all files to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            gist_root = Path(tmpdir) / gist_id
            gist_root.mkdir()
            
            # Build headers for raw file downloads
            raw_headers = {"User-Agent": "Vivid-Chat-Bot"}
            if github_token and github_token != "your-github-token":
                raw_headers["Authorization"] = f"token {github_token}"
            
            for filename, file_info in files.items():
                raw_url = file_info.get("raw_url")
                if raw_url:
                    file_path = gist_root / filename
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        req = urllib.request.Request(raw_url, headers=raw_headers)
                        urllib.request.urlretrieve(raw_url, file_path)
                    except Exception as e:
                        print(f"Failed to download {filename}: {e}")
            
            # Process based on tag type
            if role_tag_filename:
                role_tag_file = gist_root / role_tag_filename
                await _install_role_from_gist(gist_id, gist_root, role_tag_file, status_msg, github_token)
            elif story_tag_filename:
                story_tag_file = gist_root / story_tag_filename
                await _install_story_from_gist(gist_id, gist_root, story_tag_file, status_msg, github_token)
    
    except urllib.error.HTTPError as e:
        if e.code == 404:
            await status_msg.edit_text(f"❌ Gist not found: `{gist_id}`")
        elif e.code == 403:
            await status_msg.edit_text(f"❌ GitHub API rate limit exceeded. Add GITHUB_TOKEN to config.yaml for higher limits (5000/hr vs 60/hr).")
        else:
            await status_msg.edit_text(f"❌ GitHub API error: {e.code} {e.reason}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Install failed: {e}")

async def _install_role_from_gist(gist_id, gist_root, role_tag_file, status_msg, github_token=None):
    """Helper to install a role from gist files."""
    # Extract role name from tag file (e.g., GOCHATAPPROLETAG_assistant_nsfw.txt -> assistant_nsfw)
    role_name = role_tag_file.stem.replace("GOCHATAPPROLETAG_", "")
    full_name = f"{role_name}!{gist_id}"
    
    # Read role content
    role_content = role_tag_file.read_text(encoding='utf-8')
    
    # Look for image files (thumb.md, image_prompt.md)
    thumb_md = None
    image_prompt_md = None
    for f in gist_root.rglob("*"):
        if f.is_file():
            if f.name == "thumb.md":
                thumb_md = f.read_text(encoding='utf-8')
            elif f.name == "image_prompt.md":
                image_prompt_md = f.read_text(encoding='utf-8')
    
    # Save role text
    role_file = CHAT_ROLES_DIR / f"{full_name}.txt"
    role_file.write_text(role_content, encoding='utf-8')
    
    # Build headers for image downloads
    img_headers = {'User-Agent': 'Mozilla/5.0'}
    if github_token and github_token != "your-github-token":
        img_headers['Authorization'] = f"token {github_token}"
    
    # Download and save images if thumb.md contains image URLs
    if thumb_md:
        img_urls = re.findall(r'src="([^"]+)"', thumb_md)
        for i, url in enumerate(img_urls):
            try:
                # Extract file extension from URL (default to .jpg)
                parsed_url = urllib.parse.urlparse(url)
                ext = os.path.splitext(parsed_url.path)[1]
                if not ext:
                    ext = ".jpg"
                img_name = f"{full_name}{ext}" if i == 0 else f"{full_name}_{i}{ext}"
                img_path = CHAT_ROLES_DIR / img_name
                
                # Use urllib with headers
                req = urllib.request.Request(url, headers=img_headers)
                with urllib.request.urlopen(req, timeout=30) as response:
                    img_path.write_bytes(response.read())
                print(f"✅ Downloaded role image: {img_name}")
            except Exception as e:
                print(f"⚠️ Failed to download role image from {url}: {e}")
    
    await status_msg.edit_text(f"✅ Role installed: `{full_name}.txt`\nUse `.role` to select it.")

async def _install_story_from_gist(gist_id, gist_root, story_tag_file, status_msg, github_token=None):
    """Helper to install a story from gist files."""
    # Read story metadata
    story_content = story_tag_file.read_text(encoding='utf-8')
    
    # Extract story name from tag file
    story_name = story_tag_file.stem.replace("GOCHATAPPSTORYTAG_", "")
    full_story_name = f"{story_name}!{gist_id}"
    
    # Create story directory
    story_dir = STORIES_DIR / full_story_name
    story_dir.mkdir(parents=True, exist_ok=True)
    
    # Create subdirectories
    (story_dir / "characters").mkdir(exist_ok=True)
    (story_dir / "storyelements").mkdir(exist_ok=True)
    (story_dir / "story_context").mkdir(exist_ok=True)
    (story_dir / "backups").mkdir(exist_ok=True)
    
    # Save story context (backstory)
    (story_dir / "story_context" / "context.txt").write_text(story_content, encoding='utf-8')
    
    # Build headers for image downloads
    img_headers = {'User-Agent': 'Mozilla/5.0'}
    if github_token and github_token != "your-github-token":
        img_headers['Authorization'] = f"token {github_token}"
    
    # Process all files
    # First pass: collect character names from character_*.txt files
    character_names = set()
    for f in gist_root.rglob("*"):
        if f.is_file() and f.name.startswith("character_") and f.suffix == ".txt":
            char_name = f.stem.replace("character_", "")
            character_names.add(char_name)
            (story_dir / "characters" / f"{char_name}.txt").write_text(f.read_text(encoding='utf-8'))
    
    # Second pass: process thumb files and other files
    for f in gist_root.rglob("*"):
        if not f.is_file():
            continue
        
        # Character thumb files - match both character_*_thumb.md and *_thumb.md for known characters
        if "thumb" in f.name and f.suffix == ".md" and f.name != "thumb.md":
            # Try to extract character name
            stem = f.stem.replace("_thumb", "")
            # Check if it matches a known character (with or without character_ prefix)
            char_name = None
            if stem in character_names:
                char_name = stem
            elif stem.startswith("character_") and stem[10:] in character_names:
                char_name = stem[10:]
            
            if char_name:
                thumb_content = f.read_text(encoding='utf-8')
                img_urls = re.findall(r'src="([^"]+)"', thumb_content)
                for url in img_urls:
                    try:
                        req = urllib.request.Request(url, headers=img_headers)
                        with urllib.request.urlopen(req, timeout=30) as response:
                            (story_dir / "characters" / f"{char_name}.jpg").write_bytes(response.read())
                    except Exception:
                        pass
        
        # Story element files
        elif f.name.startswith("storyelement_") and f.suffix == ".txt":
            elem_name = f.stem.replace("storyelement_", "")
            (story_dir / "storyelements" / f"{elem_name}.txt").write_text(f.read_text(encoding='utf-8'))
        
        # Story thumb
        elif f.name == "thumb.md":
            thumb_content = f.read_text(encoding='utf-8')
            img_urls = re.findall(r'src="([^"]+)"', thumb_content)
            for url in img_urls:
                try:
                    req = urllib.request.Request(url, headers=img_headers)
                    with urllib.request.urlopen(req, timeout=30) as response:
                        (story_dir / "thumb.jpg").write_bytes(response.read())
                except Exception:
                    pass
    
    await status_msg.edit_text(f"✅ Story installed: `{full_story_name}`\nUse `.story select {full_story_name}` to activate it.")

async def last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the last n messages from the chat history."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    n = 3
    if context.args:
        try:
            n = int(context.args[0])
        except ValueError:
            await reply_and_log(update, "❌ Usage: `.last [n]` where n is a number.", bot=bot)
            return

    if not bot.chat_history:
        await reply_and_log(update, "📜 History is empty.", bot=bot)
        return

    history_slice = bot.chat_history[-n:]
    text = f"📜 **Showing last {len(history_slice)} messages (Bot: {bot.name}):**\n\n"

    for msg in history_slice:
        role_icon = "👤" if msg['role'] == 'user' else "🤖"
        if msg['role'] == 'system': 
            role_icon = "⚙️"
        
        line = f"{role_icon} **{msg['role'].capitalize()}**:\n{msg['content']}\n\n"
        text += line

    await reply_and_log(update, text, bot=bot)

async def char_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manages story characters (only in story mode with active story).
    Usage: .char (list), .char [n/name] on/off, .char all off, .char save [name] [text], .char edit [n]
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if bot.current_mode != "story" or not bot.active_story:
        await reply_and_log(update, "❌ `.char` only works in story mode with an active story (`.story select <name>`).", bot=bot)
        return

    chars_dir = get_story_characters_dir(bot.active_story)
    if not chars_dir or not chars_dir.exists():
        chars_dir.mkdir(parents=True, exist_ok=True)
    
    char_files = sorted(list(chars_dir.glob("*.txt")))
    available_chars = [f.stem for f in char_files]

    if not context.args:
        text = f"👥 **Story Characters: {bot.active_story} (Bot: {bot.name})**\n---\n"
        if not available_chars:
            text += "No character files found in this story's `characters/`."
        for i, name in enumerate(available_chars):
            status = "✅ (ON)" if name in bot.active_characters else "❌ (OFF)"
            text += f"{i+1}. `{name}` {status}\n"
        text += "\nUse `.char [n/name] on/off`, `.char all off`, `.char save [name] [text]`, `.char edit [n]`"
        await reply_and_log(update, text, bot=bot)
        return

    arg1 = context.args[0].lower()
    
    if arg1 == "all" and len(context.args) > 1 and context.args[1].lower() == "off":
        bot.active_characters = []
        await reply_transient(update, context, "🧹 All story characters deactivated.")
        bot.save_active_context_and_status()
        return

    if len(context.args) >= 3 and arg1 == "save":
        target = context.args[1]
        content = " ".join(context.args[2:])
        (chars_dir / f"{target}.txt").write_text(content, encoding='utf-8')
        await reply_and_log(update, f"💾 Story character `{target}` saved.", bot=bot)
        return

    if arg1 == "edit" and len(context.args) > 1:
        try:
            idx = int(context.args[1]) - 1
            name = available_chars[idx]
            content = get_character_content(name, bot.active_story)
            text = f".char save {name} {content}"
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i+4000])
            return
        except Exception:
            await reply_and_log(update, "❌ Use `.char edit [number]`", bot=bot)
            return

    target = None
    if arg1.isdigit():
        idx = int(arg1) - 1
        if 0 <= idx < len(available_chars):
            target = available_chars[idx]
    elif arg1 in available_chars:
        target = arg1

    if not target:
        await reply_and_log(update, "❌ Character not found.", bot=bot)
        return

    action = context.args[1].lower() if len(context.args) > 1 else "on"
    if action == "on":
        if target not in bot.active_characters:
            bot.active_characters.append(target)
        await reply_transient(update, context, f"👤 Story character `{target}` activated.")
    elif action == "off":
        if target in bot.active_characters:
            bot.active_characters.remove(target)
        await reply_transient(update, context, f"👤 Story character `{target}` deactivated.")

    bot.save_active_context_and_status()

async def element_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manages story elements (locations, items, props) - only in story mode with active story.
    Usage: .element (list), .element [n/name] on/off, .element all off, .element save [name] [text], .element edit [n]
    Aliases: .el
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    if bot.current_mode != "story" or not bot.active_story:
        await reply_and_log(update, "❌ `.element` only works in story mode with an active story (`.story select <name>`).", bot=bot)
        return

    elements_dir = get_story_elements_dir(bot.active_story)
    if not elements_dir or not elements_dir.exists():
        elements_dir.mkdir(parents=True, exist_ok=True)
    
    element_files = sorted(list(elements_dir.glob("*.txt")))
    available_elements = [f.stem for f in element_files]

    if not context.args:
        text = f"🏗️ **Story Elements: {bot.active_story} (Bot: {bot.name})**\n---\n"
        if not available_elements:
            text += "No element files found in this story's `storyelements/`."
        for i, name in enumerate(available_elements):
            status = "✅ (ON)" if name in bot.active_scenes else "❌ (OFF)"
            text += f"{i+1}. `{name}` {status}\n"
        text += "\nUse `.element [n/name] on/off`, `.element all off`, `.element save [name] [text]`, `.element edit [n]`"
        await reply_and_log(update, text, bot=bot)
        return

    arg1 = context.args[0].lower()
    
    if arg1 == "all" and len(context.args) > 1 and context.args[1].lower() == "off":
        bot.active_scenes = []
        await reply_transient(update, context, "🧹 All story elements deactivated.")
        bot.save_active_context_and_status()
        return

    if len(context.args) >= 3 and arg1 == "save":
        target = context.args[1]
        content = " ".join(context.args[2:])
        (elements_dir / f"{target}.txt").write_text(content, encoding='utf-8')
        await reply_and_log(update, f"💾 Story element `{target}` saved.", bot=bot)
        return

    if arg1 == "edit" and len(context.args) > 1:
        try:
            idx = int(context.args[1]) - 1
            name = available_elements[idx]
            content = get_element_content(name, bot.active_story)
            text = f".element save {name} {content}"
            for i in range(0, len(text), 4000):
                await update.message.reply_text(text[i:i+4000])
            return
        except Exception:
            await reply_and_log(update, "❌ Use `.element edit [number]`", bot=bot)
            return

    target = None
    if arg1.isdigit():
        idx = int(arg1) - 1
        if 0 <= idx < len(available_elements):
            target = available_elements[idx]
    elif arg1 in available_elements:
        target = arg1

    if not target:
        await reply_and_log(update, "❌ Element not found.", bot=bot)
        return

    action = context.args[1].lower() if len(context.args) > 1 else "on"
    if action == "on":
        if target not in bot.active_scenes:
            bot.active_scenes.append(target)
        await reply_transient(update, context, f"🏗️ Story element `{target}` activated.")
    elif action == "off":
        if target in bot.active_scenes:
            bot.active_scenes.remove(target)
        await reply_transient(update, context, f"🏗️ Story element `{target}` deactivated.")

    bot.save_active_context_and_status()

async def test_provider_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Iterates through all models on a provider to check for access restrictions.
    Works with Ollama providers (local and online).
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    provider = providers.get(bot.current_provider)
    if not provider:
        provider_name = escape_markdown(bot.current_provider)
        await reply_and_log(update, f"❌ Provider '{provider_name}' not found.", bot=bot)
        return

    if not provider.supports_model_management():
        await reply_and_log(update, "⚠️ This feature is only supported for Ollama providers.", bot=bot)
        return

    display_name = provider.get_display_name()
    progress_msg = await update.message.reply_text(f"🧪 Initializing model sweep for `{display_name}`...\nThis process verifies accessibility and may take several minutes.")

    try:
        all_models = await provider.list_models()
        all_models.sort()
        tested_models_status = {}
        last_model_info = ""
        
        for i, model_name in enumerate(all_models):
            current_display = f"🧪 Testing: `{model_name}` ({i+1}/{len(all_models)})..."
            display_text = f"{last_model_info}\n{current_display}" if last_model_info else current_display
            await progress_msg.edit_text(display_text)

            try:
                test_response = await provider.chat(
                    model=model_name,
                    messages=[{"role": "user", "content": "hello"}],
                    options={"temperature": 0.1}
                )
                if "Error: this model requires a subscription, upgrade for access:" in test_response:
                    tested_models_status[model_name] = "bad"
                    last_model_info = f"❌ `{model_name}`: Restricted"
                else:
                    tested_models_status[model_name] = "good"
                    last_model_info = f"✅ `{model_name}`: Accessible"
            except Exception as e:
                error_str = str(e).lower()
                if "subscription" in error_str or "upgrade" in error_str:
                    tested_models_status[model_name] = "bad"
                    last_model_info = f"❌ `{model_name}`: Restricted"
                else:
                    tested_models_status[model_name] = "unknown"
                    last_model_info = f"⚠️ `{model_name}`: Unknown Status"
            
            await asyncio.sleep(0.5)
        
        # Store results in bot's filtered_models for use with .mf command
        good_models = [m for m, status in tested_models_status.items() if status == "good"]
        bot.filtered_models = good_models
        
        summary = f"✅ **Sweep Complete: `{display_name}`**\n\n"
        summary += f"🟢 Standard: {list(tested_models_status.values()).count('good')}\n"
        summary += f"🔴 Restricted: {list(tested_models_status.values()).count('bad')}"
        await progress_msg.edit_text(summary)

    except Exception as e:
        await reply_and_log(update, f"❌ Failed to test models on `{display_name}`: {e}", bot=bot)

async def mf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Filters models by partial name and allows selection from the filtered list for Ollama providers.
    
    Usage:
        .mf [partial_name]  - Filter models by partial name and show/store the filtered list
        .mf [n]             - Select model by index from the stored filtered list
        .mf                 - Show the current stored filtered list
    """
    if update.effective_user.id != TELEGRAM_USER_ID:
        return

    bot = get_bot(context, update)
    provider = providers.get(bot.current_provider)
    if not provider:
        provider_name = escape_markdown(bot.current_provider)
        await reply_and_log(update, f"❌ Provider '{provider_name}' not found.", bot=bot)
        return

    if not provider.supports_model_management():
        await reply_and_log(update, "⚠️ The `.mf` command is only applicable to Ollama providers.", bot=bot)
        return

    display_name = provider.get_display_name()
    
    # Initialize filtered_models on bot if not exists
    if not hasattr(bot, 'filtered_models'):
        bot.filtered_models = []
    
    try:
        all_models = await provider.list_models()
        all_models.sort()
        
        if not context.args:
            # Show current filtered list
            if not bot.filtered_models:
                await reply_and_log(update, f"ℹ️ No filtered model list stored. Use `.mf [partial_name]` to filter models.", bot=bot)
                return
            
            text = f"🔍 **Filtered Models (`{display_name}` - Bot: {bot.name}):**\n"
            for i, name in enumerate(bot.filtered_models):
                active = " ✅" if name == bot.current_model else ""
                text += f"{i+1}. `{name}`{active}\n"
            await reply_and_log(update, text, bot=bot)
            return
        
        arg = context.args[0]
        
        # Check if argument is a number (index selection)
        try:
            index = int(arg) - 1
            if 0 <= index < len(bot.filtered_models):
                bot.current_model = bot.filtered_models[index]
                bot.save_active_context_and_status()
                await reply_transient(update, context, f"🧠 Switched to model: `{bot.current_model}`")
            else:
                await reply_and_log(update, "❌ Invalid model number.", bot=bot)
            return
        except ValueError:
            # Not a number, treat as partial model name for filtering
            partial_name = arg.lower()
            filtered = [m for m in all_models if partial_name in m.lower()]
            
            if not filtered:
                await reply_and_log(update, f"ℹ️ No models found matching `{arg}` on `{display_name}`.", bot=bot)
                return
            
            # Store the filtered list
            bot.filtered_models = filtered
            
            text = f"🔍 **Filtered Models (`{display_name}` - Bot: {bot.name})** — matching `{arg}`:\n"
            for i, name in enumerate(filtered):
                active = " ✅" if name == bot.current_model else ""
                text += f"{i+1}. `{name}`{active}\n"
            text += f"\nUse `.mf [number]` to select a model."
            await reply_and_log(update, text, bot=bot)
            return
            
    except Exception as e:
        await reply_and_log(update, f"❌ Failed to list models: {e}", bot=bot)

LAZY_COMMAND_MAP = {
    "help": help_command, "h": h_command,
    "role": role_command, "r": r_command, "reload": reload_role_command,
    "roles": roles_command,
    "persona": persona_command, "per": persona_command,
    "image": image_command,
    "provider": provider_command, "p": p_command,
    "model": model_command, "m": m_command,
    "think": toggle_think, "verbose": toggle_verbose, "v": toggle_verbose, 
    "trace": toggle_trace, "lazy": toggle_lazy, "lz": toggle_lazy,
    "chat": chat_actions, "chats": chat_actions, "c": c_command,
    "rs": role_summary_command, "rolesummary": role_summary_command,
    "mf": mf_command, "modelsfiltered": mf_command,
    "clear": chat_actions, "clean": chat_actions, "cl": chat_actions,
    "del": delete_last_message, "context": set_context_limit,
    "recap": recap_command, "story": story_command, "store": story_command,
    "stories": stories_command, "st": st_command,
    "last": last_command, "l": last_command, "ask": ask_command,
    "mode": mode_command, "mo": mode_command,
    "status": status_command, "s": s_command,
    "resend": resend_last_message, "char": char_command,
    "element": element_command, "el": element_command,
    "install": install_command,
    "llmctx": llmcontext_command, "mc": llmcontext_command, "mcontext": llmcontext_command
}

async def handle_failed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Catch-all for messages starting with a dot that do not match a 
    registered command prefix.
    """
    bot = get_bot(context, update)
    text = update.message.text
    if text.startswith("."):
        error_text = (
            "⚠️ **Unknown Command or Typo Detected**\n"
            "It looks like you tried a command but added a space or typo. "
            "Please use `.help` to see valid commands."
        )
        await reply_and_log(update, error_text, is_command=True, bot=bot)
        return

def setup_handlers(app: Application):
    """Configures handlers on an application instance."""
    app.add_handler(PrefixHandler(".","help", help_command))
    app.add_handler(PrefixHandler(".","h", h_command))
    app.add_handler(PrefixHandler(".","role", role_command))
    app.add_handler(PrefixHandler(".","r", r_command))
    app.add_handler(PrefixHandler(".","reload", reload_role_command))
    app.add_handler(PrefixHandler(".","persona", persona_command))
    app.add_handler(PrefixHandler(".","per", persona_command))
    app.add_handler(PrefixHandler(".","image", image_command))
    app.add_handler(PrefixHandler(".","provider", provider_command))
    app.add_handler(PrefixHandler(".","p", p_command))
    app.add_handler(PrefixHandler(".","model", model_command))
    app.add_handler(PrefixHandler(".","m", m_command))
    app.add_handler(PrefixHandler(".","think", toggle_think))
    app.add_handler(PrefixHandler(".","verbose", toggle_verbose))
    app.add_handler(PrefixHandler(".","trace", toggle_trace))
    app.add_handler(PrefixHandler(".","lazy", toggle_lazy))
    app.add_handler(PrefixHandler(".","lz", toggle_lazy))
    app.add_handler(PrefixHandler(".","v", toggle_verbose))
    app.add_handler(PrefixHandler(".","chat", chat_actions))
    app.add_handler(PrefixHandler(".","chats", chat_actions))
    app.add_handler(PrefixHandler(".","c", c_command))
    app.add_handler(PrefixHandler(".","rs", role_summary_command))
    app.add_handler(PrefixHandler(".","rolesummary", role_summary_command))
    app.add_handler(PrefixHandler(".","mf", mf_command))
    app.add_handler(PrefixHandler(".","modelsfiltered", mf_command))
    app.add_handler(PrefixHandler(".","clear", chat_actions))
    app.add_handler(PrefixHandler(".","clean", chat_actions))
    app.add_handler(PrefixHandler(".","cl", chat_actions))
    app.add_handler(PrefixHandler(".","del", delete_last_message))
    app.add_handler(PrefixHandler(".","context", set_context_limit))
    app.add_handler(PrefixHandler(".", "recap", recap_command))
    app.add_handler(PrefixHandler(".", "story", story_command))
    app.add_handler(PrefixHandler(".", "store", story_command))
    app.add_handler(PrefixHandler(".", "stories", stories_command))
    app.add_handler(PrefixHandler(".", "st", st_command))
    app.add_handler(PrefixHandler(".", "roles", roles_command))
    app.add_handler(PrefixHandler(".", "last", last_command))
    app.add_handler(PrefixHandler(".", "l", last_command))
    app.add_handler(PrefixHandler(".", "ask", ask_command))
    app.add_handler(PrefixHandler(".", "mode", mode_command))
    app.add_handler(PrefixHandler(".", "mo", mode_command))
    app.add_handler(PrefixHandler(".","status", status_command))
    app.add_handler(PrefixHandler(".","s", s_command))
    app.add_handler(PrefixHandler(".", "resend", resend_last_message))
    app.add_handler(PrefixHandler(".", "llmctx", llmcontext_command))
    app.add_handler(PrefixHandler(".", "mc", llmcontext_command))
    app.add_handler(PrefixHandler(".", "mcontext", llmcontext_command))
    app.add_handler(PrefixHandler(".","char", char_command))
    app.add_handler(PrefixHandler(".","element", element_command))
    app.add_handler(PrefixHandler(".","el", element_command))
    app.add_handler(PrefixHandler(".","install", install_command))
    app.add_handler(MessageHandler(filters.Regex(r"^\.help(\s|$)"), help_command))
    app.add_handler(MessageHandler(filters.Regex(r"^\.h(\s|$)"), h_command))
    app.add_handler(MessageHandler(filters.Regex(r"^\."), handle_failed_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

async def run_all_bots(bot_instances: List[BotInstance]):
    """Initializes and starts polling for all bots concurrently."""
    for bot in bot_instances:
        await bot.app.initialize()
        BOT_INSTANCES_BY_ID[bot.app.bot.id] = bot
        await bot.app.start()
        await bot.app.updater.start_polling(drop_pending_updates=True)
        print(f"--- Bot Active: {bot.name} (Role: {bot.current_role}, Mode: {bot.current_mode}) ---")

    print(f"✅ All {len(bot_instances)} bot(s) started and listening!")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass
    finally:
        print("\nStopping all bots...")
        for bot in bot_instances:
            try:
                if bot.app.updater and bot.app.updater.running:
                    await bot.app.updater.stop()
                if bot.app.running:
                    await bot.app.stop()
                await bot.app.shutdown()
            except Exception as e:
                print(f"Error stopping bot {bot.name}: {e}")

def main():
    global tracker, BOT_INSTANCES, BOT_INSTANCES_BY_TOKEN, BOT_INSTANCES_BY_ID

    tracker = BotUsageTracker(SETTINGS_DIR / "bot_assignments.json")
    tracker.load()

    tokens = CONFIG.get("TELEGRAM_TOKENS", {})
    if not tokens:
        single_token = CONFIG.get("TELEGRAM_TOKEN")
        if single_token:
            tokens = {"bot": single_token}

    if not tokens:
        print("❌ Error: No TELEGRAM_TOKENS found in config.yaml.")
        return

    # If CLI arg provided, only run that specific bot (e.g. python vivid.py chat1)
    if len(sys.argv) > 1 and sys.argv[1] in tokens:
        target_tokens = {sys.argv[1]: tokens[sys.argv[1]]}
    else:
        target_tokens = tokens

    bot_instances = []
    for bot_name, token in target_tokens.items():
        if not token or token.startswith("your-"):
            print(f"⚠️ Skipping bot '{bot_name}': Invalid or placeholder token.")
            continue
        bot = BotInstance(name=bot_name, token=token)
        app = (
            Application.builder()
            .token(bot.token)
            .read_timeout(60)
            .write_timeout(60)
            .connect_timeout(60)
            .build()
        )
        setup_handlers(app)
        app.bot_data["bot_instance"] = bot
        bot.app = app
        BOT_INSTANCES[bot_name] = bot
        BOT_INSTANCES_BY_TOKEN[token] = bot
        bot.init_state(tracker)
        bot_instances.append(bot)

    if not bot_instances:
        print("❌ Error: No valid bots initialized.")
        return

    try:
        asyncio.run(run_all_bots(bot_instances))
    except (KeyboardInterrupt, SystemExit):
        print("\nBots stopped.")

if __name__ == "__main__":
    main()
