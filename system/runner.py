"""
Main runner - orchestrates Telegram bots and web server.
This replaces the main() function in vivid.py.
"""

import os
import sys
import asyncio
from typing import Dict, List, Optional

from .config import (
    CONFIG,
    SETTINGS_DIR,
    initialize_providers,
)
from .tracker import BotUsageTracker
from .bot_instance import BotInstance, BOT_INSTANCES, BOT_INSTANCES_BY_ID, BOT_INSTANCES_BY_TOKEN
from .handlers import setup_handlers
from .web_server import create_web_app
from telegram.ext import Application

WEB_PORT = int(os.environ.get("VIVID_WEB_PORT", "8080"))
SETUP_URL = f"http://localhost:{WEB_PORT}/setup"

# Shared by every bot; assigned once the event loop owns the bots.
_active_tracker: Optional[BotUsageTracker] = None


def _configured_tokens() -> Dict[str, str]:
    """Return the usable {name: token} map from the live CONFIG.

    Reads ``CONFIG`` on every call so changes saved from /setup are picked up,
    and ignores empty / placeholder tokens ("your-bot-token-here").
    """
    tokens = CONFIG.get("TELEGRAM_TOKENS") or {}
    if not tokens:
        single = CONFIG.get("TELEGRAM_TOKEN")
        if single:
            tokens = {"bot": single}
    return {
        name: token
        for name, token in tokens.items()
        if token and not str(token).startswith("your-")
    }


def build_bot(name: str, token: str) -> BotInstance:
    """Create a BotInstance + Telegram Application and register it globally."""
    bot = BotInstance(name=name, token=token)
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
    BOT_INSTANCES[name] = bot
    BOT_INSTANCES_BY_TOKEN[token] = bot
    return bot


async def start_bot(bot: BotInstance, usage_tracker: Optional[BotUsageTracker] = None) -> None:
    """Initialize a bot and start polling for updates."""
    if usage_tracker is not None:
        bot.init_state(usage_tracker)
    await bot.app.initialize()
    BOT_INSTANCES_BY_ID[bot.app.bot.id] = bot
    await bot.app.start()
    await bot.app.updater.start_polling(drop_pending_updates=True)
    print(f"--- Bot Active: {bot.name} (Role: {bot.current_role}, Mode: {bot.current_mode}) ---")


async def stop_bot(bot: BotInstance) -> None:
    """Stop a running bot and remove it from the global registries."""
    try:
        if bot.app:
            if bot.app.updater and bot.app.updater.running:
                await bot.app.updater.stop()
            if bot.app.running:
                await bot.app.stop()
            await bot.app.shutdown()
    except Exception as e:
        print(f"Error stopping bot {bot.name}: {e}")

    BOT_INSTANCES.pop(bot.name, None)
    BOT_INSTANCES_BY_TOKEN.pop(bot.token, None)
    for bot_id, known in list(BOT_INSTANCES_BY_ID.items()):
        if known is bot:
            BOT_INSTANCES_BY_ID.pop(bot_id, None)
    print(f"--- Bot Stopped: {bot.name} ---")


async def sync_bots_with_config() -> Dict[str, List[str]]:
    """Start bots added in config, stop removed/changed ones.

    Called after /api/config saves, so a first-run install can go from an empty
    config to a live bot without restarting the process.
    """
    global _active_tracker
    if _active_tracker is None:
        _active_tracker = BotUsageTracker(SETTINGS_DIR / "bot_assignments.json")
        _active_tracker.load()

    desired = _configured_tokens()
    result: Dict[str, List[str]] = {"started": [], "stopped": [], "errors": []}

    # Stop bots that were removed or whose token changed.
    for name, bot in list(BOT_INSTANCES.items()):
        if name not in desired or desired[name] != bot.token:
            await stop_bot(bot)
            result["stopped"].append(name)

    # Start new bots (including a changed token under the same name).
    for name, token in desired.items():
        existing = BOT_INSTANCES.get(name)
        if existing and existing.token == token:
            continue

        bot = None
        try:
            bot = build_bot(name, token)
            await asyncio.wait_for(start_bot(bot, _active_tracker), timeout=30)
            result["started"].append(name)
        except Exception as e:
            if bot is not None:
                await stop_bot(bot)
            result["errors"].append(f"{name}: {e}")
            print(f"❌ Failed to start bot '{name}': {e}")

    return result


async def run_all_bots(bot_instances: List[BotInstance], web_app=None):
    """Start the web server first, then start polling for every configured bot."""
    global _active_tracker

    # Initialize providers first
    initialize_providers()

    # Load tracker
    _active_tracker = BotUsageTracker(SETTINGS_DIR / "bot_assignments.json")
    _active_tracker.load()

    # Start the web server first: on a fresh install (no bots configured yet)
    # the setup page is exactly what the user needs to reach.
    web_runner = None
    if web_app:
        from aiohttp import web
        web_runner = web.AppRunner(web_app)
        await web_runner.setup()
        site = web.TCPSite(web_runner, "0.0.0.0", WEB_PORT)
        await site.start()
        print(f"🌐 Web server started on http://0.0.0.0:{WEB_PORT}")
        print(f"🛠️  Setup / config editor: {SETUP_URL}")

    for bot in bot_instances:
        try:
            await start_bot(bot, _active_tracker)
        except Exception as e:
            print(f"❌ Failed to start bot '{bot.name}': {e}")
            await stop_bot(bot)

    if bot_instances:
        print(f"✅ {len(bot_instances)} bot(s) configured and listening!")
    else:
        print("⚠️  No Telegram bots configured yet.")
        print(f"👉 Open {SETUP_URL} to add your bot token and user id.")
        print("   Bots start automatically as soon as you save the config.")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass
    finally:
        print("\nStopping all bots...")
        for bot in list(BOT_INSTANCES.values()):
            await stop_bot(bot)

        if web_runner:
            await web_runner.cleanup()
            print("🌐 Web server stopped")

        print("\nBots stopped.")


def main():
    """Entry point: always start the web server, plus any configured bots."""
    initialize_providers()

    # If a CLI arg is provided, only run that specific bot (e.g. python vivid.py chat1)
    desired = _configured_tokens()
    if len(sys.argv) > 1 and sys.argv[1] in desired:
        desired = {sys.argv[1]: desired[sys.argv[1]]}

    bot_instances = []
    for name, token in desired.items():
        try:
            bot_instances.append(build_bot(name, token))
        except Exception as e:
            # A malformed token must not stop the web server / setup page.
            print(f"❌ Invalid token for bot '{name}': {e}")

    # The web app is always created: /setup is how a fresh install gets its
    # first bot token and user id.
    web_app = create_web_app()

    try:
        asyncio.run(run_all_bots(bot_instances, web_app))
    except (KeyboardInterrupt, SystemExit):
        print("\nBots stopped.")
