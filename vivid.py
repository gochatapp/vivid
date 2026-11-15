"""
Vivid Chat - entry point and backward-compatibility shim.

All implementation now lives in the ``system`` package. This module keeps the
``python vivid.py`` entry point working (used by vwin.bat / vlin.sh) and
re-exports the legacy names that tests and tools import from ``vivid``.
"""

import sys  # noqa: F401  (kept for callers that expect it on the module)

# Public API from the system package (config, providers, tracker, bot instance).
from system import *  # noqa: F401,F403
from system import main  # noqa: F401
from system.runner import run_all_bots  # noqa: F401

# Configuration / paths and live containers.
from system.config import (  # noqa: F401
    CONFIG,
    CONFIG_PATH,
    PATHS_CONFIG_PATH,
    BASE_DIR,
    CHATS_DIR,
    CHATS_STORIES_DIR,
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
    initialize_providers,
    reload_config,
    write_config,
    get_current_default_bot,
    get_bot,
    BOT_INSTANCES,
    BOT_INSTANCES_BY_ID,
    BOT_INSTANCES_BY_TOKEN,
)

# Providers.
from system.providers import (  # noqa: F401
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    GeminiProvider,
    create_provider,
)

# Usage tracker.
from system.tracker import BotUsageTracker, tracker  # noqa: F401

# Bot instance helpers (context formatting, status (de)serialization).
from system.bot_instance import (  # noqa: F401
    BotInstance,
    format_context_text,
    parse_context_text,
    get_bot_status_dict,
    apply_status_dict,
)

# Handler helpers historically imported from vivid by the test scripts.
from system.handlers import (  # noqa: F401
    setup_handlers,
    LAZY_COMMAND_MAP,
    _install_role_from_gist,
    _install_story_from_gist,
)

# Web app factory (used by tooling and the runner).
from system.web_server import create_web_app  # noqa: F401


if __name__ == "__main__":
    main()
