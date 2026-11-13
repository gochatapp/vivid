"""
Vivid Chat - System Modules
"""

from .config import (
    BASE_DIR,
    CONFIG,
    CONFIG_PATH,
    PATHS_CONFIG_PATH,
    CHATS_STORIES_DIR,
    CHATS_DIR,
    CHAT_PERSONAS_DIR,
    CHAT_ROLES_DIR,
    CHAT_CONTEXT_DIR,
    CHAT_CONFIG_DIR,
    CHAT_BACKUPS_DIR,
    CHAT_SESSIONS_DIR,
    STORIES_DIR,
    ROLES_DIR,
    ROLE_IMAGES_DIR,
    CONTEXT_DIR,
    SETTINGS_DIR,
    TELEGRAM_USER_ID,
    TELEGRAM_TOKENS,
    VIVID_PREFIX,
    GENERATION_OPTIONS,
    CONTEXT_LIMIT,
    AI_PROVIDERS,
    providers,
    provider_display_info,
    ollama_clients,
    LAST_LATENCY,
    TOTAL_MESSAGES_SENT,
)
from .providers import (
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    GeminiProvider,
    create_provider,
)
from .tracker import BotUsageTracker, tracker
from .bot_instance import BotInstance
from .content import (
    get_role_content,
    get_persona_content,
    get_story_characters_dir,
    get_story_elements_dir,
    get_character_content,
    get_element_content,
    get_scene_content,
)
from .handlers import setup_handlers, LAZY_COMMAND_MAP
from .web_server import create_web_app
from .runner import main

__all__ = [
    # config
    "BASE_DIR",
    "CONFIG",
    "CONFIG_PATH",
    "PATHS_CONFIG_PATH",
    "CHATS_STORIES_DIR",
    "CHATS_DIR",
    "CHAT_PERSONAS_DIR",
    "CHAT_ROLES_DIR",
    "CHAT_CONTEXT_DIR",
    "CHAT_CONFIG_DIR",
    "CHAT_BACKUPS_DIR",
    "CHAT_SESSIONS_DIR",
    "STORIES_DIR",
    "ROLES_DIR",
    "ROLE_IMAGES_DIR",
    "CONTEXT_DIR",
    "SETTINGS_DIR",
    "TELEGRAM_USER_ID",
    "TELEGRAM_TOKENS",
    "VIVID_PREFIX",
    "GENERATION_OPTIONS",
    "CONTEXT_LIMIT",
    "AI_PROVIDERS",
    "providers",
    "provider_display_info",
    "ollama_clients",
    "LAST_LATENCY",
    "TOTAL_MESSAGES_SENT",
    # providers
    "LLMProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "GeminiProvider",
    "create_provider",
    # tracker
    "BotUsageTracker",
    "tracker",
    # bot_instance
    "BotInstance",
    # content
    "get_role_content",
    "get_persona_content",
    "get_story_characters_dir",
    "get_story_elements_dir",
    "get_character_content",
    "get_element_content",
    "get_scene_content",
    # handlers
    "setup_handlers",
    "LAZY_COMMAND_MAP",
    # web_server
    "create_web_app",
    # runner
    "main",
]