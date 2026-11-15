"""
Regression test: /api/roles/save must honour ``image_ext`` so a Role Maker image
is written beside the role with its real format. Before this, every portrait was
forced to <name>.jpg even when the bytes were an animated WebP.

Runs the real endpoint against a temporary roles directory, so nothing in the
project is touched.

Run:  python testing/test_roles_save_ext.py
"""

import asyncio
import base64
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import system.web_server as ws  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

PASS = 0
FAIL = 0

PAYLOAD = b"RIFF0000WEBPVP8XANMF"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


async def save_role(client, name, image_ext=None):
    """POSTs a role with the given image_ext and returns (response, json)."""
    body = {
        "name": name,
        "text": "role body",
        "image_png": base64.b64encode(PAYLOAD).decode("ascii"),
        "overwrite": True,
    }
    if image_ext is not None:
        body["image_ext"] = image_ext
    resp = await client.post("/api/roles/save", json=body)
    try:
        data = await resp.json()
    except Exception:
        data = {}
    return resp, data


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        roles_dir = Path(tmp)
        with mock.patch.object(ws, "CHAT_ROLES_DIR", roles_dir):
            app = ws.create_web_app()
            async with TestClient(TestServer(app)) as client:
                print("\nimage_ext=.webp (animated WebP)")
                resp, data = await save_role(client, "Amy_Wong", ".webp")
                check("status 200", resp.status == 200, f"got {resp.status}")
                check(".webp file written",
                      (roles_dir / "Amy_Wong.webp").read_bytes() == PAYLOAD
                      if (roles_dir / "Amy_Wong.webp").exists() else False)
                check("no stray .jpg",
                      not (roles_dir / "Amy_Wong.jpg").exists())
                files = (data.get("data") or {}).get("files") or []
                check("response lists the .webp",
                      any(f.endswith("Amy_Wong.webp") for f in files), files)

                print("\nno image_ext (static PNG resized to .jpg)")
                resp, data = await save_role(client, "Bob", None)
                check("status 200", resp.status == 200, f"got {resp.status}")
                check("defaults to .jpg",
                      (roles_dir / "Bob.jpg").read_bytes() == PAYLOAD
                      if (roles_dir / "Bob.jpg").exists() else False)

                print("\nunknown extension falls back to .jpg")
                resp, data = await save_role(client, "Carl", ".exe")
                check("status 200", resp.status == 200, f"got {resp.status}")
                check("writes .jpg", (roles_dir / "Carl.jpg").exists())
                check("does not write .exe", not (roles_dir / "Carl.exe").exists())

                print("\nuppercase extension is normalised")
                resp, data = await save_role(client, "Dana", ".WEBP")
                check("status 200", resp.status == 200, f"got {resp.status}")
                check("writes lowercase .webp", (roles_dir / "Dana.webp").exists())

                print("\n.mp4 extension is allowed")
                resp, data = await save_role(client, "Eve", ".mp4")
                check("status 200", resp.status == 200, f"got {resp.status}")
                check("writes .mp4", (roles_dir / "Eve.mp4").exists())

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
