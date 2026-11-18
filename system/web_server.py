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
    CHAT_PERSONAS_DIR,
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
PERSONA_TEMPLATES_DIR = WEB_DIR / "persona_templates"
USER_PERSONA_TEMPLATES_DIR = WEB_DIR / "user" / "persona_templates"

# Ensure role images and persona template directories exist
ROLE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
PERSONA_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
USER_PERSONA_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

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
    "deepseek": ("openai_compatible", "https://api.deepseek.com/v1"),
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
    elif "deepseek" in low:
        editor_type = "deepseek"
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


def _sanitize_install_name(name: str) -> str:
    """Mirror the Go ``sanitizeInstallName`` used by the persona/store APIs.

    Spaces become underscores, anything outside ``[A-Za-z0-9_-]`` is dropped,
    leading/trailing ``_`` or ``-`` are trimmed, and an empty result becomes
    ``untitled``.
    """
    import re
    name = (name or "").strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_-]", "", name)
    name = name.strip("_-")
    return name or "untitled"


async def api_personas(request: Request) -> Response:
    """Persona Maker: list or show personas, and save one.

    GET  /api/personas?name=X  -> {"name", "text", "image_desc"?}
    GET  /api/personas         -> {"personas": [names]}
    POST /api/personas         -> {"status": "saved", "name": safe}
    """
    if request.method == "GET":
        name = request.query.get("name")
        if name:
            safe_name = _sanitize_install_name(name)
            persona_file = CHAT_PERSONAS_DIR / f"{safe_name}.txt"
            if not persona_file.exists():
                return web.json_response(
                    {"error": f"Persona '{name}' not found"}, status=404
                )
            out = {
                "name": safe_name,
                "text": persona_file.read_text(encoding="utf-8"),
            }
            image_desc_file = CHAT_PERSONAS_DIR / f"{safe_name}.image_desc"
            if image_desc_file.exists():
                out["image_desc"] = image_desc_file.read_text(encoding="utf-8")
            return web.json_response(out)

        # List persona names, skipping image-description sidecars.
        names = []
        if CHAT_PERSONAS_DIR.exists():
            for f in sorted(CHAT_PERSONAS_DIR.glob("*.txt")):
                if f.stem.endswith(".image"):
                    continue
                names.append(f.stem)
        return web.json_response({"personas": names})

    # POST -> save (always overwrites, mirroring the Go handler)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    name = data.get("name", "") or ""
    text = data.get("text", "") or ""
    image_desc = data.get("image_desc", "") or ""
    image_jpeg = data.get("image_jpeg", "") or ""

    if not name:
        return web.json_response({"error": "name required"}, status=400)

    safe_name = _sanitize_install_name(name)
    CHAT_PERSONAS_DIR.mkdir(parents=True, exist_ok=True)

    (CHAT_PERSONAS_DIR / f"{safe_name}.txt").write_text(text, encoding="utf-8")

    # Image description sidecar. Using ".image_desc" (instead of the Go
    # ".image.txt") keeps it out of the bot's *.txt persona listing.
    image_desc_file = CHAT_PERSONAS_DIR / f"{safe_name}.image_desc"
    if image_desc.strip():
        image_desc_file.write_text(image_desc, encoding="utf-8")
    elif image_desc_file.exists():
        image_desc_file.unlink()

    # Optional JPEG avatar supplied as base64 (the page strips the data: prefix).
    image_path = CHAT_PERSONAS_DIR / f"{safe_name}.jpg"
    if image_jpeg.strip():
        try:
            raw = base64.b64decode(image_jpeg.split(",")[-1])
        except Exception:
            return web.json_response(
                {"error": "invalid image_jpeg base64"}, status=400
            )
        image_path.write_bytes(raw)
    elif image_path.exists():
        image_path.unlink()

    return web.json_response({"status": "saved", "name": safe_name})


def _template_names_from_dir(directory: Path) -> list:
    """Return sorted template stems (*.txt) for a directory."""
    if not directory.exists():
        return []
    return sorted(f.stem for f in directory.glob("*.txt") if f.is_file())


