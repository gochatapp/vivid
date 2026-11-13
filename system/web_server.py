"""
Web server with aiohttp - serves dashboard and web chat UI.
All web-specific code lives here, NOT in vivid.py.
"""

import json
import time
from pathlib import Path
from aiohttp import web
from aiohttp.web import Request, Response

from .config import (
    BASE_DIR,
    AI_PROVIDERS,
    providers,
    provider_display_info,
    TELEGRAM_USER_ID,
    BOT_INSTANCES,
)
from .bot_instance import BotInstance


# Path to web static files
WEB_DIR = BASE_DIR / "web"


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
                self.effective_user = type('obj', (object,), {'id': TELEGRAM_USER_ID})()

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
                self.effective_user = type('obj', (object,), {'id': TELEGRAM_USER_ID})()

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


def create_web_app() -> web.Application:
    """Create and configure the aiohttp web application."""
    app = web.Application()

    if WEB_DIR.exists():
        app.router.add_static("/static/", path=WEB_DIR, name="static")

    app.router.add_get("/", index, name="index")
    app.router.add_get("/chat", chat_page, name="chat")

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

    return app