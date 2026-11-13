"""
Main runner - orchestrates Telegram bots and web server.
This replaces the main() function in vivid.py.
"""

import sys
import asyncio
from typing import List

from .config import (
    CONFIG,
    SETTINGS_DIR,
    TELEGRAM_TOKENS,
    AI_PROVIDERS,
    providers,
    provider_display_info,
    initialize_providers,
)
from .tracker import BotUsageTracker, tracker
from .bot_instance import BotInstance, BOT_INSTANCES, BOT_INSTANCES_BY_ID, BOT_INSTANCES_BY_TOKEN
from .handlers import setup_handlers
from .web_server import create_web_app
from telegram import Update
from telegram.ext import Application, ContextTypes


async def run_all_bots(bot_instances: List[BotInstance], web_app=None):
    """Initializes and starts polling for all bots concurrently, plus web server."""
    
    # Initialize providers first
    initialize_providers()
    
    # Load tracker
    tracker = BotUsageTracker(SETTINGS_DIR / "bot_assignments.json")
    tracker.load()

    for bot in bot_instances:
        bot.init_state(tracker)
        await bot.app.initialize()
        BOT_INSTANCES_BY_ID[bot.app.bot.id] = bot
        await bot.app.start()
        await bot.app.updater.start_polling(drop_pending_updates=True)
        print(f"--- Bot Active: {bot.name} (Role: {bot.current_role}, Mode: {bot.current_mode}) ---")

    print(f"✅ All {len(bot_instances)} bot(s) started and listening!")

    # Start web server if provided
    web_runner = None
    if web_app:
        from aiohttp import web
        web_runner = web.AppRunner(web_app)
        await web_runner.setup()
        site = web.TCPSite(web_runner, '0.0.0.0', 8080)
        await site.start()
        print(f"🌐 Web server started on http://0.0.0.0:8080")

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
        
        if web_runner:
            await web_runner.cleanup()
            print("🌐 Web server stopped")

        print("\nBots stopped.")


def main():
    global BOT_INSTANCES, BOT_INSTANCES_BY_TOKEN, BOT_INSTANCES_BY_ID

    # Initialize providers from config
    from .config import initialize_providers
    initialize_providers()

    # Load tracker
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
        bot_instances.append(bot)

    if not bot_instances:
        print("❌ Error: No valid bots initialized.")
        return

    # Create web app
    web_app = create_web_app()

    # Run everything
    try:
        asyncio.run(run_all_bots(bot_instances, web_app))
    except (KeyboardInterrupt, SystemExit):
        print("\nBots stopped.")