def _read_template_text_from_dir(directory: Path, name: str):
    """Read one template's text from a directory, or None if missing."""
    path = directory / f"{_sanitize_install_name(name)}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _read_merged_persona_template(name: str):
    """Merge user + builtin persona templates, like the Go helper."""
    parts = []
    for directory in (USER_PERSONA_TEMPLATES_DIR, PERSONA_TEMPLATES_DIR):
        text = _read_template_text_from_dir(directory, name)
        if text is not None:
            parts.append(text)
    if not parts:
        return None
    return "\n---\n".join(parts)


async def api_persona_templates(request: Request) -> Response:
    """Persona Maker: list templates or return one merged template's text."""
    name = request.query.get("name")
    if name:
        text = _read_merged_persona_template(name)
        if text is None:
            # Templates are optional; mirror Go by returning empty text (200).
            return web.json_response({"name": name, "text": ""})
        return web.json_response({"name": name, "text": text})

    # Merge builtin + user template names.
    all_names = sorted(
        set(_template_names_from_dir(PERSONA_TEMPLATES_DIR))
        | set(_template_names_from_dir(USER_PERSONA_TEMPLATES_DIR))
    )

    if request.query.get("names_only") == "1":
        return web.json_response({"templates": all_names})

    out = []
    for n in all_names:
        text = _read_merged_persona_template(n)
        if text is not None:
            out.append({"name": n, "text": text})
    return web.json_response({"templates": out})


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
    # Extension for the saved portrait. The Role Maker sends ".webp" for an
    # animated WebP (so its frames survive) and ".jpg" for the resized still;
    # anything unexpected falls back to ".jpg".
    image_ext = str(data.get("image_ext", ".jpg") or ".jpg").lower()
    if image_ext not in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
        image_ext = ".jpg"
    overwrite = bool(data.get("overwrite", False))

    if not name or not text:
        return web.json_response({"error": "name and text required"}, status=400)

    safe_name = _sanitize_role_name(name)
    txt_path = CHAT_ROLES_DIR / f"{safe_name}.txt"
    img_path = CHAT_ROLES_DIR / f"{safe_name}{image_ext}"

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
            img_path.write_bytes(image_data)
            files_saved.append(f"chats/roles/{safe_name}{image_ext}")
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


# --- Story Creator (public gist publishing) ---------------------------------
# /story runs web/story_creator.html. The page builds a story in the browser and
# posts it here, and this module publishes it as a public gist in the GOCHATAPP
# convention so any install can pick it up with /api/story/install:
#
#   GOCHATAPPSTORYTAG_<story>.md    story backstory -> story_context/context.txt
#   character_<name>.txt            one file per character
#   <name>_thumb.md                 <img src="..."> portrait for that character
#   storyelement_<name>.txt         one file per story element
#   thumb.md                        <img src="..."> story cover
#
# Gist files are text only, so images are uploaded to ImgBB (IMGBB_API_KEY) and
# the gist carries the public URLs.

STORY_TAG_PREFIX = "GOCHATAPPSTORYTAG_"
STORY_IMAGE_SIZE = 450


def _story_file_slug(value: str) -> str:
    """Sanitize a story/character/element name into a gist-safe file stem."""
    return _sanitize_role_name(str(value or ""))


