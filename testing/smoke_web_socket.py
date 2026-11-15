"""
Real-socket smoke test for the Vivid web server.

Boots create_web_app() on 127.0.0.1:8099 (no Telegram polling) and checks that
the dashboard, setup editor, browser pages and static assets are served.

Run:  python testing/smoke_web_socket.py
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiohttp import web, ClientSession
from system.web_server import create_web_app

HOST, PORT = "127.0.0.1", 8099


async def run():
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    print(f"serving on http://{HOST}:{PORT}")

    failures = 0
    checks = [
        ("/", "text/html"),
        ("/setup", "text/html"),
        ("/browse", "text/html"),
        ("/browser_static", "text/html"),
        ("/api/config", "application/json"),
        ("/api/models?type=llamacpp&base=http://127.0.0.1:1/v1", "application/json"),
        ("/static/styles.css", "text/css"),
        ("/static/setup.js", "javascript"),
        ("/static/app.js", "javascript"),
    ]
    try:
        async with ClientSession() as client:
            for path, expected in checks:
                async with client.get(f"http://{HOST}:{PORT}{path}") as resp:
                    ctype = resp.headers.get("Content-Type", "")
                    ok = resp.status == 200 and expected in ctype
                    print(f"  [{'PASS' if ok else 'FAIL'}] {path} -> "
                          f"{resp.status} {ctype}")
                    if not ok:
                        failures += 1
                        await resp.text()
    finally:
        await runner.cleanup()

    print(f"\n{'ALL PASSED' if failures == 0 else str(failures) + ' FAILED'}")
    return failures == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
