"""
Regression test: the process must start even when no Telegram bots are
configured, so a fresh install can be set up at /setup.

Runs the real run_all_bots() with an empty bot list on a spare port
(VIVID_WEB_PORT) and checks that the setup page + config API answer.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before importing runner: it reads the port at import time.
os.environ["VIVID_WEB_PORT"] = "8099"

import aiohttp

from system import config as cfg
from system.runner import run_all_bots, _configured_tokens
from system.web_server import create_web_app

PORT = int(os.environ["VIVID_WEB_PORT"])
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


async def run():
    print("_configured_tokens() with an empty bot list")
    original = cfg.CONFIG.get("TELEGRAM_TOKENS")
    cfg.CONFIG["TELEGRAM_TOKENS"] = {}
    try:
        got = _configured_tokens()
        check("empty config yields no bots", got == {}, f"got {got}")
    finally:
        cfg.CONFIG["TELEGRAM_TOKENS"] = original

    print("\nboot with zero bots (web server must still come up)")
    app = create_web_app()
    task = asyncio.create_task(run_all_bots([], app))
    await asyncio.sleep(1.5)

    try:
        async with aiohttp.ClientSession() as session:
            for path, expected_type in [
                ("/", "text/html"),
                ("/setup", "text/html"),
                ("/api/config", "application/json"),
            ]:
                url = f"http://127.0.0.1:{PORT}{path}"
                async with session.get(url) as resp:
                    body = await resp.text()
                    check(f"GET {path} -> 200", resp.status == 200,
                          f"got {resp.status} ({body[:120]})")
                    ctype = resp.headers.get("Content-Type", "")
                    check(f"GET {path} content-type", expected_type in ctype, ctype)
                    check(f"GET {path} non-empty body", len(body) > 0)
    finally:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