def _story_thumb_markdown(image_url: str, size: int = STORY_IMAGE_SIZE) -> str:
    """Wrap a public image URL in the <img> markup the installers parse."""
    safe = (
        str(image_url or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f'<img width="{size}" height="{size}" alt="image" src="{safe}" />\n'


def _story_first_image_url(text: str) -> str:
    """First src="..." URL inside a thumb markdown file."""
    match = re.search(r'src="([^"]+)"', text or "")
    return match.group(1) if match else ""


def _story_description_text(value) -> str:
    """Normalize a description (string or list of lines) to plain text."""
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(part) for part in value)
    return str(value or "").strip()


def _story_unique_stem(stem: str, used: set) -> str:
    """Return a file stem that is not in *used* yet, and reserve it."""
    candidate = stem or "story"
    counter = 2
    while candidate in used:
        candidate = f"{stem}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


async def _imgbb_upload_base64(
    session: aiohttp.ClientSession, image_base64: str, name: str = ""
) -> str:
    """Upload a base64 (or data-URL) image to ImgBB and return its public URL."""
    api_key = str(CONFIG.get("IMGBB_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError(
            "ImgBB API key is not configured. Add IMGBB_API_KEY to config.yaml "
            "or on the Setup page."
        )

    payload = str(image_base64 or "").strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    if not payload:
        raise RuntimeError("No image data to upload.")

    form = aiohttp.FormData()
    form.add_field("key", api_key)
    form.add_field("image", payload)
    if name:
        form.add_field("name", str(name)[:64])

    async with session.post(
        "https://api.imgbb.com/1/upload",
        data=form,
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {}
        if resp.status >= 400 or not isinstance(body, dict) or not body.get("success"):
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                error = error.get("message")
            raise RuntimeError(
                f"ImgBB upload failed: {error or f'HTTP {resp.status}'}"
            )
        data = body.get("data") or {}
        url = str(data.get("display_url") or data.get("url") or "")
        if not url:
            raise RuntimeError("ImgBB returned no image URL.")
        return url


async def _story_public_image_url(
    session: aiohttp.ClientSession, image, label: str
) -> str:
    """Resolve an image entry to a public URL (data URLs go up to ImgBB)."""
    if isinstance(image, str):
        image = {"url": image}
    if not isinstance(image, dict):
        return ""
    url = str(image.get("url") or image.get("data_url") or "").strip()
    if not url:
        return ""
    if url.startswith("data:"):
        return await _imgbb_upload_base64(session, url, label)
    return url


async def _create_gist(gist_data: dict) -> dict:
    """POST a new gist to GitHub and return the API JSON."""
    github_token = str(CONFIG.get("GITHUB_TOKEN", "") or "").strip()
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Vivid-Story-Creator",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.github.com/gists",
            json=gist_data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status >= 400:
                detail = (await resp.text()).strip()
                raise RuntimeError(f"GitHub API error {resp.status}: {detail[:300]}")
            return await resp.json()


async def _download_bytes(session: aiohttp.ClientSession, url: str) -> bytes:
    """Download one image (ImgBB or GitHub raw) as bytes."""
    async with session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}")
        return await resp.read()


async def api_story_save(request: Request) -> Response:
    """Publish the Story Creator's story as a new public GitHub gist."""
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": f"Invalid JSON: {exc}"}, status=400)

    name = str(payload.get("name") or "").strip()
    if not name:
        return web.json_response(
            {"error": "A story name is required."}, status=400
        )

    github_token = str(CONFIG.get("GITHUB_TOKEN", "") or "").strip()
    if not github_token or github_token == "your-github-token":
        return web.json_response({"error": "GitHub token not configured"}, status=400)

    characters = payload.get("characters") or []
    elements = payload.get("story_elements")
    if elements is None:
        elements = payload.get("storyElements")
    elements = elements or []
    if not isinstance(characters, list) or not isinstance(elements, list):
        return web.json_response(
            {"error": "characters and story_elements must be lists."}, status=400
        )
    characters = [entry for entry in characters if isinstance(entry, dict)]
    elements = [entry for entry in elements if isinstance(entry, dict)]

    story_slug = _story_file_slug(name)
    description = _story_description_text(payload.get("description"))

    # Upload any new (data URL) images first: a failed upload must not leave a
    # half-published gist behind.
    async with aiohttp.ClientSession() as session:
        try:
            story_image = await _story_public_image_url(
                session, payload.get("image"), f"{story_slug}_thumb"
            )
            character_images = [
                await _story_public_image_url(
                    session,
                    entry.get("image"),
                    f"character_{_story_file_slug(entry.get('name'))}",
                )
                for entry in characters
            ]
            element_images = [
                await _story_public_image_url(
                    session,
                    entry.get("image"),
                    f"storyelement_{_story_file_slug(entry.get('name'))}",
                )
                for entry in elements
            ]
        except Exception as exc:
            return web.json_response(
                {"ok": False, "error": f"Image upload failed: {exc}"}, status=502
            )

    files: dict[str, str] = {}
    used: set[str] = set()

    tag_stem = _story_unique_stem(story_slug, used)
    files[f"{STORY_TAG_PREFIX}{tag_stem}.md"] = (
        f"# {name}\n\n{description}\n" if description else f"# {name}\n"
    )
    if story_image:
        files["thumb.md"] = _story_thumb_markdown(story_image)

    for entry, image_url in zip(characters, character_images):
        display = str(entry.get("name") or "").strip()
        text = _story_description_text(entry.get("description"))
        if not display or not text:
            continue
        stem = _story_unique_stem(_story_file_slug(display), used)
        files[f"character_{stem}.txt"] = text + "\n"
        if image_url:
            files[f"{stem}_thumb.md"] = _story_thumb_markdown(image_url)

    for entry, image_url in zip(elements, element_images):
        display = str(entry.get("name") or "").strip()
        text = _story_description_text(entry.get("description"))
        if not display or not text:
            continue
        stem = _story_unique_stem(_story_file_slug(display), used)
        files[f"storyelement_{stem}.txt"] = text + "\n"
        if image_url:
            files[f"{stem}_thumb.md"] = _story_thumb_markdown(image_url)

    gist_data = {
        "description": f"Character.XI story: {name}",
        "public": bool(payload.get("public", True)),
        "files": {fname: {"content": content} for fname, content in files.items()},
    }
    try:
        result = await _create_gist(gist_data)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)

    return web.json_response({
        "ok": True,
        "gist_id": result.get("id", ""),
        "html_url": result.get("html_url", ""),
        "files": sorted(files),
    })


async def _read_story_gist(
    session: aiohttp.ClientSession, files: dict
) -> dict | None:
    """Parse a GOCHATAPP story gist into the Story Creator's story shape."""
    tag_name = next(
        (name for name in files if STORY_TAG_PREFIX in Path(name).name), None
    )
    if not tag_name:
        return None

    tag_text = await _read_gist_file(session, files[tag_name]) or ""
    story_slug = Path(tag_name).stem.replace(STORY_TAG_PREFIX, "")

    story_name = story_slug
    description = tag_text.strip()
    if description.startswith("# "):
        first_line, _, rest = description.partition("\n")
        story_name = first_line[2:].strip() or story_slug
        description = rest.strip()

    story = {
        "name": story_name,
        "description": description,
        "image": {"url": "", "meta": ""},
        "characters": [],
        "story_elements": [],
    }

    thumb_cache: dict[str, str] = {}

    async def _thumb_url(slug: str) -> str:
        for candidate in (
            f"{slug}_thumb.md",
            f"character_{slug}_thumb.md",
            f"storyelement_{slug}_thumb.md",
        ):
            info = files.get(candidate)
            if info is None:
                continue
            if candidate not in thumb_cache:
                text = await _read_gist_file(session, info) or ""
                thumb_cache[candidate] = _story_first_image_url(text)
            return thumb_cache[candidate]
        return ""

    if "thumb.md" in files:
        text = await _read_gist_file(session, files["thumb.md"]) or ""
        story["image"]["url"] = _story_first_image_url(text)

    for name, info in files.items():
        base = Path(name).name
        stem = Path(base).stem
        if base.startswith("character_") and base.endswith(".txt"):
            slug = stem[len("character_"):]
            text = await _read_gist_file(session, info) or ""
            story["characters"].append({
                "name": slug.replace("_", " "),
                "description": text.strip(),
                "image": {"url": await _thumb_url(slug), "meta": ""},
            })
        elif base.startswith("storyelement_") and base.endswith(".txt"):
            slug = stem[len("storyelement_"):]
            text = await _read_gist_file(session, info) or ""
            story["story_elements"].append({
                "name": slug.replace("_", " "),
                "description": text.strip(),
                "image": {"url": await _thumb_url(slug), "meta": ""},
            })

    return story


async def api_story_gist(request: Request) -> Response:
    """Read a story gist back into the Story Creator."""
    gist_id = _parse_gist_id(
        request.query.get("gist_id") or request.query.get("id")
    )
    if not gist_id:
        return web.json_response(
            {"error": "A valid gist_id is required."}, status=400
        )

    try:
        async with aiohttp.ClientSession() as session:
            gist = await _fetch_gist(session, gist_id)
            files = gist.get("files") or {}
            story = await _read_story_gist(session, files)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)

    if story is None:
        return web.json_response(
            {
                "ok": False,
                "error": "That gist does not contain a "
                f"{STORY_TAG_PREFIX}* story file.",
            },
            status=400,
        )

    return web.json_response({
        "ok": True,
        "gist_id": gist_id,
        "html_url": f"https://gist.github.com/{gist_id}",
        "story": story,
    })


