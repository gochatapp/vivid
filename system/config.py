"""
Configuration loading, paths, and global constants.
Extracted from vivid.py top-level initialization.
"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, List

# =============================================================================
# BASE DIRECTORY & CONFIG LOADING
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent
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

# =============================================================================
# GLOBAL CONSTANTS
# =============================================================================

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
# DIRECTORY STRUCTURE (chats_stories layout)
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

# Ensure directories exist
for d in [
    CHATS_DIR, CHAT_PERSONAS_DIR, CHAT_ROLES_DIR, CHAT_CONTEXT_DIR,
    CHAT_CONFIG_DIR, CHAT_BACKUPS_DIR, CHAT_SESSIONS_DIR, STORIES_DIR, SETTINGS_DIR
]:
    d.mkdir(parents=True, exist_ok=True)

# =============================================================================
# PROVIDER INITIALIZATION
# =============================================================================

# Load providers from new PROVIDERS config, with backward compatibility
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

# Create provider instances (will be populated after providers module is loaded)
providers: Dict[str, Any] = {}
provider_display_info: Dict[str, str] = {}
AI_PROVIDERS: List[str] = []

# Backward compatibility: ollama_clients dict for old code
ollama_clients: Dict[str, Any] = {}

# These will be initialized in providers.py after classes are defined
def initialize_providers():
    """Initialize provider instances from config. Called after provider classes are defined."""
    global providers, provider_display_info, AI_PROVIDERS, ollama_clients
    from .providers import create_provider, OllamaProvider
    
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
    
    # Backward compatibility
    ollama_clients = {}
    for name, provider in providers.items():
        if isinstance(provider, OllamaProvider):
            ollama_clients[name] = provider.client


# =============================================================================
# GLOBAL BOT INSTANCES (shared state across modules)
# =============================================================================

from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .bot_instance import BotInstance
    from telegram.ext import ContextTypes
    from telegram import Update

# Global bot instances dictionaries (used by other modules)
BOT_INSTANCES: Dict[str, "BotInstance"] = {}
BOT_INSTANCES_BY_ID: Dict[int, "BotInstance"] = {}
BOT_INSTANCES_BY_TOKEN: Dict[str, "BotInstance"] = {}


def get_current_default_bot() -> "BotInstance":
    """Returns the primary active bot instance or a fallback."""
    if BOT_INSTANCES:
        return next(iter(BOT_INSTANCES.values()))
    # Import here to avoid circular import
    from .bot_instance import BotInstance
    dummy = BotInstance("bot", "")
    return dummy


def get_bot(context: Optional["ContextTypes.DEFAULT_TYPE"] = None, update: Optional["Update"] = None) -> "BotInstance":
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