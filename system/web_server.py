"""
Web server with aiohttp - serves dashboard and web chat UI.
All web-specific code lives here, NOT in vivid.py.
"""

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from aiohttp import web
from aiohttp.web import Request, Response

from .config import (
    BASE_DIR,
    CHAT_CONFIG_DIR,
    CHAT_CONTEXT_DIR,
    CHAT_ROLES_DIR,
    CONFIG,
    CONFIG_PATH,
    AI_PROVIDERS,
    STORIES_DIR,
    providers,
    provider_display_info,
    providers_config,
    reload_config,
    write_config,
    TELEGRAM_USER_ID,
    BOT_INSTANCES,
)
from .bot_instance import BotInstance
from .providers import create_provider


# Path to web static files
WEB_DIR = BASE_DIR / "web"
ROLE_TEMPLATES_DIR = WEB_DIR / "role_templates"
ROLE_IMAGES_DIR = WEB_DIR / "role_images"

# Ensure role images directory exists
ROLE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Composition rules every generated Role Maker image prompt must follow: the
# character is shown from the hips up, and the mouth/expression (plus pose) has
# to read as their personality.
IMAGE_PROMPT_RULES = (
    "Frame the shot from the hips up (hips/waist to the top of the head); never a "
    "head-and-shoulders close-up. Make the mouth and facial expression clearly match "
    "the character's personality (e.g. smirk, pout, soft smile, bitten lip, "
    "tight-lipped frown) -- personality wins over IMAGE LOOK for the face -- and "
    "match the pose and body language to that same temperament."
)

# --- Community store (GitHub Gist) -----------------------------------------
# The browser at /browse reads the community role/story database published in a
# public gist. Fetching it server-side keeps the GitHub rate limit on the app
# (optionally authenticated with GITHUB_TOKEN) instead of the visitor's IP.
STORE_GIST_ID = "4f4cde4c3ff29da02a89a613e039f900"
STORE_DB_FILENAME = "db_chatgo.json"
STORE_CACHE_TTL = 300  # seconds
STORE_GIST_ID_RE = re.compile(r"([0-9a-fA-F]{16,})")
STORE_INSTALLED_SUFFIX_RE = re.compile(r"!([0-9a-fA-F]{16,})$")
_store_cache = {"fetched_at": 0.0, "data": None}


def _live_user_id() -> int:
    """Telegram user id from the live CONFIG (reflects setup-page reloads)."""
    return int(CONFIG.get("TELEGRAM_USER_ID", TELEGRAM_USER_ID) or 0)


async def index(request: Request) -> Response:
    """Serve the main dashboard page."""
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return web.FileResponse(index_file)
    return web.Response(text="Dashboard not found. Create web/index.html", status=404)


async def chat_page(request: Request) -> Response:
    """Serve the web chat interface."""
    chat_file = WEB_DIR / "chat.html"
    if chat_file.exists():
        return web.FileResponse(chat_file)
    return web.Response(text="Chat page not found. Create web/chat.html", status=404)


# --- API Endpoints ---

async def api_bots(request: Request) -> Response:
    """List all bot instances with their current state."""
    bots_data = []
    for name, bot in BOT_INSTANCES.items():
        bots_data.append({
            "name": bot.name,
            "provider": bot.current_provider,
            "model": bot.current_model,
            "role": bot.current_role,
            "persona": bot.current_persona,
            "mode": bot.current_mode,
            "active_story": bot.active_story,
            "active_characters": bot.active_characters,
            "active_scenes": bot.active_scenes,
            "context_limit": bot.context_limit,
            "lazy_mode": bot.lazy_mode,
            "think_mode": bot.think_mode,
            "verbose_mode": bot.verbose_mode,
            "trace_mode": bot.trace_mode,
            "voice_mode": bot.voice_mode,
            "total_messages_sent": bot.total_messages_sent,
            "history_length": len(bot.chat_history),
        })
    return web.json_response({"bots": bots_data})


async def api_bot_status(request: Request) -> Response:
    """Get detailed status for a specific bot."""
    bot_name = request.match_info.get("name")
    bot = BOT_INSTANCES.get(bot_name)
    if not bot:
        return web.json_response({"error": f"Bot '{bot_name}' not found"}, status=404)

    from .bot_instance import get_bot_status_dict
    return web.json_response(get_bot_status_dict(bot))


async def api_bot_history(request: Request) -> Response:
    """Get chat history for a specific bot."""
    bot_name = request.match_info.get("name")
    bot = BOT_INSTANCES.get(bot_name)
    if not bot:
        return web.json_response({"error": f"Bot '{bot_name}' not found"}, status=404)

    limit = int(request.query.get("limit", 100))
    history = bot.chat_history[-limit:] if bot.chat_history else []

    return web.json_response({
        "bot": bot_name,
        "history": history,
        "total_messages": len(bot.chat_history)
    })