async def _install_story_gist(gist_id: str) -> dict:
    """Download a story gist into chats_stories/stories/<name>!<gist_id>/."""
    async with aiohttp.ClientSession() as session:
        gist = await _fetch_gist(session, gist_id)
        files = gist.get("files") or {}

        tag_name = next(
            (name for name in files if STORY_TAG_PREFIX in Path(name).name), None
        )
        if not tag_name:
            raise RuntimeError(
                f"This gist does not contain a {STORY_TAG_PREFIX}* story file."
            )

        tag_text = await _read_gist_file(session, files[tag_name]) or ""
        story_name = _sanitize_role_name(
            Path(tag_name).stem.replace(STORY_TAG_PREFIX, "")
        )
        full_name = f"{story_name}!{gist_id}"
        story_dir = STORIES_DIR / full_name

        (story_dir / "characters").mkdir(parents=True, exist_ok=True)
        (story_dir / "storyelements").mkdir(parents=True, exist_ok=True)
        (story_dir / "story_context").mkdir(parents=True, exist_ok=True)
        (story_dir / "backups").mkdir(parents=True, exist_ok=True)

        context_path = story_dir / "story_context" / "context.txt"
        context_path.write_text(tag_text, encoding="utf-8")
        saved = [f"stories/{full_name}/story_context/context.txt"]

        async def _save_image(url: str, dest: Path) -> None:
            if not url:
                return
            try:
                dest.write_bytes(await _download_bytes(session, url))
            except Exception as exc:
                print(f"Failed to download story image {url}: {exc}")
                return
            saved.append(f"stories/{full_name}/{dest.relative_to(story_dir).as_posix()}")

        for name, info in files.items():
            base = Path(name).name
            stem = Path(base).stem
            if base.startswith("character_") and base.endswith(".txt"):
                slug = stem[len("character_"):]
                text = await _read_gist_file(session, info) or ""
                dest = story_dir / "characters" / f"{slug}.txt"
                dest.write_text(text, encoding="utf-8")
                saved.append(f"stories/{full_name}/characters/{dest.name}")
                thumb = files.get(f"{slug}_thumb.md")
                if thumb is None:
                    thumb = files.get(f"character_{slug}_thumb.md")
                if thumb is not None:
                    await _save_image(
                        _story_first_image_url(
                            await _read_gist_file(session, thumb) or ""
                        ),
                        story_dir / "characters" / f"{slug}.jpg",
                    )
            elif base.startswith("storyelement_") and base.endswith(".txt"):
                slug = stem[len("storyelement_"):]
                text = await _read_gist_file(session, info) or ""
                dest = story_dir / "storyelements" / f"{slug}.txt"
                dest.write_text(text, encoding="utf-8")
                saved.append(f"stories/{full_name}/storyelements/{dest.name}")
                thumb = files.get(f"{slug}_thumb.md")
                if thumb is not None:
                    await _save_image(
                        _story_first_image_url(
                            await _read_gist_file(session, thumb) or ""
                        ),
                        story_dir / "storyelements" / f"{slug}.jpg",
                    )
            elif base.lower() == "thumb.md":
                await _save_image(
                    _story_first_image_url(
                        await _read_gist_file(session, info) or ""
                    ),
                    story_dir / "thumb.jpg",
                )

    return {"name": full_name, "story": story_name, "files": saved}


