"""
Smoke test for the /setup page styling.

Only issues GET requests (no /api/config POST), so it never starts bots or
touches the real config. It verifies the page still serves, still references
/static/setup.js, and now uses the Role Maker design tokens (indigo accent,
#0b0f17 input background, indigo focus ring) instead of the old blue palette.

Run:  python testing/test_setup_page.py
"""

import asyncio
import sys
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from system.web_server import create_web_app
from aiohttp.test_utils import TestClient, TestServer


PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


async def run():
    app = create_web_app()
    async with TestClient(TestServer(app)) as client:
        print("\nGET /setup")
        resp = await client.get("/setup")
        check("/setup 200", resp.status == 200, f"got {resp.status}")
        check("/setup is html", "text/html" in resp.headers.get("Content-Type", ""))
        body = await resp.text()

        check("references /static/setup.js", "/static/setup.js" in body)

        # Role Maker design tokens now drive the page.
        check("indigo primary button", "#4f46e5" in body)
        check("indigo hover/accent", "#6366f1" in body)
        check("dark input background", "#0b1017" in body)
        check("page background", "#0b0f17" in body)
        check("header icon", "fa-sliders" in body)

        # Old neutral/blue palette must be gone.
        check("old blue button removed", "#4f7cff" not in body)
        check("old page bg removed", "#111418" not in body)

        # setup.js is still served from the static mount.
        resp = await client.get("/static/setup.js")
        check("/static/setup.js 200", resp.status == 200, f"got {resp.status}")

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