async def api_send_message(request: Request) -> Response:
    """Send a message to a bot from the web UI."""
    bot_name = request.match_info.get("name")
    bot = BOT_INSTANCES.get(bot_name)
    if not bot:
        return web.json_response({"error": f"Bot '{bot_name}' not found"}, status=404)

    try:
        data = await request.json()
        message = data.get("message", "").strip()
        if not message:
            return web.json_response({"error": "Empty message"}, status=400)

        from .handlers import handle_message
        from telegram import Update
        from telegram.ext import ContextTypes
        import time

        class MockMessage:
            def __init__(self, text):
                self.text = text
                self.message_id = int(time.time() * 1000)
            
            async def reply_text(self, text, parse_mode=None):
                class MockSent:
                    def __init__(self, text):
                        self.message_id = int(time.time() * 1000) + 1
                        self.text = text
                return MockSent(text)

        class MockChat:
            id = 12345

        class MockUpdate:
            def __init__(self, message_text):
                self.message = MockMessage(message_text)
                self.effective_chat = MockChat()
                self.effective_user = type('obj', (object,), {'id': _live_user_id()})()

        class MockBot:
            async def send_chat_action(self, chat_id, action):
                pass

        class MockContext:
            def __init__(self, bot_instance):
                self.bot = MockBot()
                self.bot_data = {"bot_instance": bot_instance}
                self.args = []

        mock_update = MockUpdate(message)
        mock_context = MockContext(bot)

        await handle_message(mock_update, mock_context, incoming_text=message)

        response = ""
        if bot.chat_history and bot.chat_history[-1]["role"] == "assistant":
            response = bot.chat_history[-1]["content"]

        return web.json_response({
            "success": True,
            "bot": bot_name,
            "response": response,
            "history_length": len(bot.chat_history)
        })

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_switch_role(request: Request) -> Response:
    """Switch role for a bot."""
    bot_name = request.match_info.get("name")
    bot = BOT_INSTANCES.get(bot_name)
    if not bot:
        return web.json_response({"error": f"Bot '{bot_name}' not found"}, status=404)

    try:
        data = await request.json()
        role = data.get("role")
        if not role:
            return web.json_response({"error": "Role name required"}, status=400)

        success, error = await bot.switch_role(role)
        if not success:
            return web.json_response({"error": error}, status=400)

        return web.json_response({
            "success": True,
            "bot": bot_name,
            "role": bot.current_role
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_switch_provider(request: Request) -> Response:
    """Switch provider for a bot."""
    bot_name = request.match_info.get("name")
    bot = BOT_INSTANCES.get(bot_name)
    if not bot:
        return web.json_response({"error": f"Bot '{bot_name}' not found"}, status=404)

    try:
        data = await request.json()
        provider = data.get("provider")
        if not provider or provider not in providers:
            return web.json_response({"error": "Invalid provider"}, status=400)

        bot.current_provider = provider
        bot.save_active_context_and_status()

        return web.json_response({
            "success": True,
            "bot": bot_name,
            "provider": bot.current_provider
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_switch_model(request: Request) -> Response:
    """Switch model for a bot."""
    bot_name = request.match_info.get("name")
    bot = BOT_INSTANCES.get(bot_name)
    if not bot:
        return web.json_response({"error": f"Bot '{bot_name}' not found"}, status=404)

    try:
        data = await request.json()
        model = data.get("model")
        if not model:
            return web.json_response({"error": "Model name required"}, status=400)

        provider = providers.get(bot.current_provider)
        if not provider:
            return web.json_response({"error": "No provider selected"}, status=400)

        models = await provider.list_models()
        if model not in models:
            return web.json_response({"error": f"Model '{model}' not available on current provider"}, status=400)

        bot.current_model = model
        bot.save_active_context_and_status()

        return web.json_response({
            "success": True,
            "bot": bot_name,
            "model": bot.current_model
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_providers(request: Request) -> Response:
    """List all available providers."""
    providers_list = []
    for name in AI_PROVIDERS:
        providers_list.append({
            "name": name,
            "display": provider_display_info.get(name, name)
        })
    return web.json_response({"providers": providers_list})


async def api_models(request: Request) -> Response:
    """List models for a specific provider."""
    provider_name = request.match_info.get("provider")
    provider = providers.get(provider_name)
    if not provider:
        return web.json_response({"error": f"Provider '{provider_name}' not found"}, status=404)

    try:
        models = await provider.list_models()
        return web.json_response({"provider": provider_name, "models": models})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_execute_command(request: Request) -> Response:
    """Execute a dot-command on a bot."""
    bot_name = request.match_info.get("name")
    bot = BOT_INSTANCES.get(bot_name)
    if not bot:
        return web.json_response({"error": f"Bot '{bot_name}' not found"}, status=404)

    try:
        data = await request.json()
        command = data.get("command", "").strip()
        if not command.startswith("."):
            command = "." + command

        parts = command.split()
        cmd_name = parts[0][1:]
        args = parts[1:] if len(parts) > 1 else []

        from .handlers import LAZY_COMMAND_MAP
        if cmd_name not in LAZY_COMMAND_MAP:
            return web.json_response({"error": f"Unknown command: {cmd_name}"}, status=400)

        class MockContext:
            def __init__(self, bot_instance, args_list):
                self.bot_data = {"bot_instance": bot_instance}
                self.args = args_list

        class MockMessage:
            def __init__(self):
                self.text = command
                self.message_id = int(time.time() * 1000)
            
            async def reply_text(self, text, parse_mode=None):
                class MockSent:
                    def __init__(self):
                        self.message_id = int(time.time() * 1000) + 1
                return MockSent()

        class MockChat:
            id = 12345

        class MockUpdate:
            def __init__(self):
                self.message = MockMessage()
                self.effective_chat = MockChat()
                self.effective_user = type('obj', (object,), {'id': _live_user_id()})()

        mock_update = MockUpdate()
        mock_context = MockContext(bot, args)

        await LAZY_COMMAND_MAP[cmd_name](mock_update, mock_context)

        return web.json_response({
            "success": True,
            "bot": bot_name,
            "command": command
        })

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# --- Setup / config editor endpoints -----------------------------------------

# Mapping between the editor's provider "type" values and the pychat provider
# schema (provider type + default base URL).
_EDITOR_TYPES = {
    "ollama": ("ollama", "http://localhost:11434"),
    "ollama_cloud": ("ollama", "https://ollama.com"),
    "llamacpp": ("openai_compatible", "http://localhost:8080/v1"),
    "openrouter": ("openai_compatible", "https://openrouter.ai/api/v1"),
    "nvidia": ("openai_compatible", "https://integrate.api.nvidia.com/v1"),
    "google": ("gemini", "https://generativelanguage.googleapis.com/"),
}


def _provider_to_editor(p: dict) -> dict:
    """Convert a pychat provider config dict into the setup editor's shape."""
    pychat_type = (p.get("type") or "ollama").lower()
    url = p.get("url") or ""
    low = url.lower()
    if pychat_type == "ollama":
        editor_type = "ollama_cloud" if "ollama.com" in low else "ollama"
    elif pychat_type == "gemini":
        editor_type = "google"
    elif "nvidia" in low:
        editor_type = "nvidia"
    elif "openrouter" in low:
        editor_type = "openrouter"
    elif "googleapis" in low or "generativelanguage" in low:
        editor_type = "google"
    else:
        editor_type = "llamacpp"
    return {
        "name": p.get("name", ""),
        "type": editor_type,
        "api_key": p.get("api_key", "") or "",
        "model": p.get("model", "") or "",
        "base": url,
        "api_base": url,
    }


def _editor_to_provider(row: dict, existing: dict) -> dict:
    """Convert a setup editor row into a pychat provider config dict.

    ``existing`` holds the current config for the same provider name (if any);
    unknown keys are preserved so hand-edited options survive a UI save.
    """
    editor_type = (row.get("type") or "ollama").lower()
    pychat_type, default_base = _EDITOR_TYPES.get(editor_type, ("openai_compatible", ""))
    base = (row.get("api_base") or row.get("base") or "").strip() or default_base

    cfg = dict(existing or {})
    cfg["name"] = (row.get("name") or "").strip()
    cfg["type"] = pychat_type
    if base:
        cfg["url"] = base
    else:
        cfg.pop("url", None)

    api_key = (row.get("api_key") or "").strip()
    if api_key:
        cfg["api_key"] = api_key
    else:
        cfg.pop("api_key", None)

    model = (row.get("model") or "").strip()
    if model:
        cfg["model"] = model
    else:
        cfg.pop("model", None)

    cfg.setdefault("show_on_provider_list", True)
    return cfg


def _ephemeral_config(row: dict) -> dict:
    """Build a throwaway provider config from an editor row (for test/models)."""
    editor_type = (row.get("type") or "ollama").lower()
    pychat_type, default_base = _EDITOR_TYPES.get(editor_type, ("openai_compatible", ""))
    base = (row.get("api_base") or row.get("base") or "").strip() or default_base
    cfg = {"name": row.get("name") or "test", "type": pychat_type, "url": base}
    if row.get("api_key"):
        cfg["api_key"] = row["api_key"]
    if row.get("model"):
        cfg["model"] = row["model"]
    return cfg


async def _list_models_for(row: dict):
    """Create a transient provider from an editor row and return its models."""
    cfg = _ephemeral_config(row)
    provider = create_provider(cfg["name"], cfg)
    try:
        return await provider.list_models()
    finally:
        client = getattr(provider, "client", None)
        close = getattr(client, "close", None)
        if close is not None:
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass


async def api_config_get(request: Request) -> Response:
    """Return the current config in the shape expected by web/setup.js."""
    bots = [
        {"name": name, "token": token}
        for name, token in (CONFIG.get("TELEGRAM_TOKENS") or {}).items()
    ]
    editor_providers = [_provider_to_editor(p) for p in providers_config]
    return web.json_response({
        "user_id": CONFIG.get("TELEGRAM_USER_ID", 0),
        "debug": bool(CONFIG.get("DEBUG", False)),
        "gist_github_token": CONFIG.get("GITHUB_TOKEN", "") or "",
        "bots": bots,
        "providers": editor_providers,
    })


async def api_config_post(request: Request) -> Response:
    """Persist a config posted by web/setup.js, then reload it in memory."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"error": f"invalid JSON body: {e}"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "expected a JSON object"}, status=400)

    new_config = dict(CONFIG)

    if "user_id" in data:
        try:
            new_config["TELEGRAM_USER_ID"] = int(data.get("user_id") or 0)
        except (TypeError, ValueError):
            new_config["TELEGRAM_USER_ID"] = 0

    if "debug" in data:
        new_config["DEBUG"] = bool(data.get("debug"))

    if "gist_github_token" in data:
        new_config["GITHUB_TOKEN"] = data.get("gist_github_token") or ""

    if "bots" in data:
        tokens = {}
        for bot in data.get("bots") or []:
            name = (bot.get("name") or "").strip()
            token = (bot.get("token") or "").strip()
            if name and token:
                tokens[name] = token
        new_config["TELEGRAM_TOKENS"] = tokens

    if "providers" in data:
        existing_by_name = {p.get("name"): p for p in providers_config}
        new_providers = []
        for row in data.get("providers") or []:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            new_providers.append(_editor_to_provider(row, existing_by_name.get(name, {})))
        new_config["PROVIDERS"] = new_providers
        # We now write the canonical PROVIDERS list; drop legacy keys so they
        # don't get merged back in on the next reload.
        new_config.pop("OLLAMA_LOCAL", None)
        new_config.pop("OLLAMA_ONLINE", None)

    try:
        write_config(new_config)
    except Exception as e:
        return web.json_response({"error": f"failed to write config: {e}"}, status=500)

    # Start/stop bots to match the new config so a fresh install can chat
    # immediately, without restarting the process.
    try:
        from .runner import sync_bots_with_config
        bot_sync = await sync_bots_with_config()
    except Exception as e:
        bot_sync = {"started": [], "stopped": [], "errors": [str(e)]}
        print(f"⚠️ Bot sync after config save failed: {e}")

    return web.json_response({
        "success": True,
        "providers": [p.get("name") for p in providers_config],
        "bots": list(BOT_INSTANCES.keys()),
        "bot_sync": bot_sync,
    })


async def api_config_reload(request: Request) -> Response:
    """Re-read config.yaml from disk and rebuild providers in the running app."""
    try:
        reload_config()
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    # Also bring the running bots in line with the reloaded config.
    try:
        from .runner import sync_bots_with_config
        bot_sync = await sync_bots_with_config()
    except Exception as e:
        bot_sync = {"started": [], "stopped": [], "errors": [str(e)]}

    return web.json_response({
        "success": True,
        "providers": list(providers.keys()),
        "bots": list(BOT_INSTANCES.keys()),
        "bot_sync": bot_sync,
    })


async def api_test_telegram(request: Request) -> Response:
    """Validate a bot token against the Telegram getMe endpoint."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    token = (data.get("token") or "").strip()
    if not token:
        return web.json_response({"error": "token is required"}, status=400)

    import aiohttp
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                payload = await resp.json(content_type=None)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    if not payload.get("ok"):
        return web.json_response(
            {"error": payload.get("description", "invalid token")}, status=400
        )
    result = payload.get("result") or {}
    return web.json_response({
        "username": result.get("username", ""),
        "id": result.get("id"),
    })


async def api_test_provider(request: Request) -> Response:
    """Validate an editor provider row by fetching its model list."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"error": f"invalid JSON body: {e}"}, status=400)
    try:
        models = await _list_models_for(data)
        return web.json_response({"ok": True, "models": models})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_models_query(request: Request) -> Response:
    """List models for an ad-hoc provider described via query parameters."""
    row = {
        "type": request.query.get("type", "ollama"),
        "api_key": request.query.get("key", ""),
        "api_base": request.query.get("base", ""),
        "model": request.query.get("model", ""),
    }
    try:
        models = await _list_models_for(row)
        return web.json_response({"models": models})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ============================================================
# Role Maker API Endpoints
# ============================================================

async def api_templates(request: Request) -> Response:
    """List templates or return a single template's text."""
    name = request.query.get("name")
    names_only = request.query.get("names_only") == "1"

    if not ROLE_TEMPLATES_DIR.exists():
        if names_only:
            return web.json_response({"templates": []})
        return web.json_response({"error": "Templates directory not found"}, status=404)

    if names_only:
        templates = []
        for f in sorted(ROLE_TEMPLATES_DIR.glob("*.txt")):
            templates.append({"name": f.stem})
        return web.json_response({"templates": templates})

    if not name:
        return web.json_response({"error": "Template name required"}, status=400)

    template_file = ROLE_TEMPLATES_DIR / f"{name}.txt"
    if not template_file.exists():
        return web.json_response({"error": f"Template '{name}' not found"}, status=404)

    text = template_file.read_text(encoding="utf-8")
    return web.json_response({"text": text})


async def api_servers(request: Request) -> Response:
    """List all configured LLM servers/providers for the role maker."""
    servers = []
    all_models = []
    default_server = None

    for cfg in providers_config:
        if not cfg.get("show_on_provider_list", True):
            continue
        name = cfg.get("name", "")
        url = cfg.get("url", "")
        model = cfg.get("model", "")
        if not name or not url:
            continue
        servers.append({
            "name": name,
            "url": url,
            "model": model
        })
        if model and model not in all_models:
            all_models.append(model)
        if default_server is None:
            default_server = url

    return web.json_response({
        "servers": servers,
        "models": all_models,
        "default_server": default_server
    })


async def api_server_models(request: Request) -> Response:
    """List models for a specific server/provider."""
    server = request.query.get("server", "")
    if not server:
        return web.json_response({"models": [], "live": False})

    provider = None
    for name, prov in providers.items():
        cfg = None
        for pc in providers_config:
            if pc.get("name") == name:
                cfg = pc
                break
        if cfg and (cfg.get("name") == server or cfg.get("url") == server):
            provider = prov
            break

    if not provider:
        return web.json_response({"models": [], "live": False})

    try:
        models = await provider.list_models()
        return web.json_response({"models": models, "live": True})
    except Exception:
        return web.json_response({"models": [], "live": False})


async def api_create_role_stream(request: Request) -> Response:
    """Stream role generation via SSE using the selected LLM."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    template_text = data.get("template_text", "")
    general_description = data.get("general_description", "")
    server_url = data.get("server_url", "")
    model = data.get("model", "")
    image_look = data.get("image_look", "sultry")

    if not template_text or not general_description:
        return web.json_response({"error": "template_text and general_description required"}, status=400)

    provider = None
    for name, prov in providers.items():
        cfg = None
        for pc in providers_config:
            if pc.get("name") == name:
                cfg = pc
                break
        if cfg and cfg.get("url") == server_url:
            provider = prov
            break

    if not provider:
        return web.json_response({"error": f"Provider for server '{server_url}' not found"}, status=400)

    prompt = f"""{template_text}

=== GENERAL DESCRIPTION ===
{general_description}

=== IMAGE LOOK ===
{image_look}

Generate a complete character role following the template structure above. Output in this exact format:

=== ROLE ===
<the full role text>

=== FILENAME ===
<suggested filename without extension, e.g. My_Character>

=== IMAGE PROMPT ===
<detailed image generation prompt for the character. {IMAGE_PROMPT_RULES} Include hair, eyes, body type, clothing, lighting, and art style.>

=== SUMMARY ===
<concise 2-3 paragraph summary of the character>
"""

    async def generate():
        try:
            yield f"data: {json.dumps({'step': 'role', 'status': 'started'})}\n\n"
            role_text = await provider.chat(model, [{"role": "user", "content": prompt}], {"temperature": 0.8})
            yield f"data: {json.dumps({'step': 'role', 'status': 'done', 'role': role_text})}\n\n"

            sections = {}
            current_section = None
            for line in role_text.splitlines():
                if line.startswith("=== ") and line.endswith(" ==="):
                    current_section = line[4:-4].strip().lower()
                    sections[current_section] = []
                elif current_section:
                    sections[current_section].append(line)

            role_content = "\n".join(sections.get("role", [])).strip()
            filename = "\n".join(sections.get("filename", [])).strip().replace(".txt", "").replace(".jpg", "")
            image_prompt = "\n".join(sections.get("image prompt", [])).strip()
            summary = "\n".join(sections.get("summary", [])).strip()

            if not role_content:
                role_content = role_text
            if not filename:
                import re
                filename = re.sub(r'[^A-Za-z0-9_-]+', '_', general_description[:50]).strip('_')
            if not image_prompt:
                image_prompt = (
                    f"Hips-up shot of {general_description}, framed from the hips to "
                    f"the top of the head, mouth and facial expression matching their "
                    f"personality, {image_look}, detailed, high quality"
                )
            if not summary:
                summary = general_description[:300]

            yield f"data: {json.dumps({'step': 'filename', 'status': 'done', 'filename': f'GOCHATAPPROLETAG_{filename}.txt'})}\n\n"
            yield f"data: {json.dumps({'step': 'image', 'status': 'done', 'image_description': image_prompt})}\n\n"
            yield f"data: {json.dumps({'step': 'summary', 'status': 'done', 'summary': summary})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        }
    )
    await response.prepare(request)

    async for chunk in generate():
        await response.write(chunk.encode('utf-8'))

    await response.write_eof()
    return response


async def api_imgbb_upload(request: Request) -> Response:
    """Upload image to local storage (imgbb fallback). Returns a local URL."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    image_base64 = data.get("image_base64", "")
    if not image_base64:
        return web.json_response({"error": "image_base64 required"}, status=400)

    try:
        image_data = base64.b64decode(image_base64)
        filename = f"role_img_{int(time.time() * 1000)}.png"
        filepath = ROLE_IMAGES_DIR / filename
        filepath.write_bytes(image_data)
        return web.json_response({"url": f"/static/role_images/{filename}"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_catbox_upload(request: Request) -> Response:
    """Upload image to local storage (catbox fallback). Returns a local URL."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    image_base64 = data.get("image_base64", "")
    if not image_base64:
        return web.json_response({"error": "image_base64 required"}, status=400)

    try:
        image_data = base64.b64decode(image_base64)
        filename = f"role_img_{int(time.time() * 1000)}.png"
        filepath = ROLE_IMAGES_DIR / filename
        filepath.write_bytes(image_data)
        return web.json_response({"url": f"/static/role_images/{filename}"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def api_github_gist(request: Request) -> Response:
    """Create a GitHub gist with the role files."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    files = data.get("files", {})
    description = data.get("description", "Character role")

    if not files:
        return web.json_response({"error": "No files provided"}, status=400)

    github_token = CONFIG.get("GITHUB_TOKEN", "").strip()
    if not github_token or github_token == "your-github-token":
        return web.json_response({"error": "GitHub token not configured"}, status=400)

    import aiohttp
    gist_data = {
        "description": description,
        "public": True,
        "files": {}
    }
    for fname, fcontent in files.items():
        gist_data["files"][fname] = {"content": fcontent}

    url = "https://api.github.com/gists"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "vivid-role-maker"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=gist_data, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status >= 400:
                    err_text = await resp.text()
                    return web.json_response({"error": f"GitHub API error: {err_text}"}, status=resp.status)
                result = await resp.json()
                return web.json_response({"html_url": result.get("html_url", "")})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


def _sanitize_role_name(name: str) -> str:
    """Sanitize role name for filesystem."""
    import re
    name = (name or "").strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_-]", "", name)
    name = re.sub(r"[_-]{2,}", "_", name)
    name = name.strip("_-")
    return name[:80] or "role"


async def api_roles_save(request: Request) -> Response:
    """Save a role locally to chats/roles/. Handles conflicts with _2 suffix."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

    name = data.get("name", "").strip()
    text = data.get("text", "")
    image_png = data.get("image_png", "")
    overwrite = bool(data.get("overwrite", False))

    if not name or not text:
        return web.json_response({"error": "name and text required"}, status=400)

    safe_name = _sanitize_role_name(name)
    txt_path = CHAT_ROLES_DIR / f"{safe_name}.txt"
    jpg_path = CHAT_ROLES_DIR / f"{safe_name}.jpg"

    if txt_path.exists() and not overwrite:
        base = safe_name
        counter = 2
        while (CHAT_ROLES_DIR / f"{base}_{counter}.txt").exists():
            counter += 1
        suggested = f"{base}_{counter}"
        return web.json_response({
            "conflict": True,
            "data": {"suggested": suggested}
        }, status=409)

    txt_path.write_text(text, encoding="utf-8")
    files_saved = [f"chats/roles/{safe_name}.txt"]

    if image_png:
        try:
            image_data = base64.b64decode(image_png)
            jpg_path.write_bytes(image_data)
            files_saved.append(f"chats/roles/{safe_name}.jpg")
        except Exception:
            pass

    return web.json_response({
        "data": {
            "name": safe_name,
            "files": files_saved,
            "suggested": safe_name
        }
    })


# --- Community store API ---------------------------------------------------
# Backs /browse: the store database, the installed-role list and the
# install/uninstall buttons on each role card.

def _parse_gist_id(value) -> str:
    """Extract a gist id from a plain id or any gist URL, lowercased."""
    match = STORE_GIST_ID_RE.search(str(value or ""))
    return match.group(1).lower() if match else ""


def _installed_gist_id(stem: str) -> str:
    """Return the gist id from an installed name like 'role!<gist_id>'."""
    match = STORE_INSTALLED_SUFFIX_RE.search(stem or "")
    return match.group(1).lower() if match else ""


def _github_headers() -> dict:
    """Headers for GitHub API/raw requests, authenticated when configured."""
    headers = {
        "User-Agent": "Vivid-Chat-Web",
        "Accept": "application/vnd.github+json",
    }
    token = str(CONFIG.get("GITHUB_TOKEN", "") or "").strip()
    if token and token != "your-github-token":
        headers["Authorization"] = f"token {token}"
    return headers


async def _fetch_gist(session: aiohttp.ClientSession, gist_id: str) -> dict:
    """Fetch gist metadata from the GitHub API."""
    url = f"https://api.github.com/gists/{gist_id}"
    async with session.get(
        url, headers=_github_headers(), timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        if resp.status == 404:
            raise RuntimeError(f"Gist not found: {gist_id}")
        if resp.status == 403:
            raise RuntimeError(
                "GitHub API rate limit exceeded. Add GITHUB_TOKEN to config.yaml "
                "for higher limits (5000/hr vs 60/hr)."
            )
        if resp.status >= 400:
            detail = (await resp.text()).strip()
            raise RuntimeError(f"GitHub API error {resp.status}: {detail[:200]}")
        return await resp.json()


async def _read_gist_file(session: aiohttp.ClientSession, file_info: dict) -> str:
    """Return a gist file's text, falling back to its raw URL when truncated."""
    content = file_info.get("content")
    if content is not None and not file_info.get("truncated"):
        return content
    raw_url = file_info.get("raw_url")
    if not raw_url:
        raise RuntimeError("Gist file cannot be downloaded (no raw URL).")
    async with session.get(
        raw_url, headers=_github_headers(), timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status} downloading gist file")
        return await resp.text()


async def _fetch_store_db() -> dict:
    """Download and parse the community store database from its gist."""
    async with aiohttp.ClientSession() as session:
        gist = await _fetch_gist(session, STORE_GIST_ID)
        files = gist.get("files") or {}
        file_info = files.get(STORE_DB_FILENAME)
        if file_info is None:
            raise RuntimeError(
                f"The store gist does not contain {STORE_DB_FILENAME}."
            )
        text = await _read_gist_file(session, file_info)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {STORE_DB_FILENAME}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"{STORE_DB_FILENAME} is not a JSON object.")
    data.setdefault("roles", [])
    data.setdefault("stories", [])
    return data


async def api_store(request: Request) -> Response:
    """Serve the community store database, cached for a few minutes."""
    refresh = request.query.get("refresh", "").lower() in ("1", "true", "yes")
    now = time.monotonic()
    cached = _store_cache["data"]
    if (
        not refresh
        and cached is not None
        and now - _store_cache["fetched_at"] < STORE_CACHE_TTL
    ):
        return web.json_response(cached)

    try:
        data = await _fetch_store_db()
    except Exception as exc:
        return web.json_response(
            {"error": f"Could not load store data: {exc}"}, status=502
        )

    _store_cache["data"] = data
    _store_cache["fetched_at"] = time.monotonic()
    return web.json_response(data)


def _installed_entries() -> list[dict]:
    """List roles and stories installed from the store (name!<gist_id>)."""
    entries = []
    if CHAT_ROLES_DIR.exists():
        for path in sorted(CHAT_ROLES_DIR.glob("*.txt")):
            gist_id = _installed_gist_id(path.stem)
            if not gist_id:
                continue
            entries.append({
                "kind": "role",
                "name": path.stem.rsplit("!", 1)[0],
                "gist_id": gist_id,
                "file": f"chats/roles/{path.name}",
            })
    if STORIES_DIR.exists():
        for path in sorted(STORIES_DIR.iterdir()):
            if not path.is_dir():
                continue
            gist_id = _installed_gist_id(path.name)
            if not gist_id:
                continue
            entries.append({
                "kind": "story",
                "name": path.name.rsplit("!", 1)[0],
                "gist_id": gist_id,
                "file": f"stories/{path.name}",
            })
    return entries


async def api_installed(request: Request) -> Response:
    """List every role/story installed from the community store."""
    return web.json_response({"installed": _installed_entries()})


async def _install_role_from_store(gist_id: str) -> dict:
    """Install a role gist into chats/roles/ as 'name!<gist_id>' + images."""
    async with aiohttp.ClientSession() as session:
        gist = await _fetch_gist(session, gist_id)
        files = gist.get("files") or {}

        tag_name = next(
            (name for name in files if "GOCHATAPPROLETAG_" in name), None
        )
        if not tag_name:
            raise RuntimeError(
                "This gist does not contain a GOCHATAPPROLETAG_* role file."
            )

        role_name = Path(tag_name).stem.replace("GOCHATAPPROLETAG_", "")
        role_name = _sanitize_role_name(role_name)
        full_name = f"{role_name}!{gist_id}"

        role_text = await _read_gist_file(session, files[tag_name])
        if not role_text.strip():
            raise RuntimeError("The role file in this gist is empty.")

        CHAT_ROLES_DIR.mkdir(parents=True, exist_ok=True)
        role_path = CHAT_ROLES_DIR / f"{full_name}.txt"
        role_path.write_text(role_text, encoding="utf-8")
        saved = [f"chats/roles/{role_path.name}"]

        thumb_name = next(
            (name for name in files if Path(name).name == "thumb.md"), None
        )
        if thumb_name:
            thumb_text = await _read_gist_file(session, files[thumb_name]) or ""
            for index, image_url in enumerate(
                re.findall(r'src="([^"]+)"', thumb_text)
            ):
                try:
                    ext = os.path.splitext(urlparse(image_url).path)[1] or ".jpg"
                    image_name = (
                        f"{full_name}{ext}" if index == 0
                        else f"{full_name}_{index}{ext}"
                    )
                    async with session.get(
                        image_url,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status >= 400:
                            raise RuntimeError(f"HTTP {resp.status}")
                        (CHAT_ROLES_DIR / image_name).write_bytes(
                            await resp.read()
                        )
                    saved.append(f"chats/roles/{image_name}")
                except Exception as exc:
                    print(f"Failed to download role image {image_url}: {exc}")

        return {"name": full_name, "role": role_name, "files": saved}


async def api_store_install(request: Request) -> Response:
    """Install a role from the community store."""
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": f"Invalid JSON: {exc}"}, status=400)

    kind = str(payload.get("kind") or "role").strip().lower()
    gist_id = _parse_gist_id(payload.get("gist_id") or payload.get("id"))
    if not gist_id:
        return web.json_response(
            {"error": "A valid gist_id is required."}, status=400
        )
    if kind != "role":
        return web.json_response(
            {"error": f"Installing '{kind}' items is not supported."}, status=400
        )

    try:
        result = await _install_role_from_store(gist_id)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)

    return web.json_response({"ok": True, **result})


def _uninstall_role(gist_id: str) -> list[str]:
    """Delete an installed role, its images, context and status files."""
    removed = []

    def _remove(path: Path) -> None:
        try:
            path.unlink()
        except OSError as exc:
            print(f"Failed to remove {path}: {exc}")
            return
        try:
            rel = path.relative_to(BASE_DIR)
        except ValueError:
            rel = path
        removed.append(str(rel).replace("\\", "/"))

    full_names = set()
    if CHAT_ROLES_DIR.exists():
        for path in CHAT_ROLES_DIR.iterdir():
            if (
                path.is_file()
                and path.suffix == ".txt"
                and _installed_gist_id(path.stem) == gist_id
            ):
                full_names.add(path.stem)

        for path in CHAT_ROLES_DIR.iterdir():
            if not path.is_file():
                continue
            stem = path.stem
            if stem.endswith(f"!{gist_id}") or any(
                stem.startswith(f"{name}_") for name in full_names
            ):
                _remove(path)

    for folder, suffix in ((CHAT_CONTEXT_DIR, ".txt"), (CHAT_CONFIG_DIR, ".status")):
        if not folder.exists():
            continue
        for path in folder.iterdir():
            if (
                path.is_file()
                and path.suffix == suffix
                and _installed_gist_id(path.stem) == gist_id
            ):
                _remove(path)

    return removed


async def api_store_uninstall(request: Request) -> Response:
    """Uninstall a role installed from the community store."""
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": f"Invalid JSON: {exc}"}, status=400)

    kind = str(payload.get("kind") or "role").strip().lower()
    gist_id = _parse_gist_id(payload.get("gist_id") or payload.get("id"))
    if not gist_id:
        return web.json_response(
            {"error": "A valid gist_id is required."}, status=400
        )
    if kind != "role":
        return web.json_response(
            {"error": f"Uninstalling '{kind}' items is not supported."}, status=400
        )

    removed = _uninstall_role(gist_id)
    if not removed:
        return web.json_response(
            {"ok": False, "error": "That role is not installed."}, status=404
        )
    return web.json_response({"ok": True, "removed": removed})


async def role_maker_page(request: Request) -> Response:
    """Serve the Role Maker page."""
    return await _serve_page("role_maker.html")


async def _serve_page(filename: str) -> Response:
    page = WEB_DIR / filename
    if page.exists():
        return web.FileResponse(page)
    return web.Response(text=f"{filename} not found", status=404)


async def setup_page(request: Request) -> Response:
    return await _serve_page("setup.html")


async def browser_page(request: Request) -> Response:
    return await _serve_page("browser.html")


async def browser_static_page(request: Request) -> Response:
    return await _serve_page("browser_static.html")


def create_web_app() -> web.Application:
    """Create and configure the aiohttp web application."""
    app = web.Application()

    if WEB_DIR.exists():
        app.router.add_static("/static/", path=WEB_DIR, name="static")

    app.router.add_get("/", index, name="index")
    app.router.add_get("/chat", chat_page, name="chat")
    app.router.add_get("/setup", setup_page, name="setup")
    app.router.add_get("/browse", browser_page, name="browse")
    app.router.add_get("/browser", browser_page, name="browser")
    app.router.add_get("/browser_static", browser_static_page, name="browser_static")
    app.router.add_get("/role_maker", role_maker_page, name="role_maker")
    app.router.add_get("/role", role_maker_page, name="role")

    app.router.add_get("/api/bots", api_bots)
    app.router.add_get("/api/bots/{name}/status", api_bot_status)
    app.router.add_get("/api/bots/{name}/history", api_bot_history)
    app.router.add_post("/api/bots/{name}/message", api_send_message)
    app.router.add_post("/api/bots/{name}/role", api_switch_role)
    app.router.add_post("/api/bots/{name}/provider", api_switch_provider)
    app.router.add_post("/api/bots/{name}/model", api_switch_model)
    app.router.add_post("/api/bots/{name}/command", api_execute_command)
    app.router.add_get("/api/providers", api_providers)
    app.router.add_get("/api/models/{provider}", api_models)
    app.router.add_get("/api/models", api_models_query)

    # Role Maker API
    app.router.add_get("/api/templates", api_templates)
    app.router.add_get("/api/servers", api_servers)
    app.router.add_get("/api/server_models", api_server_models)
    app.router.add_post("/api/create_role_stream", api_create_role_stream)
    app.router.add_post("/api/imgbb/upload", api_imgbb_upload)
    app.router.add_post("/api/catbox/upload", api_catbox_upload)
    app.router.add_post("/api/github/gist", api_github_gist)
    app.router.add_post("/api/roles/save", api_roles_save)

    # Community store API (backs /browse)
    app.router.add_get("/api/store", api_store)
    app.router.add_get("/api/installed", api_installed)
    app.router.add_post("/api/store/install", api_store_install)
    app.router.add_post("/api/store/uninstall", api_store_uninstall)

    app.router.add_get("/api/config", api_config_get)
    app.router.add_post("/api/config", api_config_post)
    app.router.add_post("/api/config/reload", api_config_reload)
    app.router.add_post("/api/test/telegram", api_test_telegram)
    app.router.add_post("/api/test/provider", api_test_provider)

    return app