async def api_story_install(request: Request) -> Response:
    """Install a published story gist into chats_stories/stories/."""
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": f"Invalid JSON: {exc}"}, status=400)

    gist_id = _parse_gist_id(payload.get("gist_id") or payload.get("id"))
    if not gist_id:
        return web.json_response(
            {"error": "A valid gist_id is required."}, status=400
        )

    try:
        result = await _install_story_gist(gist_id)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)

    return web.json_response({"ok": True, **result})


async def api_story_imgbb(request: Request) -> Response:
    """Upload one Story Creator image to ImgBB and return its public URL."""
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": f"Invalid JSON: {exc}"}, status=400)

    image_base64 = payload.get("image_base64") or payload.get("image") or ""
    if not image_base64:
        return web.json_response({"error": "image_base64 required"}, status=400)

    try:
        async with aiohttp.ClientSession() as session:
            url = await _imgbb_upload_base64(
                session, image_base64, str(payload.get("name") or "story")
            )
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)

    return web.json_response({"ok": True, "url": url, "link": url})


async def api_ai_enhance(request: Request) -> Response:
    """Run one server-side completion for the Story Creator's AI Enhance."""
    try:
        payload = await request.json()
    except Exception as exc:
        return web.json_response({"error": f"Invalid JSON: {exc}"}, status=400)

    prompt = str(payload.get("prompt") or payload.get("user") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt required"}, status=400)
    system = str(payload.get("system") or "").strip()

    requested = str(payload.get("provider") or "").strip()
    provider = providers.get(requested) if requested else None
    provider_name = requested if provider is not None else ""
    if provider is None:
        for candidate in AI_PROVIDERS:
            if candidate in providers:
                provider_name, provider = candidate, providers[candidate]
                break
    if provider is None and providers:
        provider_name, provider = next(iter(providers.items()))
    if provider is None:
        return web.json_response(
            {"error": "No LLM provider is configured. Add one on the Setup page."},
            status=400,
        )

    model = str(payload.get("model") or "").strip()
    if not model:
        for cfg in providers_config:
            if cfg.get("name") == provider_name:
                model = str(cfg.get("model") or "").strip()
                break
    if not model:
        for bot in BOT_INSTANCES.values():
            if getattr(bot, "current_provider", "") == provider_name:
                model = str(getattr(bot, "current_model", "") or "").strip()
                if model:
                    break
    if not model:
        return web.json_response(
            {"error": f"No model selected for provider '{provider_name}'."},
            status=400,
        )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        text = await provider.chat(model, messages, {"temperature": 0.8})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=502)

    text = str(text or "").strip()
    if not text:
        return web.json_response(
            {"ok": False, "error": "The model returned an empty reply."}, status=502
        )
    return web.json_response({
        "ok": True,
        "text": text,
        "provider": provider_name,
        "model": model,
    })


async def role_maker_page(request: Request) -> Response:
    """Serve the Role Maker page."""
    return await _serve_page("role_maker.html")


async def story_creator_page(request: Request) -> Response:
    """Serve the Story Creator page (builds and publishes story gists)."""
    return await _serve_page("story_creator.html")


async def persona_maker_page(request: Request) -> Response:
    """Serve the Persona Maker page."""
    return await _serve_page("persona_maker.html")


async def commands_page(request: Request) -> Response:
    """Serve the Commands Reference page."""
    return await _serve_page("commands.html")


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
    # Some pages (role_maker.html, persona_maker.html) link back with a
    # relative "index.html"; serve the dashboard there too.
    app.router.add_get("/index.html", index, name="index_html")
    app.router.add_get("/chat", chat_page, name="chat")
    app.router.add_get("/setup", setup_page, name="setup")
    app.router.add_get("/browse", browser_page, name="browse")
    app.router.add_get("/browser", browser_page, name="browser")
    app.router.add_get("/browser_static", browser_static_page, name="browser_static")
    app.router.add_get("/role_maker", role_maker_page, name="role_maker")
    app.router.add_get("/role", role_maker_page, name="role")
    app.router.add_get("/persona_maker", persona_maker_page, name="persona_maker")
    app.router.add_get("/persona", persona_maker_page, name="persona")
    app.router.add_get("/story_creator", story_creator_page, name="story_creator")
    # The dashboard's Story Maker cards link to /story and /story2.
    app.router.add_get("/story", story_creator_page, name="story")
    app.router.add_get("/story2", story_creator_page, name="story2")
    app.router.add_get("/commands", commands_page, name="commands")

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

    # Persona Maker API
    app.router.add_get("/api/personas", api_personas)
    app.router.add_post("/api/personas", api_personas)
    app.router.add_get("/api/persona_templates", api_persona_templates)

    # Community store API (backs /browse)
    app.router.add_get("/api/store", api_store)
    app.router.add_get("/api/installed", api_installed)
    app.router.add_post("/api/store/install", api_store_install)
    app.router.add_post("/api/store/uninstall", api_store_uninstall)

    # Story Creator API (backs /story)
    app.router.add_post("/api/story/save", api_story_save)
    app.router.add_get("/api/story/gist", api_story_gist)
    app.router.add_post("/api/story/install", api_story_install)
    app.router.add_post("/api/story/imgbb", api_story_imgbb)
    app.router.add_post("/api/ai/enhance", api_ai_enhance)

    app.router.add_get("/api/config", api_config_get)
    app.router.add_post("/api/config", api_config_post)
    app.router.add_post("/api/config/reload", api_config_reload)
    app.router.add_post("/api/test/telegram", api_test_telegram)
    app.router.add_post("/api/test/provider", api_test_provider)

    return app